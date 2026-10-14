"""Packaged viewer behavior in Chromium; host APIs are simulated, not live ChatGPT."""
import base64
import io
import json
import pytest
from PIL import Image

@pytest.mark.parametrize('mode', ['initial', 'notification', 'wrapped', 'globals', 'missing', 'invalid', 'gif', 'gif_url', 'bad_url', 'downloads'])
def test_result_envelopes(mode, page, html, preview, buf):
    requests = []
    page.on('request', lambda r: requests.append(r.url))
    if mode == 'initial':
        page.add_init_script('window.openai=' + json.dumps({'toolResponseMetadata': {'mcp_tool_result': {'_meta': {'preview': preview}}}}))
    page.goto('about:blank')
    page.set_content(html)
    result = {'_meta': {'preview': preview}, 'structuredContent': {'complete': True}}
    if mode in ('gif', 'gif_url'):
        gif = io.BytesIO()
        Image.new('RGB', (80, 60), 'red').save(gif, format='GIF', save_all=True, append_images=[Image.new('RGB', (80, 60), 'blue')], duration=200, loop=0)
        result['_meta']['preview'] = {'mimeType': 'image/gif', 'data': base64.b64encode(gif.getvalue()).decode()}
    if mode == 'gif_url':
        page.route('https://render.example.org/preview/test', lambda route: route.fulfill(content_type='image/gif', body=gif.getvalue()))
        result['_meta']['preview'] = {'mimeType': 'image/gif', 'url': 'https://render.example.org/preview/test'}
    if mode == 'bad_url':
        result['_meta']['preview'] = {'mimeType': 'image/gif', 'url': 'javascript:alert(1)'}
    if mode == 'downloads':
        result['_meta']['downloads'] = [{'name': 'render.' + ext, 'url': 'https://render.example.org/download/' + ext} for ext in ('gif', 'mp4', 'png')] + [{'name': 'render.gif', 'url': 'javascript:alert(1)'}]
        page.evaluate('window.openai={openExternal:({href})=>window.clicked=href}')
    if mode in ('notification', 'missing', 'invalid', 'gif', 'gif_url', 'bad_url', 'downloads'):
        if mode == 'missing':
            result['_meta']['preview'] = None
        if mode == 'invalid':
            result['_meta']['preview'] = {'mimeType': 'image/png', 'data': 'https://evil.invalid/image.png'}
        page.evaluate("r => window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')", result)
    if mode in ('wrapped', 'globals'):
        data = {'call_tool_result': result} if mode == 'wrapped' else result['_meta']
        page.evaluate("r=>window.dispatchEvent(new CustomEvent('openai:set_globals',{detail:{globals:{toolResponseMetadata:r}}}))", data)
    if mode in ('missing', 'invalid', 'bad_url'):
        page.wait_for_function("document.getElementById('status').textContent.includes('unavailable')")
        assert page.locator('#preview').is_hidden()
    else:
        page.wait_for_function("!document.getElementById('preview').hidden && document.getElementById('preview').naturalWidth===80")
        assert page.locator('#status').is_hidden()
    assert requests == (['https://render.example.org/preview/test'] if mode == 'gif_url' else []), requests
    if mode == 'downloads':
        assert page.locator('#downloads a').count() == 3
        page.get_by_text('Download GIF', exact=True).click()
        assert page.evaluate('window.clicked') == 'https://render.example.org/download/gif'

def test_late_file_api(page, html, preview, buf):
    page.set_content(html)
    page.route('https://render.example.org/preview/late', lambda route: route.fulfill(content_type='image/png', body=buf.getvalue()))
    page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')", {'_meta': {'saveFiles': [{'name': 'render.png', 'jobId': 'late', 'url': 'https://render.example.org/preview/late'}]}})
    page.wait_for_function("document.getElementById('saved').textContent.includes('File saving is unavailable')")
    assert page.locator('#saved').is_visible()
    page.evaluate("window.openai={uploadFile:async()=>({fileId:'file-late'}),setWidgetState:s=>window.savedState=s}")
    page.locator('#save').click()
    page.wait_for_function("window.savedState?.privateContent?.savedFiles?.['render.png']?.fileId==='file-late'")
    assert page.locator('#save').is_visible()

@pytest.mark.parametrize('fail', [False, True])
def test_explicit_save_and_retry(fail, page, html, preview, buf, file_host):
    page.set_content(html)
    page.route('https://render.example.org/preview/save', lambda route: route.fulfill(content_type='image/png', body=buf.getvalue()))
    file_host(rejected=['render.png'] if fail else [])
    page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')", {'_meta': {'preview': preview, 'saveImage': {'jobId': 'test-job', 'url': 'https://render.example.org/preview/save'}}})
    assert page.evaluate('window.counts["render.png"] || 0') == 0
    page.locator('#save').click()
    if fail:
        page.wait_for_function("document.getElementById('saved').textContent.includes('not saved')")
        assert page.evaluate('window.savedState') is None
    else:
        page.wait_for_function("window.savedState?.privateContent?.savedFiles?.['render.png']?.fileId==='saved-render.png'")
        assert page.evaluate('window.uploaded.type') == 'image/png'
    assert page.evaluate('window.uploadOptions') == {'library': True}
    assert page.evaluate('window.counts["render.png"]') == 1
    page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')", {'_meta': {'saveImage': {'jobId': 'test-job', 'url': 'https://render.example.org/preview/save'}}})
    page.wait_for_timeout(100)
    assert page.evaluate('window.counts["render.png"]') == 1
    if fail:
        page.get_by_text('Retry saving files', exact=True).click()
        page.wait_for_function('window.counts["render.png"]===2')

def test_saved_image_restore(page, html, preview, buf):
    page.route('https://files.oaiusercontent.com/saved.png', lambda route: route.fulfill(content_type='image/png', body=buf.getvalue()))
    page.goto('about:blank')
    saved = {'fileId': 'file-saved', 'jobId': 'test-job'}
    state = {'privateContent': {'savedImage': saved, 'savedFiles': {'render.png': saved}}}
    page.evaluate("state=>window.openai={widgetState:state,getFileDownloadUrl:async({fileId})=>{window.restoredId=fileId;return {downloadUrl:'https://files.oaiusercontent.com/saved.png'}}}", state)
    page.set_content(html)
    page.wait_for_function("document.getElementById('preview').naturalWidth===80")
    assert page.evaluate('window.restoredId') == 'file-saved'

def test_partial_save_retries_only_failed_file(page, html, preview, buf, file_host):
    page.set_content(html)
    for ext, mime in [('png', 'image/png'), ('gif', 'image/gif'), ('mp4', 'video/mp4')]:
        page.route('https://render.example.org/preview/' + ext, lambda route, request, mime=mime: route.fulfill(content_type=mime, body=b'file-test-bytes'))
    file_host(rejected=['render.mp4'])
    files = [{'jobId': 'movie', 'name': 'render.' + ext, 'mimeType': mime, 'url': 'https://render.example.org/preview/' + ext} for ext, mime in [('png', 'image/png'), ('gif', 'image/gif'), ('mp4', 'video/mp4')]]
    page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')", {'_meta': {'saveFiles': files}})
    page.locator('#save').click()
    page.wait_for_function("document.getElementById('saved').textContent.includes('render.mp4 not saved')")
    assert set(page.evaluate('Object.keys(window.savedState.privateContent.savedFiles)')) == {'render.png', 'render.gif'}
    assert page.evaluate('window.savedState.imageIds') == ['saved-render.png']
    page.evaluate('window.rejected=[]')
    page.get_by_text('Retry saving files', exact=True).click()
    page.wait_for_function("window.savedState.privateContent.savedFiles['render.mp4']?.fileId")
    assert page.evaluate('window.counts') == {'render.png': 1, 'render.gif': 1, 'render.mp4': 2}

def test_state_commit_retry_reuses_uploaded_files(page, html, preview, buf, file_host):
    formats = {'render.png': 'image/png', 'render.gif': 'image/gif', 'render.mp4': 'video/mp4', 'render.blend': 'application/octet-stream', 'manifest.json': 'application/json', 'frames.zip': 'application/zip'}
    page.set_content(html)
    for name, mime in formats.items():
        page.route('https://render.example.org/download/' + name + '?delivery=ack', lambda route, request, mime=mime: route.fulfill(content_type=mime, body=buf.getvalue()))
    file_host(reject_state=True)
    files = [{'jobId': 'all', 'name': name, 'url': 'https://render.example.org/download/' + name + '?delivery=ack'} for name in formats]
    page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')", {'_meta': {'saveFiles': files}})
    page.locator('#save').click()
    page.wait_for_function("document.getElementById('saved').textContent.includes('not saved')")
    assert page.evaluate('window.savedState') is None
    assert 'saved in ChatGPT' not in page.locator('#saved').inner_text()
    page.evaluate('window.rejectState=false')
    page.get_by_text('Retry saving files', exact=True).click()
    page.wait_for_function("document.getElementById('save').textContent==='Files uploaded'")
    assert page.evaluate('window.uploads') == list(formats)
    state = page.evaluate('window.savedState')
    assert set(state['privateContent']['savedFiles']) == set(formats)

@pytest.mark.parametrize('late', [False, True])
def test_state_only_reopen(late, page, html, preview, buf):
    formats = ['render.png', 'render.gif', 'render.mp4', 'render.blend', 'manifest.json', 'frames.zip']
    state = {'privateContent': {'savedFiles': {name: {'fileId': 'saved-' + name, 'jobId': 'all'} for name in formats}}}
    requests = []
    page.on('request', lambda r: requests.append(r.url))
    page.route('https://files.oaiusercontent.com/restored', lambda route: route.fulfill(content_type='image/png', body=buf.getvalue()))
    page.goto('about:blank')
    page.evaluate("state=>window.openai={widgetState:state,\n     getFileDownloadUrl:async({fileId})=>{window.restoredId=fileId;return {downloadUrl:'https://files.oaiusercontent.com/restored'};},\n     openExternal:({href})=>window.opened=href}", None if late else state)
    page.set_content(html)
    if late:
        page.evaluate("state=>window.dispatchEvent(new CustomEvent('openai:set_globals',{detail:{globals:{widgetState:state}}}))", state)
    page.wait_for_function("document.getElementById('preview').naturalWidth===80")
    assert page.evaluate('window.restoredId') == 'saved-render.gif'
    assert page.locator('#downloads a').count() == 6
    page.get_by_text('Download ZIP', exact=True).click()
    page.wait_for_function("window.restoredId==='saved-frames.zip'")
    assert page.evaluate('window.opened') == 'https://files.oaiusercontent.com/restored'
    assert all((url.startswith('https://files.oaiusercontent.com/') for url in requests))

def test_sandbox_handshake_and_height(host, page, html, preview):
    frame = host(html, {'_meta': {'preview': preview}})
    frame.wait_for_function("!document.getElementById('preview').hidden && document.getElementById('preview').naturalWidth===80")
    page.wait_for_function('window.measuredHeight > 0 && window.measuredHeight < 500')

@pytest.mark.parametrize('reject', [False, True])
def test_model_image_delivery(host, page, html, preview, reject):
    frame = host(html, {'_meta': {'preview': preview}, 'structuredContent': {'job_id': 'cpu-test'}}, capabilities={'message': {'image': True}, 'updateModelContext': {'image': True}}, reject=reject, repeats=2)
    page.wait_for_function('window.calls.length===1')
    frame.wait_for_function("document.getElementById('handoff').textContent.includes('confirmed' )" if reject else "document.getElementById('handoff').textContent.includes('supplied as context')")
    calls = page.evaluate('window.calls')
    assert calls[0]['method'] == 'ui/update-model-context'
    assert calls[0]['params']['content'][1] == {'type': 'image', **preview}
    frame.locator('#share').click()
    page.wait_for_function('window.calls.length===2')
    frame.wait_for_function("document.getElementById('handoff').textContent.includes('unconfirmed')" if reject else "document.getElementById('share').textContent==='Image sent to ChatGPT'")
    calls = page.evaluate('window.calls')
    assert calls[1]['method'] == 'ui/message'
    assert calls[1]['params']['role'] == 'user'
    assert calls[1]['params']['content'][1] == {'type': 'image', **preview}
    assert frame.locator('#share').is_enabled() == reject
    page.wait_for_timeout(100)
    assert len(page.evaluate('window.calls')) == 2


@pytest.mark.parametrize('mode', ['background', 'message', 'control', 'blender'])
def test_diagnostic_modes_use_production_delivery(host, page, preview, mode):
    from support.image_handoff import viewer
    frame = host(viewer(mode), {'_meta': {'preview': preview}},
                 capabilities={'message': {'image': True}, 'updateModelContext': {'image': True}})
    frame.wait_for_function("document.getElementById('preview').naturalWidth===80")
    background = mode in ('background', 'blender')
    message = mode in ('message', 'blender')
    if background:
        page.wait_for_function('window.calls.length===1')
        assert page.evaluate('window.calls[0].method') == 'ui/update-model-context'
    assert frame.locator('#share').is_visible() == message
    if message:
        frame.locator('#share').click()
        page.wait_for_function(f'window.calls.length==={1 + int(background)}')
        call = page.evaluate('window.calls.at(-1)')
        assert call['method'] == 'ui/message'
        assert call['params']['content'][1] == {'type': 'image', **preview}
        if mode == 'message':
            assert 'five-character code' in call['params']['content'][0]['text']
    page.wait_for_timeout(100)
    assert len(page.evaluate('window.calls')) == int(background) + int(message)


@pytest.mark.parametrize('url', [
    'http://render.example.org/preview/file',
    'https://user:pass@render.example.org/preview/file',
    'https://render.example.org/download/file',
    'https://render.example.org/download/file?delivery=consume',
    'https://render.example.org/other/file',
])
def test_saving_rejects_unsafe_or_consuming_urls(page, html, preview, file_host, url):
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    page.set_content(html)
    file_host()
    page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')",
                  {'_meta': {'preview': preview, 'saveFiles': [{'jobId': 'test', 'name': 'render.png', 'url': url}]}})
    page.locator('#save').click()
    page.wait_for_function("document.getElementById('saved').textContent.includes('not saved')")
    assert requests == []
    assert page.evaluate('window.uploads') == []


@pytest.mark.parametrize('kind', ['invalid', 'oversize', 'gif'])
def test_model_delivery_rejects_unsupported_image_data(host, page, html, preview, kind):
    value = dict(preview)
    if kind == 'invalid':
        value['data'] = 'not an image'
    elif kind == 'oversize':
        value['data'] = 'A' * 2796205
    else:
        value['mimeType'] = 'image/gif'
    frame = host(html, {'_meta': {'preview': value}, 'structuredContent': {'complete': True}},
                 capabilities={'message': {'image': True}, 'updateModelContext': {'image': True}})
    assert frame.locator('#share').is_hidden()
    page.wait_for_timeout(100)
    assert page.evaluate('window.calls') == []

"""Library bytes and fresh-context reuse with a simulated host, never live acceptance."""
import hashlib
import io
import json

import pytest
from PIL import Image


FILE_ID = 'sediment://file_library_exact_id'
REUSE = {'_meta': {'library': {'mode': 'reuse'}},
         'structuredContent': {'original_images_available': False}}


@pytest.fixture
def library_result(preview):
    return {'structuredContent': {'job_id': 'library-trial'}, '_meta': {
        'preview': preview, 'saveFiles': [{'name': 'render.png', 'fileName': 'blender-library-trial.png',
            'jobId': 'library-trial', **preview}]}}


def test_library_upload_and_fresh_context_reuse(library_host, library_result, file_host, buf, preview):
    page, _ = library_host(result=library_result)
    page.wait_for_function("document.getElementById('preview').naturalWidth===80")
    assert page.evaluate('window.uploads') == []
    page.locator('#save').click()
    page.wait_for_function("document.getElementById('save').textContent==='Files uploaded'")
    assert file_host.files[FILE_ID] == buf.getvalue()
    assert page.evaluate('window.uploaded.name') == 'blender-library-trial.png'
    assert page.evaluate('window.uploadOptions') == {'library': True}
    assert page.evaluate('window.uploads') == ['blender-library-trial.png']
    assert 'not confirmed' in page.locator('#saved').inner_text()
    state = page.evaluate('window.savedState')
    assert state['imageIds'] == [FILE_ID]
    assert state['privateContent']['savedImage']['fileId'] == FILE_ID
    assert 'data' not in json.dumps(state)
    page.context.close()

    # The next context has no original server, cache, or inherited browser state.
    page, requests = library_host(result=REUSE, state=state)
    assert page.locator('#preview').is_hidden()
    assert page.locator('#save').is_hidden()
    assert page.evaluate('window.calls') == []
    assert page.evaluate('window.fileReads') == []
    page.locator('#library').click()
    page.wait_for_function("document.getElementById('library-status').textContent.includes('retrieved')")
    assert page.locator('#library-status').get_attribute('data-sha256') == hashlib.sha256(buf.getvalue()).hexdigest()
    page.wait_for_function('window.calls.length===1')
    assert page.evaluate('window.calls[0].params.content[1]') == {'type': 'image', **preview}
    page.locator('#share').click()
    page.wait_for_function('window.calls.length===2')
    assert page.evaluate('window.calls[1].method') == 'ui/message'
    assert page.evaluate('window.calls[1].params.content[1]') == {'type': 'image', **preview}
    assert page.evaluate('window.savedState.imageIds') == [FILE_ID]
    assert page.evaluate('window.fileReads') == [FILE_ID]
    assert all('render.example.org' not in url for url in requests)
    page.locator('#library').click()
    page.wait_for_function('window.fileReads.length===2 && !document.getElementById("library").disabled')
    assert page.evaluate('window.uploads') == []
    assert len(page.evaluate('window.calls')) == 2


@pytest.mark.parametrize('selected', [False, True])
def test_library_state_reopens_without_claiming_space_storage(selected, library_host, file_host, buf, preview):
    file_host.files[FILE_ID] = buf.getvalue()
    file = {'fileId': FILE_ID, 'jobId': 'library-trial'}
    state = {'privateContent': {'savedImage': file, 'savedFiles': {'render.png': file}}}
    if selected:
        state['privateContent']['libraryImage'] = {'fileId': FILE_ID, 'mimeType': 'image/png'}
    page, requests = library_host(state=state, selection=[])
    page.wait_for_function("document.getElementById('preview').naturalWidth===80")
    page.wait_for_function('window.calls.length===1')
    assert page.evaluate('window.calls[0].params.content[1]') == {'type': 'image', **preview}
    assert all('render.example.org' not in url for url in requests)
    assert 'does not confirm it appears in Space' in page.locator('#saved').inner_text()
    assert 'saving to Space is not confirmed' in page.evaluate('window.savedState.modelContent')
    reads = page.evaluate('window.fileReads.length')
    page.locator('#library').click()
    page.wait_for_function("document.getElementById('library-status').textContent==='No image selected.'")
    assert page.evaluate('window.fileReads.length') == reads


@pytest.mark.parametrize('failure', ['denied', 'malformed', 'wrong_mime', 'oversize', 'unsafe_url'])
def test_failed_library_retrieval_never_supplies_model_pixels(failure, library_host, file_host, buf):
    file_host.files[FILE_ID] = buf.getvalue()
    page, _ = library_host(result=REUSE, denied=failure == 'denied')
    if failure in ('malformed', 'wrong_mime', 'oversize'):
        headers = {'Access-Control-Allow-Origin': '*'}
        if failure == 'oversize':
            headers['Content-Length'] = str(64 * 1024 * 1024 + 1)
        page.route('https://files.oaiusercontent.com/**', lambda route: route.fulfill(
            content_type='text/plain' if failure == 'wrong_mime' else 'image/png',
            body=b'invalid PNG' if failure == 'malformed' else buf.getvalue(), headers=headers))
    elif failure == 'unsafe_url':
        page.evaluate("window.downloadUrl='http://files.oaiusercontent.com/image.png'")
    page.locator('#library').click()
    page.wait_for_function("document.getElementById('library-status').textContent.includes('not loaded')")
    assert page.evaluate('window.calls') == []
    assert page.evaluate('window.savedState') is None
    assert page.locator('#preview').is_hidden()


@pytest.mark.parametrize('selection', [[], [{'fileId': FILE_ID, 'mimeType': 'application/pdf'}],
    [{'fileId': '', 'mimeType': 'image/png'}], [{'fileId': FILE_ID, 'mimeType': 'image/png'}] * 2])
def test_cancelled_or_invalid_selection_reads_nothing(selection, library_host):
    page, _ = library_host(result=REUSE, selection=selection)
    page.locator('#library').click()
    page.wait_for_function("!document.getElementById('library').disabled")
    assert page.evaluate('window.calls') == []
    assert page.evaluate('window.fileReads') == []


def test_missing_library_api_is_visible(library_host):
    page, _ = library_host(result=REUSE, no_api=True)
    assert page.locator('#library').is_disabled()
    assert 'unavailable' in page.locator('#library-status').inner_text()
    assert page.evaluate('window.calls') == []


def test_delayed_restore_cannot_replace_new_selection(library_host, file_host, buf, preview):
    file_host.files[FILE_ID] = buf.getvalue()
    older = io.BytesIO()
    Image.new('RGB', (32, 32), 'red').save(older, format='PNG')
    file_host.files['older-image'] = older.getvalue()
    page, _ = library_host()
    page.evaluate("""() => {
      const getUrl=window.openai.getFileDownloadUrl;
      window.openai.getFileDownloadUrl=({fileId})=>fileId==='older-image'
        ? new Promise(resolve=>window.releaseOld=()=>resolve({downloadUrl:'https://files.oaiusercontent.com/older-image'}))
        : getUrl({fileId});
      window.dispatchEvent(new CustomEvent('openai:set_globals',{detail:{globals:{widgetState:{
        privateContent:{savedFiles:{'render.png':{fileId:'older-image'}}}}}}}));
    }""")
    page.wait_for_function('!!window.releaseOld')
    page.locator('#library').click()
    page.wait_for_function('window.savedState?.privateContent?.libraryImage?.fileId===' + json.dumps(FILE_ID))
    page.wait_for_function('window.calls.length===1')
    page.evaluate('window.releaseOld()')
    page.wait_for_function("document.getElementById('preview').naturalWidth===80")
    # Wait for the old fetch to finish; applying it must still be rejected.
    page.wait_for_function("performance.getEntriesByType('resource').some(r=>r.name.endsWith('/older-image'))")
    page.wait_for_timeout(100)
    assert page.locator('#preview').evaluate('(image)=>image.naturalWidth') == 80
    assert page.evaluate('window.savedState.privateContent.libraryImage.fileId') == FILE_ID
    assert page.evaluate('window.calls[0].params.content[1]') == {'type': 'image', **preview}
    assert len(page.evaluate('window.calls')) == 1

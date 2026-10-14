"""Native file handoff must not consume files before the host fetches them."""
import json
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from blender_render.api import create_app
from test_d2_consumer import consumer, server
from test_d2_review import call, record, submit
from test_jobs_api import payload, TOKEN


def test_native_image_handoff_no_prose_links_and_fetch_cleanup(consumer):
    service, _, _ = consumer
    job = submit(service)
    service._execute(service.store.claim())
    path = service.store.artifact_root(job['job_id']) / 'render.png'
    original = path.read_bytes()
    result = call(server(service), 'render_result', {'job_id': job['job_id']})
    assert not result.is_error
    metadata = record(result)
    assert metadata['complete'] and metadata['delivery_mode'] == 'native_files'
    assert metadata['file_import_confirmed'] is False
    assert 'download_url' not in json.dumps(metadata)
    assert 'preview_url' not in metadata
    assert any(item.type == 'image' for item in result.content)
    refs = [item for item in result.content if item.type == 'resource_link']
    assert {ref.name for ref in refs} == {'render.png', 'manifest.json'}
    refs.sort(key=lambda ref: ref.name != 'render.png')
    assert refs[0].name == 'render.png' and refs[0].mimeType == 'image/png'
    assert refs[0].size == len(original)
    for item in result.content:
        if item.type == 'text':
            assert 'https://' not in item.text and '](' not in item.text
    preview = result.meta['preview']
    native = next(item for item in result.content if item.type == 'image')
    assert preview == {'mimeType': 'image/png', 'data': native.data}
    assert len(preview['data']) <= 524288
    assert 'preview' not in metadata  # Widget bytes stay outside structured text.
    assert path.read_bytes() == original  # Returning a reference isn't delivery.
    # The signed native-file reference uses the normal owned download route.
    api = TestClient(create_app(service, token=TOKEN, signing_key='x' * 48,
                                public_url='https://render.example.org'))
    saved_preview = api.get(urlsplit(result.meta['saveImage']['url']).path)
    assert saved_preview.content == original
    assert saved_preview.headers['access-control-allow-origin'] == '*'
    assert path.exists()  # Reading for a host save is not a delivery receipt.
    # Host imports and viewer autosave can race: both must read without consuming.
    for ref in refs:
        for _ in range(2):
            response = api.get(str(ref.uri))
            assert response.status_code == 200
            assert response.headers['access-control-allow-origin'] == '*'
    assert path.exists()
    assert api.get(str(refs[0].uri).replace('/download/', '/download/tampered')).status_code == 401
    # Legacy explicit consumption remains unchanged.
    route = urlsplit(str(refs[0].uri)).path
    assert api.head(route).status_code in (405, 200)
    assert path.exists()
    response = api.get(route)
    assert response.status_code == 200 and response.content == original
    assert not path.exists()
    states = json.loads(service.store.db.execute('SELECT artifact_states FROM jobs WHERE id=?',
                        (job['job_id'],)).fetchone()[0])
    assert states['render.png'] == 'delivered'
    assert api.get(route).status_code == 410
    assert len(service.backend.calls) == 1
    api.close()


def test_viewer_discovery_allows_render_and_library_origins(consumer):
    import asyncio
    from fastmcp import Client
    from blender_render.mcp_server import INLINE_PREVIEW_URI
    async def run():
        async with Client(server(consumer[0])) as client:
            resource = (await client.read_resource(INLINE_PREVIEW_URI))[0]
            assert resource.meta['ui']['csp'] == {'connectDomains': ['https://render.example.org', 'https://*.oaiusercontent.com', 'https://chatgpt.com'], 'resourceDomains': ['https://render.example.org', 'https://*.oaiusercontent.com', 'https://chatgpt.com']}
            assert "'data:' + preview.mimeType + ';base64,'" in resource.text
            assert 'download_url' not in resource.text
    asyncio.run(run())


def test_cached_viewer_versions_remain_readable(consumer):
    import asyncio
    from fastmcp import Client
    async def run():
        async with Client(server(consumer[0])) as client:
            for version in range(1, 14):
                uri = f'ui://blender-render/inline-result-v{version}.html'
                resource = (await client.read_resource(uri))[0]
                assert str(resource.uri) == uri
                assert resource.mimeType == 'text/html;profile=mcp-app'
                assert 'domain' not in resource.meta['ui']
                assert 'openai/widgetDomain' not in resource.meta
                assert "message.method === 'ping'" in resource.text
    asyncio.run(run())


def test_private_http_public_origin_reaches_downloads_and_widget(consumer):
    import asyncio
    from fastmcp import Client
    from blender_render import mcp_server
    service = consumer[0]
    job = submit(service)
    service._execute(service.store.claim())
    mcp = mcp_server.create_private_http(service, 'x' * 48, public_url='https://test.example.org')
    result = call(mcp, 'render_result', {'job_id': job['job_id']})
    assert not result.is_error
    assert result.meta['saveImage']['url'].startswith('https://test.example.org/preview/')
    async def run():
        async with Client(mcp) as client:
            resource = (await client.read_resource(mcp_server.INLINE_PREVIEW_URI))[0]
            assert resource.meta['ui']['csp']['connectDomains'] == ['https://test.example.org', 'https://*.oaiusercontent.com', 'https://chatgpt.com']
    asyncio.run(run())


def test_private_http_rejects_non_origin_public_urls(consumer):
    import pytest
    from blender_render.mcp_server import create_private_http
    for url in ['http://example.org', 'https://example.org/path', 'https://u:p@example.org',
                'https://example.org?token=secret', 'https://example.org#fragment']:
        with pytest.raises(ValueError, match='HTTPS origin'):
            create_private_http(consumer[0], 'x' * 48, public_url=url)


def test_library_picker_does_not_read_or_render_originals(consumer):
    service, backend, _ = consumer
    result = call(server(service), 'open_saved_image', {})
    assert not result.is_error
    assert result.meta == {'library': {'mode': 'reuse'}}
    assert result.structured_content == {'status': 'select_library_image'}
    assert all(item.type == 'text' for item in result.content)
    assert not backend


def test_explicit_native_png_handoff_is_owned_exact_and_non_consuming(consumer, monkeypatch):
    import asyncio
    import hashlib
    from fastmcp import Client
    from blender_render import mcp_server
    service, _, _ = consumer
    job = submit(service)
    service._execute(service.store.claim())
    path = service.store.artifact_root(job['job_id']) / 'render.png'
    original = path.read_bytes()
    factory = server(service)

    async def discovery():
        async with Client(factory) as client:
            tool = next(t for t in await client.list_tools() if t.name == 'get_render_png_file')
            assert not tool.meta or ('ui' not in tool.meta and 'openai/outputTemplate' not in tool.meta)
    asyncio.run(discovery())
    result = call(factory, 'get_render_png_file', {'job_id': job['job_id']})
    assert not result.is_error and not result.meta
    # The native-file contract must remain usable without structuredContent.
    text_record = next(json.loads(item.text) for item in result.content
                       if item.type == 'text' and item.text.startswith('{'))
    assert text_record == result.structured_content
    file = text_record['file']
    assert file['sha256'] == hashlib.sha256(original).hexdigest()
    assert file['size_bytes'] == len(original)
    assert file['file_name'] == 'blender-render-' + job['job_id'] + '.png'
    assert result.structured_content['file_import_confirmed'] is False
    assert result.structured_content['space_save_confirmed'] is False
    assert not any(item.type == 'image' for item in result.content)
    assert str(next(item.uri for item in result.content if item.type == 'resource_link')) == file['source_url']
    with TestClient(create_app(service, token=TOKEN, signing_key='x' * 48)) as api:
        for _ in range(2):
            response = api.get(file['source_url'])
            assert response.status_code == 200 and response.content == original
        assert path.read_bytes() == original
        assert api.get(file['source_url'].replace('/download/', '/download/tampered')).status_code == 401
    monkeypatch.setattr(mcp_server, 'owner', lambda: 'other-user')
    assert call(factory, 'get_render_png_file', {'job_id': job['job_id']}).is_error
    monkeypatch.setattr(mcp_server, 'owner', lambda: 'alice')
    path.unlink()
    missing = call(factory, 'get_render_png_file', {'job_id': job['job_id']})
    assert missing.is_error and all(item.type != 'resource_link' for item in missing.content)
    assert len(service.backend.calls) == 1

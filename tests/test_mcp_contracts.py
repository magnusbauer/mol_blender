import asyncio
import json
import pytest
from types import SimpleNamespace
from PIL import Image as PillowImage
from fastmcp import Client as MCPClient
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from blender_render.mcp_server import create_stdio, create_http
from blender_render.api import Downloads


def test_preview_is_bounded_and_preserves_original_alpha(tmp_path):
    import base64
    import io
    from blender_render.mcp_server import image_preview
    path = tmp_path / 'large.png'
    source = PillowImage.effect_noise((2048, 1536), 80).convert('RGBA')
    source.putalpha(PillowImage.linear_gradient('L').resize(source.size))
    source.save(path)
    original = path.read_bytes()
    result = image_preview(path).to_image_content()
    raw = base64.b64decode(result.data)
    preview = PillowImage.open(io.BytesIO(raw))
    assert len(raw) <= 384 * 1024
    assert max(preview.size) <= 1024 and preview.mode == 'RGBA'
    assert preview.getchannel('A').getextrema() == (0, 255)
    assert path.read_bytes() == original


def test_native_preview_and_stdio_tools(monkeypatch, tmp_path):
    import blender_render.client
    path = tmp_path / 'preview.png'
    PillowImage.new('RGBA',(8,8),(20,100,40,128)).save(path)
    class Client:
        def download(self, identifier, output):
            assert identifier == 'job'
            return {'outputs':{'render.png':str(path)},'manifest':{'dss_ran':True}}
    def configured(profile, **kwargs):
        assert kwargs == {'admission': True, 'consumer_trusted': True}
        # OAuth's synchronous adapter starts its own asyncio loop. Tool calls
        # must run that adapter outside the MCP server's event loop.
        with pytest.raises(RuntimeError, match='no running event loop'):
            asyncio.get_running_loop()
        return Client()
    monkeypatch.setattr(blender_render.client,'configured_client',configured)
    async def run():
        async with MCPClient(create_stdio(compatibility=True)) as client:
            tools = {t.name:t for t in await client.list_tools()}
            assert set(tools) == {'list_presets','preset_details','list_structures','delete_structure','import_structure','render_structure','submit_render','render_status','render_result','cancel_render','available_quota','list_recent_renders','recover_render','recover_transfer'}
            assert tools['submit_render'].annotations.idempotentHint
            assert tools['render_structure'].annotations.idempotentHint
            result = await client.call_tool('render_result',{'job_id':'job','output_path':str(path)})
            assert any(item.type == 'image' and item.mimeType == 'image/png' for item in result.content)
            assert not any(item.type == 'text' and 'dss_ran' in item.text for item in result.content)
            assert result.structured_content['outputs']['render.png'] == str(path)
    asyncio.run(run())


def test_chatgpt_declared_file_contract():
    auth = StaticTokenVerifier(tokens={})
    service = SimpleNamespace()
    server = create_http(service,auth=auth,downloads=Downloads('x'*48,''),converter=None,compatibility=True,legacy_widget=True)
    async def run():
        async with MCPClient(server) as client:
            tools = {t.name:t for t in await client.list_tools()}
            imp = tools['import_structure']
            assert imp.meta['openai/fileParams'] == ['file']
            file_schema = imp.inputSchema['properties']['file']
            assert {'download_url','file_id'} <= set(file_schema['required'])
            assert {'download_url','file_id','mime_type','file_name'} == set(file_schema['properties'])
            assert file_schema['properties']['file_name']['type'] == 'string'
            assert tools['render_structure'].meta['openai/fileParams'] == ['file']
            assert tools['render_structure'].annotations.idempotentHint
            assert tools['render_status'].annotations.readOnlyHint
            assert tools['render_status'].inputSchema['properties']['wait_seconds']['maximum'] == 25
            assert tools['render_result'].outputSchema
            # The result viewer receives embedded bytes, not expiring URLs.
            from blender_render.mcp_server import INLINE_PREVIEW_URI
            assert tools['render_result'].meta['ui']['resourceUri'] == INLINE_PREVIEW_URI
            assert tools['render_result'].meta['openai/outputTemplate'] == INLINE_PREVIEW_URI
            uri='ui://blender-render/result-v1.html'
            resource=await client.read_resource(uri)
            assert resource[0].mimeType == 'text/html;profile=mcp-app'
            assert resource[0].meta['ui']['csp']['connectDomains'] == []
    asyncio.run(run())


def test_preview_origin_is_separate_from_artifact_network_permissions():
    endpoint = 'https://almostscheidplatz--blender-render-beta-gateway.us-east.modal.direct'
    widget = 'https://almostscheidplatz--blender-render-beta.modal.run'
    server = create_http(SimpleNamespace(), auth=StaticTokenVerifier(tokens={}),
        downloads=Downloads('x'*48, endpoint), converter=None, widget_origin=widget, legacy_widget=True)
    async def run():
        async with MCPClient(server) as client:
            resource = (await client.read_resource('ui://blender-render/result-v1.html'))[0]
            assert resource.meta['ui']['domain'] == widget
            assert resource.meta['ui']['csp']['resourceDomains'] == [endpoint]
            assert resource.meta['openai/widgetCSP']['redirect_domains'] == [endpoint]
    asyncio.run(run())

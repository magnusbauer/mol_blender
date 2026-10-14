"""The live diagnostic must not leak its visual answer through ordinary tool text."""
import asyncio
import base64
import io
import json
import re
from pathlib import Path

from fastmcp import Client
from PIL import Image


def test_challenge_answers_are_only_in_pixels_and_local_oracle(tmp_path):
    from support import image_handoff as module
    oracle = tmp_path / "expected.jsonl"
    saved = tmp_path / "saved.png"
    Image.new("RGB", (128, 128), "blue").save(saved)
    server = module.make_server(oracle, saved)
    async def run():
        async with Client(server) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {"open_background_test", "open_message_test",
                "open_no_image_control", "open_saved_blender_test", "open_library_save_test",
                "open_blender_library_save_test", "disable_original_images", "open_library_reuse_test",
                "get_native_blender_png_test"}
            assert all(not t.inputSchema.get("properties") for t in tools)
            for name in ("open_background_test", "open_message_test", "open_no_image_control"):
                result = await client.call_tool(name, {})
                assert not result.is_error
                assert all(item.type == "text" for item in result.content)
                expected = json.loads(oracle.read_text().splitlines()[-1])["expected"]
                model_visible = json.dumps({"content": [x.model_dump() for x in result.content],
                                           "structuredContent": result.structured_content})
                assert expected["code"] not in model_visible
                assert expected["shape"] not in model_visible
                assert not re.search(r"\b" + expected["colour"] + r"\b", model_visible)
                png = base64.b64decode(result.meta["preview"]["data"])
                with Image.open(io.BytesIO(png)) as image:
                    assert image.size == (128, 128)
            result = await client.call_tool("open_saved_blender_test", {})
            assert base64.b64decode(result.meta["preview"]["data"]) == saved.read_bytes()
            for mode in module.MODES:
                resource = (await client.read_resource(module.URI + mode + ".html"))[0]
                assert resource.meta["ui"]["csp"] == {"connectDomains": ["https://*.oaiusercontent.com", "https://chatgpt.com"], "resourceDomains": ["https://*.oaiusercontent.com", "https://chatgpt.com"]}
                html = resource.text
                options = json.loads(re.search(r'<script id="viewer-options" type="application/json">(.*?)</script>', html).group(1))
                assert options["backgroundContext"] == (mode not in ("message", "control"))
                assert options["imageMessage"] == (mode not in ("background", "control"))
    asyncio.run(run())
    assert oracle.stat().st_mode & 0o777 == 0o600


def test_diagnostic_has_a_bounded_session(tmp_path):
    from support import image_handoff as module
    server = module.make_server(tmp_path / "expected.jsonl", tmp_path / "unused.png")
    async def run():
        async with Client(server) as client:
            for _ in range(40):
                await client.call_tool("open_background_test", {})
            result = await client.call_tool("open_background_test", {}, raise_on_error=False)
            assert result.is_error
    asyncio.run(run())


def test_library_reuse_has_no_original_image_fallback(tmp_path):
    import hashlib
    from support import image_handoff as module
    saved = tmp_path / "render.png"
    Image.new("RGB", (128, 128), "blue").save(saved)
    original = saved.read_bytes()
    oracle = tmp_path / "expected.jsonl"
    server = module.make_server(oracle, saved)
    async def run():
        async with Client(server) as client:
            early = await client.call_tool("open_library_reuse_test", {}, raise_on_error=False)
            assert early.is_error
            for name in ("open_library_save_test", "open_blender_library_save_test"):
                result = await client.call_tool(name, {})
                assert all(item.type == "text" for item in result.content)
                source = result.meta["saveFiles"][0]
                assert "url" not in source
                data = base64.b64decode(source["data"])
                assert hashlib.sha256(data).hexdigest() == result.meta["library"]["sha256"]
                assert source["data"] == result.meta["preview"]["data"]
                if name == "open_blender_library_save_test":
                    assert data == original
                else:
                    answer = next(json.loads(line)["expected"] for line in oracle.read_text().splitlines()
                                  if json.loads(line).get("expected"))
                    visible = json.dumps({"text":[x.text for x in result.content], "structured":result.structured_content})
                    assert answer["code"] not in visible
                repeat = await client.call_tool(name, {})
                assert repeat.meta == result.meta  # A retry cannot silently replace the saved challenge.
            result = await client.call_tool("disable_original_images", {})
            assert result.structured_content["original_images_available"] is False
            saved.unlink()  # Even the backing public fixture is absent for the remainder of this test.
            for name in ("open_background_test", "open_message_test", "open_no_image_control", "open_saved_blender_test",
                         "open_library_save_test", "open_blender_library_save_test", "get_native_blender_png_test"):
                result = await client.call_tool(name, {}, raise_on_error=False)
                assert result.is_error
                assert not result.meta or "preview" not in result.meta
            for _ in range(2):
                reuse = await client.call_tool("open_library_reuse_test", {})
                assert reuse.meta == {"library":{"mode":"reuse"}}
                assert all(item.type == "text" for item in reuse.content)
                assert reuse.structured_content == {"original_images_available":False}
            again = await client.call_tool("disable_original_images", {})
            assert not again.is_error
    asyncio.run(run())


def test_native_file_uses_exact_bytes_and_is_revocable(tmp_path):
    import hashlib
    import httpx
    from support import image_handoff as module
    saved = tmp_path / "render.png"
    Image.new("RGB", (128, 128), "blue").save(saved)
    original = saved.read_bytes()
    source = module.NativeTestFile(saved)
    source.activate("https://test.example.org")
    oracle = tmp_path / "expected.jsonl"
    server = module.make_server(oracle, saved, native_source=source)
    app = server.http_app(path="/secret/mcp", stateless_http=True)

    async def run():
        async with Client(server) as client, httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url=source.origin) as http:
            tool = next(t for t in await client.list_tools() if t.name == "get_native_blender_png_test")
            assert not tool.meta or ("ui" not in tool.meta and "openai/outputTemplate" not in tool.meta)
            assert (await http.get(source.path)).status_code == 404
            result = await client.call_tool(tool.name, {})
            assert not result.is_error and not result.meta
            assert not result.structured_content["file_import_confirmed"]
            assert not result.structured_content["space_save_confirmed"]
            # Simulate a host that materializes the ResourceLink but exposes
            # only ordinary text to its model (the observed live failure).
            text_record = next(json.loads(item.text) for item in result.content
                               if item.type == "text" and item.text.startswith("{"))
            file = text_record["file"]
            assert text_record == result.structured_content
            assert file["size_bytes"] == len(original)
            assert file["sha256"] == hashlib.sha256(original).hexdigest()
            assert file["file_name"].startswith("blender-native-")
            ref = next(item for item in result.content if item.type == "resource_link")
            assert str(ref.uri) == file["source_url"]
            assert not any(item.type == "image" for item in result.content)
            # A retry cannot switch the fixture or consume the source.
            saved.unlink()
            repeat = await client.call_tool(tool.name, {})
            assert repeat.structured_content == result.structured_content
            for _ in range(2):
                response = await http.get(file["source_url"])
                assert response.status_code == 200 and response.content == original
                assert response.headers["cache-control"] == "no-store"
                assert response.headers["content-type"] == "image/png"
                assert file["file_name"] in response.headers["content-disposition"]
            head = await http.head(file["source_url"])
            assert head.status_code == 200 and not head.content
            assert int(head.headers["content-length"]) == len(original)
            assert (await http.get("/" + file["file_name"])).status_code == 404
            assert (await http.get(source.path + "-tampered")).status_code == 404
            await client.call_tool("disable_original_images", {})
            assert source.data is source.handoff is None
            for method in (http.get, http.head):
                revoked = await method(file["source_url"])
                assert revoked.status_code == 410 and not revoked.content
            rejected = await client.call_tool(tool.name, {}, raise_on_error=False)
            assert rejected.is_error
    asyncio.run(run())
    records = [json.loads(line) for line in oracle.read_text().splitlines()]
    assert [row["event"] for row in records] == ["native_source", "originals_disabled"]
    assert "source_url" not in oracle.read_text()


def test_native_file_expiry_and_missing_runtime_setup(tmp_path):
    import httpx
    from support import image_handoff as module
    saved = tmp_path / "render.png"
    Image.new("RGB", (128, 128), "blue").save(saved)
    now = [1000.0]
    source = module.NativeTestFile(saved, duration=30, clock=lambda: now[0])
    server = module.make_server(tmp_path / "oracle", saved, native_source=source)
    async def run():
        async with Client(server) as client:
            early = await client.call_tool("get_native_blender_png_test", {}, raise_on_error=False)
            assert early.is_error
            source.activate("https://test.example.org")
            await client.call_tool("get_native_blender_png_test", {})
            now[0] += 30
            async with httpx.AsyncClient(transport=httpx.ASGITransport(server.http_app()),
                                         base_url=source.origin) as http:
                assert (await http.get(source.path)).status_code == 410
            assert source.data is source.handoff is None
            expired = await client.call_tool("get_native_blender_png_test", {}, raise_on_error=False)
            assert expired.is_error
    asyncio.run(run())


def test_native_file_rejects_wrong_or_oversized_fixture(tmp_path):
    import pytest
    from support import image_handoff as module
    saved = tmp_path / "render.png"
    for invalid in ("oversized", "dimensions", "format", "corrupt"):
        if invalid == "oversized":
            saved.write_bytes(b"x" * 65537)
        elif invalid == "dimensions":
            Image.new("RGB", (129, 128)).save(saved)
        elif invalid == "format":
            Image.new("RGB", (128, 128)).save(saved, format="JPEG")
        else:
            saved.write_bytes(b"not a PNG")
        source = module.NativeTestFile(saved)
        source.activate("https://test.example.org")
        with pytest.raises((ValueError, OSError)):
            source.result()
        assert source.data is source.handoff is None


def test_embedded_native_file_has_exact_bytes_without_a_download_url(tmp_path):
    import hashlib
    import httpx
    from mcp import types
    from support import image_handoff as module
    saved = tmp_path / "render.png"
    Image.new("RGB", (128, 128), "blue").save(saved)
    original = saved.read_bytes()
    source = module.NativeTestFile(saved, transport="embedded")
    source.activate("https://test.example.org")
    oracle = tmp_path / "expected.jsonl"
    server = module.make_server(oracle, saved, native_source=source)

    async def run():
        async with Client(server) as client, httpx.AsyncClient(
                transport=httpx.ASGITransport(server.http_app()), base_url=source.origin) as http:
            tool = next(t for t in await client.list_tools() if t.name == "get_native_blender_png_test")
            assert not tool.meta or ("ui" not in tool.meta and "openai/outputTemplate" not in tool.meta)
            result = await client.call_tool(tool.name, {})
            assert not result.is_error and not result.meta
            # Round-trip the actual standard MCP JSON, not a private Python object.
            wire = types.CallToolResult.model_validate_json(types.CallToolResult(
                content=result.content, structuredContent=result.structured_content).model_dump_json())
            embedded = next(item.resource for item in wire.content if item.type == "resource")
            decoded = base64.b64decode(embedded.blob, validate=True)
            assert decoded == original
            record = next(json.loads(item.text) for item in wire.content
                          if item.type == "text" and item.text.startswith("{"))
            assert record == wire.structuredContent
            assert record["delivery_mode"] == "native_embedded_file_import"
            assert not record["file_import_confirmed"] and not record["space_save_confirmed"]
            file = record["file"]
            assert file["file_name"].startswith("blender-embedded-")
            assert file["size_bytes"] == len(decoded)
            assert file["sha256"] == hashlib.sha256(decoded).hexdigest()
            assert file["resource_uri"] == str(embedded.uri) == source.resource_uri
            assert embedded.mimeType == "image/png"
            assert "source_url" not in file
            assert not any(item.type in {"resource_link", "image"} for item in wire.content)
            assert "https://" not in wire.model_dump_json()
            assert (await http.get(source.path)).status_code == 404
            # Retrying/reading via MCP cannot silently replace the test file.
            saved.unlink()
            repeat = await client.call_tool(tool.name, {})
            assert repeat.content == result.content
            resources = await client.read_resource(source.resource_uri)
            assert len(resources) == 1
            assert base64.b64decode(resources[0].blob, validate=True) == original
            await client.call_tool("disable_original_images", {})
            assert source.data is source.handoff is None
            rejected = await client.call_tool(tool.name, {}, raise_on_error=False)
            assert rejected.is_error
            import pytest
            with pytest.raises(Exception, match="disabled|expired"):
                await client.read_resource(source.resource_uri)
    asyncio.run(run())
    records = [json.loads(line) for line in oracle.read_text().splitlines()]
    assert [row["event"] for row in records] == ["native_source", "originals_disabled"]
    assert records[0]["transport"] == "embedded"
    assert "blob" not in oracle.read_text() and "resource_uri" not in oracle.read_text()


def test_embedded_native_file_expires_and_rejects_unknown_transport(tmp_path):
    import pytest
    from support import image_handoff as module
    saved = tmp_path / "render.png"
    Image.new("RGB", (128, 128), "blue").save(saved)
    with pytest.raises(ValueError, match="transport"):
        module.NativeTestFile(saved, transport="unknown")
    now = [1000.0]
    source = module.NativeTestFile(saved, duration=30, clock=lambda: now[0], transport="embedded")
    source.activate("https://test.example.org")
    server = module.make_server(tmp_path / "oracle", saved, native_source=source)
    async def run():
        async with Client(server) as client:
            result = await client.call_tool("get_native_blender_png_test", {})
            assert not result.is_error
            now[0] += 30
            with pytest.raises(Exception, match="expired"):
                await client.read_resource(source.resource_uri)
            assert source.data is source.handoff is None
            result = await client.call_tool("get_native_blender_png_test", {}, raise_on_error=False)
            assert result.is_error
    asyncio.run(run())


def test_session_storage_cleans_unless_retention_is_requested(tmp_path):
    import runpy
    from pytest import raises
    launcher = runpy.run_path(str(Path(__file__).parents[1] / 'scripts/start_image_handoff_test.py'))
    directory = launcher['session_directory']
    with raises(RuntimeError):
        with directory() as temporary:
            (temporary / 'connection-url.txt').write_text('test capability')
            raise RuntimeError('interrupted')
    assert not temporary.exists()
    output = tmp_path / 'retained'
    with directory(output) as retained:
        (retained / 'expected.jsonl').write_text('test record')
    assert (output / 'expected.jsonl').read_text() == 'test record'
    assert output.stat().st_mode & 0o777 == 0o700
    with raises(FileExistsError):
        with directory(output):
            pass


def test_viewer_options_escape_script_content():
    from blender_render.viewer import inline_viewer_html
    prompt = '</script><script>unexpected()</script>'
    html = inline_viewer_html(message_prompt=prompt)
    assert prompt not in html
    options = json.loads(re.search(r'<script id="viewer-options" type="application/json">(.*?)</script>', html).group(1))
    assert options['messagePrompt'] == prompt

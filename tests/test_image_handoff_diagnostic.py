"""The live diagnostic must not leak its visual answer through ordinary tool text."""
import asyncio
import base64
import importlib.util
import io
import json
import re
from pathlib import Path

from fastmcp import Client
from PIL import Image


def diagnostic():
    path = Path(__file__).resolve().parents[1] / "scripts/start_image_handoff_test.py"
    spec = importlib.util.spec_from_file_location("image_handoff_diagnostic", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_challenge_answers_are_only_in_pixels_and_local_oracle(tmp_path):
    module = diagnostic()
    oracle = tmp_path / "expected.jsonl"
    saved = tmp_path / "saved.png"
    Image.new("RGB", (128, 128), "blue").save(saved)
    server = module.make_server(oracle, saved)
    async def run():
        async with Client(server) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {"open_background_test", "open_message_test",
                "open_no_image_control", "open_saved_blender_test"}
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
                assert resource.meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}
                html = resource.text
                assert ("delete hostCapabilities.updateModelContext;" in html) == (mode in ("message", "control"))
                assert ("delete hostCapabilities.message;" in html) == (mode in ("background", "control"))
    asyncio.run(run())
    assert oracle.stat().st_mode & 0o777 == 0o600


def test_diagnostic_has_a_bounded_session(tmp_path):
    module = diagnostic()
    server = module.make_server(tmp_path / "expected.jsonl", tmp_path / "unused.png")
    async def run():
        async with Client(server) as client:
            for _ in range(40):
                await client.call_tool("open_background_test", {})
            result = await client.call_tool("open_background_test", {}, raise_on_error=False)
            assert result.is_error
    asyncio.run(run())

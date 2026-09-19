"""Canonical records of actual MCP discovery, including order and metadata."""
import hashlib
import json
from types import SimpleNamespace


async def discover_surfaces():
    from fastmcp import Client
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
    from blender_render import mcp_server
    from blender_render.api import Downloads
    service = SimpleNamespace(store=SimpleNamespace(policy=SimpleNamespace(public=False)),
                              selection_policy=None, converter=None)
    def hosted(**kwargs):
        return mcp_server.create_http(service, auth=StaticTokenVerifier(tokens={}),
            downloads=Downloads('x' * 48, 'https://render.example.org'), converter=None, **kwargs)
    factories = {
        'hosted_default': hosted(),
        'trusted_default': mcp_server.create_stdio(trusted=True),
        'hosted_compatibility': hosted(compatibility=True),
        'trusted_compatibility': mcp_server.create_stdio(trusted=True, compatibility=True),
        'legacy_widget': hosted(legacy_widget=True),
    }
    records = {}
    for mode, factory in factories.items():
        async with Client(factory) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            templates = await client.list_resource_templates()
        records[mode] = {'mode': mode,
            'tools': [tool.model_dump(mode='json', by_alias=True, exclude_none=False) for tool in tools],
            'resources': [resource.model_dump(mode='json', by_alias=True, exclude_none=False) for resource in resources],
            'resource_templates': [resource.model_dump(mode='json', by_alias=True, exclude_none=False) for resource in templates]}
    return records


def digests(records):
    return {key: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode()).hexdigest() for key, value in records.items()}


EXPECTED_DIGESTS = {'hosted_default': '9acf2f5a5a9e21f00f84a16032c71462d000f8ab2021d83d60bc6bbac1ff1bc4',
 'trusted_default': 'fc545d27d9cea27139d78892a1e7723c79f3523be77b4d6c8fb988fa774faf02',
 'hosted_compatibility': '848efc2fdaedf364bf078dd0c8a58e543548f436a5924fee58217f95bd007ae8',
 'trusted_compatibility': '93f9e3c0c68f2b7602d0e087d454dfa14b78a3d95292369810b43d6312147820',
 'legacy_widget': '35a63376acd34f32c3b899d36e7554d32b83001908903dcb66a433b02fa69a53'}

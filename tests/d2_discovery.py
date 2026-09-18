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


EXPECTED_DIGESTS = {'hosted_default': '1a0e099c79c24ff08b37ad9e3735ae2d4ef837249150d8d535ac25da415fa59e',
 'trusted_default': '120831a2f106523650adedc3128c6fa08a6c773f55bcde4f05f96573ba9b27c6',
 'hosted_compatibility': '4ba037b4be536b5dfe6c0fdfc5218301ccc566346b8202cdd4821d6453980758',
 'trusted_compatibility': 'd03da01c3daefedc7c4ee2310085ff68ebf67d90f52e54e605468fc31a8f24ae',
 'legacy_widget': '635ec0fcb6d960e8eda0027f5439842a068a99ba72825ee4964a0f1a1c3b3c7b'}

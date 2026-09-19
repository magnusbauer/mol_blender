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


EXPECTED_DIGESTS = {'hosted_default': '6c3bc17c83c7e2df5f66afceb6e882aff681f681d6ec405a0a78e5a9d17ad228',
 'trusted_default': '4e61d5d7b12058def3e9ed91bca843a13e32bcd36d362eca4f831ced6729cee8',
 'hosted_compatibility': '5dcab18841d268f87cdd22c6201930f8d78f81cb699b5f5e80c434d3c75176c5',
 'trusted_compatibility': '1ddb87b975670429a609227a474dff1f3311828cdd9f2687e79f9c6208df4932',
 'legacy_widget': 'c9d9a1bf2f539f0428c202ed021aeefef77e234f25c5e5963552407a7223fd29'}

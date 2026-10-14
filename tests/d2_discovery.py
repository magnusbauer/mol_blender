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


EXPECTED_DIGESTS = {'hosted_default': 'cd45a250e574166011b4e19995a5233f032b25f29a87bfcc44ef47f2f742f899',
 'trusted_default': 'd5375263687fc7616a9d64ae72934cfdc15978920c2ddecd6a6fb94bc81820eb',
 'hosted_compatibility': 'e3fb80b74c2fd3a12cd828cba6c354ab87fd717d1fbb9714d2febbe6a6390059',
 'trusted_compatibility': 'cfb8de85c53d6c809a1f487692c785e26aa579f4a0a0ab02370f97086c59020a',
 'legacy_widget': '9b09a5e14b2ee1e54580d69cc8be26008a4e9acab3dccf07d08d593112dee9cb'}

"""Explicit runner child; no application import precedes the audit boundary."""
import c3_boundary_guard  # noqa: F401
from c1_boundary_guard import attempts
import json
import os
from pathlib import Path
import sys
import asyncio

from blender_render import consumer_records

# Offline receipt validation must not import current selection or engine code.
assert 'blender_render.selection' not in sys.modules
assert 'blender_render.engine' not in sys.modules
assert consumer_records.result_metadata({})['preset_decision'] is None

from blender_render import consumer, mcp_requests, presets, selection

root = Path(os.environ['D2_EXPECT_PACKAGE']).resolve()
for module in (consumer_records, consumer, mcp_requests, presets, selection):
    assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
if '--reverse' in sys.argv:
    original = presets.catalog
    presets.catalog = lambda: list(reversed(original()))

requests = [consumer.normalize_request(request) for request in (
    {'request_key': 'probe-auto', 'visual_brief': {'material_family': 'dried paint'}},
    {'request_key': 'probe-explicit', 'preset': 'pastel', 'revision': 1,
     'hard_requirements': {'content': ['protein', 'nucleic', 'protein']},
     'visual_brief': {'palette': ['#abcdef', '#123456']}, 'samples': 24},
    {'request_key': 'probe-low', 'preset': 'pastel', 'visual_brief': {'material_family': 'directional_ink'},
     'allow_low_fit_approximation': True})]
decisions = [consumer.safe_decision(consumer.preselect(request, consumer.server_policy(True))) for request in requests]
schemas = [model.model_json_schema() for model in (mcp_requests.HostedRenderRequest, mcp_requests.TrustedRenderRequest)]
from d2_discovery import discover_surfaces, digests, EXPECTED_DIGESTS
surfaces = asyncio.run(discover_surfaces())
assert digests(surfaces) == EXPECTED_DIGESTS
assert attempts == [], attempts
print(json.dumps({'requests': requests, 'identities': [consumer.identity(request) for request in requests],
    'decisions': decisions, 'schemas': schemas, 'surfaces': surfaces, 'surface_digests': digests(surfaces),
    'catalog_digest': presets.compact_catalog()['digest'],
    'receipt_reader_is_independent': True, 'import_origins_verified': True,
    'boundary_attempts': attempts}, sort_keys=True))

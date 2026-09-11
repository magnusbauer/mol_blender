"""Explicit runner child; load the audit boundary before application imports."""
import c3_boundary_guard  # noqa: F401
from c1_boundary_guard import attempts
import json
import os
from pathlib import Path
import sys

from blender_render import authoring, core, engine, presets, selection, spec

root = Path(os.environ['D1_EXPECT_PACKAGE']).resolve()
for module in (authoring, core, engine, presets, selection, spec):
    assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
if '--reverse' in sys.argv:
    original = presets.catalog

    def reversed_catalog():
        rows = list(reversed(original()))
        for row in rows:
            for key, value in row.items():
                if isinstance(value, list):
                    row[key] = list(reversed(value))
        return rows

    presets.catalog = reversed_catalog
bundle = {'version':'1.0', 'cif_text':'data_test\nloop_\n_atom_site.group_PDB\n_atom_site.id\nATOM 1\n',
          'view':{'pymol_view':[1,0,0,0,1,0,0,0,1,0,0,-50,0,0,0,1,100,0],
                  'viewport_px':[800,600], 'fov_deg':20, 'orthoscopic':False}, 'dss':{'ran':True}}
decisions = [selection.select_preset(request, available_capabilities=engine.CAPABILITIES,
             policy=selection.SelectionPolicy(), bundle=bundle).to_dict() for request in (
    selection.SelectionRequest(),
    selection.SelectionRequest(visual_brief={'material_family':'dried_paint'}),
    selection.SelectionRequest(preset='pastel', hard_requirements={'width':640},
                              overrides={'camera':{'yaw_deg':15}}))]
assert attempts == [], attempts
print(json.dumps({'catalog':presets.compact_catalog(), 'decisions':decisions,
                  'import_origins_verified':True, 'boundary_attempts':attempts}, sort_keys=True))

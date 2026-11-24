"""Load the Windows renderer bridge from PyMOL's startup hook."""
import os
from pathlib import Path

from pymol import cmd

_checkout = Path(globals().get('__script__', __file__)).resolve().parent.parent
os.environ['MOL_BLENDER_HOME'] = str(_checkout / '.runtime')
cmd.run(str(_checkout / 'mol_blender' / 'pymol_bridge_plugin.py'))
print('mol-blender: Windows renderer ready (Blender 5.0.1).')

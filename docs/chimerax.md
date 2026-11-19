# Render from ChimeraX

The ChimeraX bridge exports the current ChimeraX scene to the same data-only
bundle the PyMOL bridge writes, then renders it with the configured
`mol-blender` CLI in a separate process. ChimeraX's own Python never imports the
renderer, and the renderer and bundle format are unchanged.

## Set up

Configure the renderer first (`mol-blender setup`, see the
[main README](../README.md#install-and-configure)). The bridge reads
`local_python` from `$MOL_BLENDER_HOME/config.json` (default
`~/.config/mol-blender/`), or uses `mol-blender` from `PATH`.

In ChimeraX, load the command from this checkout:

```
open /path/to/mol_blender/chimerax_bridge/molblender_cmd.py
```

`runscript` works too. Add that line to ChimeraX's startup commands
(Preferences → Startup) to have it in every session.

## Use

```
molblender [models] [preset NAME] [output PATH] [width W] [height H] [rough true|false]
           [dssp true|false] [transparent true|false] [timeout SECONDS]
molblender export PATH [models] [dssp true|false]
```

- `molblender rough true` renders a quick preview with the default preset and
  shows a thumbnail and the file path in the Log. Without `output`, the PNG is
  kept under the system temporary folder in `mol-blender-chimerax/`.
- `molblender export scene.json` writes the bundle only; render it later with
  `mol-blender render scene.json -o out.png`.
- The command blocks ChimeraX until Blender finishes, like PyMOL's `mol_blender`.

## What is exported

- All atoms of the displayed atomic models (or the models given), in scene
  coordinates, so model moves and rotations are kept. Representations, lighting
  and background come from the preset, as with PyMOL.
- Several models become one CIF data block; colliding chain IDs are renamed and
  recorded in `provenance.chain_mapping`.
- The camera: position, orientation, perspective or orthographic projection,
  field of view and window size. ChimeraX's horizontal field of view is
  converted to the vertical angle the bundle uses. Stereo, 360° and VR cameras
  are not supported; switch with `camera mono` or `camera ortho`.
- Secondary structure as ChimeraX shows it. `dssp true` recomputes it first,
  closer to PyMOL, which always runs `dss`; the session's assignment is restored
  afterwards. A file whose header annotates only some chains leaves the others
  as coil in ChimeraX and in the render; use `dssp true` in that case.
- Colors: each atom's color where the atom is drawn, otherwise its residue's
  ribbon color. Presets use them only with color following enabled.
- Phosphoresidue interchain hydrogen bonds from ChimeraX's `hbonds` criteria,
  which can differ from PyMOL's `find_pairs`.

Volume (density map) models are skipped with a warning: the bundle carries
atomic structures only.

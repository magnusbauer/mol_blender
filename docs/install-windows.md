# Native Windows local rendering

This experimental Windows x64 route supports synchronous local rendering from
the PyMOL `mol_blender` command. co-PyMOL background rendering, warm workers,
cluster operations and the editor IPC bridge are outside this support scope.

## Requirements and installation

Use Windows x64, uv on PATH, and an existing PyMOL environment containing a working
`pymol2`. Launch conda PyMOL through its activated environment so its DLLs are
available. No administrator installation or execution-policy changes are required.
Keep this checkout at its installed location. From PowerShell in the checkout:

```powershell
.\scripts\install-windows.ps1 -PymolPython 'C:\path\to\pymol-env\python.exe'
```

If exactly one working conda PyMOL is detected, `-PymolPython` may be omitted.
Multiple matches require an explicit path. The script creates a pinned Python
3.11 controller and `.runtime/runtime/` containing the checksum-verified Blender
5.0.1 portable runtime, hash-locked Python dependencies and verified CCD database.
It does not install packages into PyMOL's environment. The runtime retains all
original licenses; it is not MIT-only. Dependencies download from upstream.

Use `-Blender 'C:\path\to\blender.exe'` to supply an existing **5.0.1** runtime.
Blender 5.2 is not compatible with these pins. `-SkipRuntime` validates an existing
configuration without reinstalling renderer dependencies; controller sync still
runs. A first installation needs network access and several GB of free storage.

The default startup file is `$HOME/.pymolrc.py`; `-StartupFile` selects another
location. UTF-8 source is required. Unsupported encodings or malformed markers
fail without overwriting the file. Setup backs up changed files and replaces only
its marked block. Repeated setup does not duplicate hooks. Runtime validation
finishes before writing the hook. Failed installation may retain downloaded files
and configuration for diagnosis and reuse.

## Use

Restart PyMOL and check for `Windows renderer ready`. Load an enabled structure,
adjust its view and run:

```pymol
mol_blender width=640, height=480, samples=32
png figure.png
mol_blender preset=dried_paint_green, width=640, height=480, samples=32
```

Rendering blocks PyMOL until completion. Unnamed still previews are removed after
display. For persistent output choose a new filename:

```pymol
mol_blender output_dir=C:/path/to/renders, output_name=figure.png, save_blend=1
```

Logs and the editable `render.blend` are in `figure.render/`. Use matching Blender
5.0.1 for editing. Animation requires FFmpeg with GIF and libx264 encoders on PATH.
The verification helper also discovers FFmpeg in the selected conda environment.
Movie display inside PyMOL additionally needs Pillow in PyMOL's own interpreter;
without it, MP4/GIF export still works and file paths are returned. The installer
does not modify PyMOL's environment to add this optional dependency.

## Verification and removal

Run the helper with PyMOL's Python; every call creates a new output directory:

```powershell
& 'C:\path\to\pymol-env\python.exe' -X utf8 scripts/verify_windows_pymol.py
& 'C:\path\to\pymol-env\python.exe' -X utf8 scripts/verify_windows_pymol.py --cpu
& 'C:\path\to\pymol-env\python.exe' -X utf8 scripts/verify_windows_pymol.py --animation
.\scripts\install-windows.ps1 -Uninstall
```

Uninstall removes only the marked startup block and preserves unrelated user
settings, backups, runtimes and outputs. Use the same `-StartupFile` if customized.
After removing the hook, the installation directory can be deleted manually once
desired renders and presets have been retained. No recursive removal is automated.

## Support boundaries

Targeted Windows CI checks startup preservation, file/process behavior, local
orchestration and packaging. It does not establish GPU or desktop execution.
Real target-machine acceptance requires a PNG, manifest, view preservation,
desktop display and any requested movie artifacts. The development machine used
PyMOL 3.2.0a/Python 3.13 and an NVIDIA GTX 1050 Ti. Other PyMOL distributions and
GPU vendors require validation. CPU execution is supported by the shared renderer.

Windows Job Objects manage render descendants; CCD input rejects final-component
reparse points. Read-only attributes are not Unix permission/ACL equivalents.
Orderly cancellation and cleanup are tested; kill-on-owner-crash behavior is not
promised. Do not claim full Windows parity for background/editor/cluster tools.

# Windows local-rendering validation

Scope: synchronous local CLI/PyMOL rendering on Windows x64. This is an
experimental contribution candidate, not a claim of complete Windows support.

Executed locally:

- Real PyMOL 3.2.0a / Python 3.13 command invocation, view preservation and PNG
  return; desktop preview and unnamed-preview cleanup were observed.
- Blender 5.0.1 with Molecular Nodes 4.5.10, NumPy 1.26.4, Biotite 1.6.0 and the
  unchanged pinned CCD digest. Official Windows Blender archive SHA-256 checked.
- NVIDIA GTX 1050 Ti CUDA still/animation rendering. Explicit CPU rendering with
  Chinese characters and spaces in the output path.
- Four-frame rocking movie: PNG, MP4, GIF, lossless frames archive and `.blend`.
  PyMOL internal movie playback was unavailable because its interpreter lacks
  optional Pillow; exported files were generated successfully.
- Built source distribution and wheel; inspected license inclusion, Windows
  installer/document inclusion and exclusion of private runtime/validation data.
- Installed wheel into an independent environment, imported it with `-I` outside
  the checkout and completed an actual Blender render.
- Targeted file/process, startup lifecycle and renderer orchestration tests.

Not established locally: clean installation on a second machine, non-NVIDIA GPU
execution, other PyMOL distributions, remote GitHub Actions results, Linux/macOS
regression results, full preset coverage, interactive Blender editing, co-PyMOL,
warm workers, cluster operations or editor IPC. These are not implied by the
targeted tests. The newly added CI is a proposed check until run on GitHub.

The source installer reuses the upstream package name/version for development.
Do not upload this candidate to PyPI as an official upstream release. Keep all
upstream notices and review contribution requirements before publication.

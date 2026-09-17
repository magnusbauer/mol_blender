# Third-party software

The root MIT license covers the independently authored client, export, job,
gateway, and plugin code. The Blender Python API scripts listed below are
GPL-3.0-or-later, as marked in their file headers. The wheel contains both
components and declares `MIT AND GPL-3.0-or-later`.

GPL scripts: `blender_custom_render.py`, `geometry.py`, `paint.py`,
`runtime_initialize.py`, `runtime_probe.py`, `animation_blender.py`, and
`rocking_blender.py`, all under `blender_render/`.
The full GPL text is in `LICENSES/GPL-3.0-or-later.txt`. This separation follows
[Blender's published guidance for Python scripts](https://www.blender.org/support/faq/).
It does not relicense upstream dependencies, bundled node assets, or their
modifications. All render-side source remains available in the release.

| Component | License | Source |
| --- | --- | --- |
| Blender 5.0.1 | GPL-3.0-or-later | https://projects.blender.org/blender/blender |
| Molecular Nodes 4.5.10 and its node assets | GPL-3.0-or-later | https://github.com/BradyAJohnston/MolecularNodes |
| Open-source PyMOL | BSD-like, see upstream LICENSE | https://github.com/schrodinger/pymol-open-source |
| Biotite | BSD-3-Clause | https://github.com/biotite-dev/biotite |
| Biotraj 1.2.2 | LGPL-2.1-or-later | https://github.com/biotite-dev/biotraj |
| MDAnalysis 2.10.0 | LGPL-3.0-or-later; bundled contributions retain their notices | https://github.com/MDAnalysis/mdanalysis |
| GridDataFormats 1.1.0 | LGPL-3.0-or-later | https://github.com/MDAnalysis/GridDataFormats |
| Gemmi 0.7.5 | MPL-2.0 | https://github.com/project-gemmi/gemmi |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| tqdm | MPL-2.0 AND MIT | https://github.com/tqdm/tqdm |
| FastMCP | Apache-2.0 | https://github.com/jlowin/fastmcp |
| Modal Python SDK | Apache-2.0 | https://github.com/modal-labs/modal-client |

Runtime images contain GPL software. Their distribution must retain the relevant
licenses and corresponding source, including any modifications and build scripts.
The release source bundle includes our build scripts; runtime source archives and
license inventories accompany container releases. Do not describe the combined
runtime as MIT-only. Rendered images are not relicensed by this notice. A saved
Blender scene can contain Molecular Nodes node groups; those assets retain
their upstream license.

`blender_render/runtime-sources.json` and `service-sources.json` record exact
source versions, URLs, and checksums. `scripts/collect_sources.py` downloads and
checks those archives, collects their unmodified license files, and writes a
source manifest. The source-only cronsim wheel is included because that release
does not provide an sdist. Windows-only dependencies are not distributed in the
Mac/Linux runtime. PyPI license metadata is supplemented by the original notice
files; it is not a substitute for their terms.

The Linux runtime retains Debian package copyright files in `/usr/share/doc`,
and an exact binary/source-version inventory in `/opt/notices/debian-packages.tsv`.
The corresponding-source bundle and container build scripts accompany release
downloads. The client does not install Blender or Molecular Nodes until runtime
setup is explicitly requested.

Example structures are obtained from the RCSB Protein Data Bank and carry their
accession and citation in the example manifest. User-uploaded structures and
reference images are not included in the public source distribution.

Animation encoding uses the system FFmpeg executable with libx264. Container
builds install Debian's FFmpeg package, retain `/usr/share/doc` copyright files,
and record exact binary/source versions in the existing Debian inventory.
Corresponding FFmpeg and x264 sources must accompany distributed container
releases, using the same Debian snapshot as the build. FFmpeg/libx264 are not
embedded in the Python wheel. Pillow retains its upstream license and notices.

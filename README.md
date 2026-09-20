# Blender Render

Ray-traced molecular stills from PyMOL, with the saved pose, colors, and transparent background. Use the same Blender renderer on an Apple Silicon Mac, your lab's SLURM cluster, or Modal, and connect it to Codex or ChatGPT through MCP.

This is the **private 0.2.0b1 beta**. The repository and release artifacts remain private, and the hosted endpoint admits only the owner's GitHub account. Public publication, publisher identity verification, and OpenAI portal submission are deferred at the owner's request. The [validation report](docs/release-validation.md) distinguishes executed checks from remaining release gates.

The renderer pins **Blender 5.0.1**, **Molecular Nodes 4.5.10**, and **Python 3.11**. Preset 1 remains the default pastel style. `dried_paint_green` adds pigment variation, surface relief, and rounded ribbon edges. Output is a PNG plus a provenance manifest, with an optional `.blend` download. Animation and interactive 3D editing are outside this beta.

To extend the engine in a trusted checkout, use the [local developer workflow](docs/developer-workflow.md): edit Python and JSON, preview fresh source, compare images, and save named presets. The depth-of-field example includes separate Python-change and JSON-control checks.

## Preset gallery

Choose the identifier beneath an image: `blender_render preset=cyan_mint_cutaway` in PyMOL, or `blender-render render pose.pse --preset cyan_mint_cutaway -o pocket.png` from the CLI. Each name links to its editable JSON. All previews below are actual local Blender renders of public structures.

The ivory/lime image opens at full resolution; its [surface and DNA grid detail](examples/presets/ivory_lime_frosted_detail.png) shows the fine cells up close.

| | |
|---|---|
| **Powder blue rounded**<br>[<img src="examples/presets/powder_blue_rounded.png" alt="Powder blue rounded" width="320">](examples/presets/powder_blue_rounded.png)<br>[`powder_blue_rounded`](blender_render/style_preset_powder_blue_rounded.json)<br>8AW3 · reference 1 · structure analogue | **Cyan / mint cutaway**<br>[<img src="examples/presets/cyan_mint_cutaway.png" alt="Cyan / mint cutaway" width="320">](examples/presets/cyan_mint_cutaway.png)<br>[`cyan_mint_cutaway`](blender_render/style_preset_cyan_mint_cutaway.json)<br>8EA6 · reference 2 · colored pocket residues |
| **Pastel duplex, rounded**<br>[<img src="examples/presets/pastel_duplex_rounded.png" alt="Pastel duplex, rounded" width="320">](examples/presets/pastel_duplex_rounded.png)<br>[`pastel_duplex_rounded`](blender_render/style_preset_pastel_duplex_rounded.json)<br>7PZB · reference 3 | **Pastel duplex, cartoon**<br>[<img src="examples/presets/pastel_duplex_cartoon.png" alt="Pastel duplex, cartoon" width="320">](examples/presets/pastel_duplex_cartoon.png)<br>[`pastel_duplex_cartoon`](blender_render/style_preset_pastel_duplex_cartoon.json)<br>7PZB · reference 4 |
| **Vivid binding site**<br>[<img src="examples/presets/vivid_binding_site.png" alt="Vivid binding site" width="320">](examples/presets/vivid_binding_site.png)<br>[`vivid_binding_site`](blender_render/style_preset_vivid_binding_site.json)<br>1AZM · reference 5 · ligand analogue | **Ivory / lime frosted**<br>[<img src="examples/presets/ivory_lime_frosted.png" alt="Ivory / lime frosted" width="320">](examples/presets/ivory_lime_frosted.png)<br>[`ivory_lime_frosted`](blender_render/style_preset_ivory_lime_frosted.json)<br>7PZB · reference 6 · fine surface and DNA grids · structure analogue |
| **Mint / sky assembly**<br>[<img src="examples/presets/mint_sky_assembly.png" alt="Mint / sky assembly" width="320">](examples/presets/mint_sky_assembly.png)<br>[`mint_sky_assembly`](blender_render/style_preset_mint_sky_assembly.json)<br>8AA5 · reference 7 | **Neon duplex hero**<br>[<img src="examples/presets/neon_duplex_hero.png" alt="Neon duplex hero" width="320">](examples/presets/neon_duplex_hero.png)<br>[`neon_duplex_hero`](blender_render/style_preset_neon_duplex_hero.json)<br>7PZB · reference 8 |
| **Pastel (default)**<br>[<img src="examples/presets/preset_1_interface.png" alt="Pastel (default)" width="320">](examples/presets/preset_1_interface.png)<br>[`preset_1_interface`](blender_render/style_preset_1_opaque_pastel.json)<br>1SPS · original preset 1 | **Frosted blue**<br>[<img src="examples/presets/preset_2_frosted_blue.png" alt="Frosted blue" width="320">](examples/presets/preset_2_frosted_blue.png)<br>[`preset_2_frosted_blue`](blender_render/style_preset_2_frosted_blue.json)<br>1SPS · original preset 2 |
| **Pastel on black**<br>[<img src="examples/presets/preset_3_black_tubes.png" alt="Pastel on black" width="320">](examples/presets/preset_3_black_tubes.png)<br>[`preset_3_black_tubes`](blender_render/style_preset_3_black_tubes.json)<br>1SPS · original preset 3 | **Milky green**<br>[<img src="examples/presets/preset_4_milky_green.png" alt="Milky green" width="320">](examples/presets/preset_4_milky_green.png)<br>[`preset_4_milky_green`](blender_render/style_preset_4_milky_green.json)<br>1SPS · original preset 4 |
| **Black interface**<br>[<img src="examples/presets/preset_5_black_interface.png" alt="Black interface" width="320">](examples/presets/preset_5_black_interface.png)<br>[`preset_5_black_interface`](blender_render/style_preset_5_black_interface.json)<br>1SPS · original preset 5 | **Dried green paint**<br>[<img src="examples/presets/dried_paint_green.png" alt="Dried green paint" width="320">](examples/presets/dried_paint_green.png)<br>[`dried_paint_green`](blender_render/style_preset_dried_paint_green.json)<br>1SPS · original green paint |

Presets preserve your saved pose by default. Use `--no-preserve-view` (PyMOL: `preserve_view=0`) to use an authored preset camera. Exact framing depends on the input structure and view. References 1, 5 and 6 are style approximations with unconfirmed original structures. See [reference details and customization](docs/reference-presets.md) and [public gallery provenance](examples/presets/gallery.json).

## Choose compute

| Compute | Setup | Use |
|---|---|---|
| Mac | Install the wheel and run setup | `blender_render` inside PyMOL; Metal with CPU fallback |
| Own HPC | Add a named cluster profile | `blender_render mode=interactive, profile=ipd`; warm Apptainer worker with SSH transport |
| Shared Modal | Install the plugin and sign in with GitHub | Import, submit, inspect progress, preview, and download |
| Your Modal account | Deploy a personal service with an explicit budget | Connect its HTTPS MCP URL |

- [Mac installation and PyMOL](docs/mac.md)
- [SLURM, Apptainer, and a lab HTTPS gateway](docs/hpc.md)
- [Codex and ChatGPT plugin installation](docs/plugin.md)
- [Private plugin routing and marketplace packages](docs/plugin-installation.md)
- [Modal deployment and budget operation](docs/modal.md)
- [Troubleshooting](docs/troubleshooting.md)

## Quick start

After installing the release wheel's `server` extra in Python 3.11:

```sh
blender-render setup --install-runtime
```

Run the plugin path printed by setup in PyMOL, then:

```python
blender_render
blender_render preset=dried_paint_green, transparent_background=1, save_blend=1
```

For files on disk, configure `--pymol-python` with an interpreter containing `pymol2`, then:

```sh
blender-render render pose.pse --preset dried_paint_green --transparent --save-blend -o figure.png
```

PSE input preserves enabled objects, object transforms, the saved camera/projection/FOV, and PyMOL colors. CIF and PDB input receive a fitted initial view. Export is CIF-first and assigns secondary structure before rendering. Ligands use native Molecular Nodes sticks, with no legacy ball-and-stick fallback. Rough mode changes performance settings only.

## Jobs and provenance

The versioned API provides upload, immediate asynchronous submission, status, cancellation, results, and expiring downloads. Submission is idempotent; reconnect to the returned job ID instead of launching another render after a network interruption. One durable FIFO queue owns each service's Blender worker. Local and HPC services stop after 15 idle minutes.

Each manifest records input hashes and provenance, DSS status, structure format, selected geometry backends, resolved preset, runtime and GPU versions, camera settings, timings, and output checksums. Temporary run directories are cleaned by default.

The shared service allows ten completed renders per UTC day, reserving a daily slot for each pending job. Upload counts and daily GPU minutes have no quota. The owner's verified GitHub account is exempt from the daily limit during testing. Output defaults to 2048 pixels, with a 4096-pixel maximum dimension and a ten-minute job timeout. Worst-case costs are reserved before dispatch, including for the testing account; the lifetime budget still applies. Inputs are deleted after rendering, and unused uploads expire after five minutes. Results are deleted after download or fifteen minutes after completion. There is no retained structure library or public gallery. See [privacy](docs/privacy.md) and [terms](docs/terms.md).

## Development

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,server,mcp,modal]'
.venv/bin/python -m pytest
.venv/bin/python -m build
```

`blender_render` remains the only rendering implementation package. The separate `blender_render_plugin` package contains only the reviewed install templates and deterministic route packager. `bridge_runner`, `render_server`, and the PyMOL script remain compatibility entry points. The preset JSON schema and packaged runtime lock are shared by all backends. See [architecture](docs/architecture.md) and [release validation](docs/release-validation.md).

The examples use public PDB structures; accession, source URL, and SHA256 are recorded in `examples/structures.json` and `examples/presets/gallery.json`.

## License and support

Independently authored bridge/client code is MIT. Blender, Molecular Nodes, and other dependencies retain their licenses and source availability; the combined runtime is not MIT-only. [Dependency notices](THIRD_PARTY_NOTICES.md) describe the distributed source bundle and upstream licenses.

Report reproducible problems through [GitHub issues](https://github.com/magnusbauer/blender_render/issues), preferably with a public example and a reviewed manifest. OpenAI directory publication is deferred; private testing uses repository installation and the restricted MCP endpoint.

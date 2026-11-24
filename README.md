# mol-blender

https://github.com/user-attachments/assets/5ff92e61-912d-43a7-a87a-27d307a7ba9a

If you're tired of seeing AI slop proteins on social media, with branching chains
and loopy salad wrapped around them, start with an actual structure and give it a
look you like.

mol-blender turns your protein, DNA or RNA structure into polished images and
animations using Blender and Molecular Nodes. The molecular geometry comes from
your structure file, while you choose the colors, materials, lighting and camera.
Use the render as a finished figure, or bring it into the AI image generator of
your choice to make an illustration you love, starting from your actual protein
structure. To preserve the protein's geometry, keep the rendered protein intact
and let AI build the scene around it. If the generator redraws the protein, check
it against the original render.

Start with one of the 17 [built-in presets](#preset-gallery), from soft pastel
ribbons to frosted surfaces and vivid binding-site views. If you have a PNG with a
style you love, the [recreate-render-preset skill](#claude-code-and-codex) can use it
as a visual reference, refine the look through local previews and save a reusable
preset for your structures. Made a preset you love? Share it with the community 
by opening a pull request!

Work from PyMOL or the command line, or ask Claude Code or Codex to run the same
local tools for you. Export a transparent PNG for a slide or paper, save an editable
Blender scene, or use molblender-studio's six animation recipes to rotate your
structure, reveal a binding site or play through supplied molecular states.

Optional local MCP tools connect compatible assistants to the renderer. An
optional [shared cluster server](docs/shared-cluster.md) lets users submit from
PyMOL to Slurm and Apptainer using the same renderer and uv-managed runtime.
Installation does not start or deploy a server.

See the [documentation index](docs/README.md) for installation routes, user guides,
advanced setup and developer reference.

## Install and configure

Windows x64 users can follow the [native Windows local-rendering guide](docs/install-windows.md)
for an isolated runtime and reversible PyMOL startup integration. Windows support
currently covers synchronous local rendering, not co-PyMOL background jobs or editor IPC.

Want your AI agent to handle installation? Give it this repository and ask it to
follow [the setup instructions in AGENTS.md](AGENTS.md#installation-requests).
Tell it whether you want [co-PyMOL](docs/install-co-pymol.md) to control a live
PyMOL session or [molblender-studio in Claude](docs/install-claude.md) for the
Claude plugin. For example:

> Follow AGENTS.md to install co-PyMOL for Codex and verify the connection.

If your lab already has a renderer, point your agent to the
[cluster connection instructions](AGENTS.md#connect-to-an-existing-cluster).

Use Python 3.11 (matching Blender's Python ABI):

```sh
uv sync --locked --extra dev --extra mcp
source .venv/bin/activate
```

Omit `mcp` if you only need the CLI/PyMOL bridge. Rendering requires the versions in
`mol_blender/runtime.lock.json`: Blender 5.0.1, Molecular Nodes 4.5.10, NumPy
1.26.4 and Biotite 1.6.0, plus the pinned CCD database. PyMOL file import uses a
separate interpreter with `pymol2`; an existing PyMOL environment is suitable.
For isolated Studio environments and a Linux rebuild from the same pins, see
[runtime setup](docs/runtime-environments.md). Installations are explicit and
never triggered by viewing a structure.

To install the pinned Blender runtime into user-owned storage on Apple Silicon
macOS or Linux x86_64 (downloads Blender and Python dependencies):

```sh
mol-blender setup --install-runtime --pymol-python /path/to/pymol/python
```

To keep Blender, its dependencies and configuration inside this checkout, set the
configuration root before running setup:

```sh
export MOL_BLENDER_HOME="$PWD/.runtime"
mol-blender setup --install-runtime --pymol-python /path/to/pymol/python
```

That installs Blender at `.runtime/runtime/Blender.app` on Apple Silicon macOS.
Keep `MOL_BLENDER_HOME` set when rendering or starting MCP so the same local
configuration is used. `.runtime/` is excluded from Git.

Or configure an existing runtime without installing anything:

```sh
mol-blender setup \
  --blender /path/to/blender \
  --pymol-python /path/to/pymol/python \
  --runtime-path /path/to/python3.11/site-packages \
  --runtime-path /path/to/extensions/blender_org \
  --ccd /path/to/biotite/structure/info/components.bcif
mol-blender doctor
```

Each `--runtime-path` must be an existing directory supplying the pinned rendering
dependencies. If using a Molecular Nodes extension with bundled wheels, add
`--runtime-wheels /path/to/molecularnodes/wheels`; these are extracted into each
isolated render environment. The dependency directories must include `jsonschema`.
`setup` saves configuration; `doctor` is read-only. A successful render verifies
source hashes, runtime versions and the CCD digest before reporting completion.
Configuration lives in `~/.config/mol-blender/config.json`; set
`MOL_BLENDER_HOME` to use a separate configuration directory.

## Render locally

```sh
mol-blender presets
mol-blender render examples/1sps.cif --rough -o renders/example.png
mol-blender render structure.pse --preset dried_paint_green --save-blend -o renders/paint.png
mol-blender render structure.pse --style-json my-style.json -o renders/custom.png
mol-blender export structure.pse -o bundle.json
```

Inputs are trusted local `.pse`, `.cif`, `.mmcif`, `.pdb` or exported bundle `.json`
files. PSE loading can execute code; use sessions you trust. CIF/PDB imports get a
PyMOL-generated view. Sessions and bundles preserve their saved pose by default;
explicit camera fields in a style take precedence. `--no-preserve-view` opts into
the preset's camera. Rough mode changes only performance settings.

Each render writes the requested PNG plus a sibling `NAME.render/` directory with
`manifest.json`, `resolved-style.json`, source/runtime provenance and logs. Optional
Blender scenes are saved there as `render.blend`. Output names must be new. Failed
runs retain their logs; choose a new name after fixing the problem. Temporary source
snapshots are removed unless `--keep-temp` is requested.

## Preset gallery

Choose one of the featured presets below with `--preset ID` in the CLI or
`preset=ID` in PyMOL. `mint_sky_assembly` is the default. Click a preset name
for its complete editable JSON or a preview for the full image.

A preset controls style; your structure and saved PyMOL pose determine its
composition. Every gallery preview uses a public PDB structure. Some presets use specific chain or ligand selections, which may need adapting
for another structure. See [preset details](docs/reference-presets.md) and the
[gallery provenance](examples/presets/gallery.json) for sources and settings.

| Preset | Appearance | Preview |
| --- | --- | --- |
| [mint_sky_assembly](mol_blender/style_preset_mint_sky_assembly.json) | **Default.** Mint and sky protein assembly with purple and pink nucleic acids. | [<img src="examples/presets/mint_sky_assembly.png" alt="mint_sky_assembly preview" width="220">](examples/presets/mint_sky_assembly.png) |
| [powder_blue_rounded](mol_blender/style_preset_powder_blue_rounded.json) | Powder blue rounded protein with pink nucleic acid on black. | [<img src="examples/presets/powder_blue_rounded.png" alt="powder_blue_rounded preview" width="220">](examples/presets/powder_blue_rounded.png) |
| [cyan_mint_cutaway](mol_blender/style_preset_cyan_mint_cutaway.json) | Cyan and mint surfaces with clean pocket cuts and intact interior ribbons. | [<img src="examples/presets/cyan_mint_cutaway.png" alt="cyan_mint_cutaway preview" width="220">](examples/presets/cyan_mint_cutaway.png) |
| [pastel_duplex_rounded](mol_blender/style_preset_pastel_duplex_rounded.json) | Rounded green and blue proteins with pink DNA on black. | [<img src="examples/presets/pastel_duplex_rounded.png" alt="pastel_duplex_rounded preview" width="220">](examples/presets/pastel_duplex_rounded.png) |
| [pastel_duplex_cartoon](mol_blender/style_preset_pastel_duplex_cartoon.json) | Thin blue and green secondary-structure ribbons with pink DNA. | [<img src="examples/presets/pastel_duplex_cartoon.png" alt="pastel_duplex_cartoon preview" width="220">](examples/presets/pastel_duplex_cartoon.png) |
| [vivid_binding_site](mol_blender/style_preset_vivid_binding_site.json) | Macro ligand view with vivid element colors and teal cartoon context. | [<img src="examples/presets/vivid_binding_site.png" alt="vivid_binding_site preview" width="220">](examples/presets/vivid_binding_site.png) |
| [ivory_lime_frosted](mol_blender/style_preset_ivory_lime_frosted.json) | Ivory frosted grid shell, lime core and cyan nucleic acids. | [<img src="examples/presets/ivory_lime_frosted.png" alt="ivory_lime_frosted preview" width="220">](examples/presets/ivory_lime_frosted.png) |
| [neon_duplex_hero](mol_blender/style_preset_neon_duplex_hero.json) | Green protein and gradient nucleic acids against a pink-cyan background. | [<img src="examples/presets/neon_duplex_hero.png" alt="neon_duplex_hero preview" width="220">](examples/presets/neon_duplex_hero.png) |
| [cyan_candy_mesh](mol_blender/style_preset_cyan_candy_mesh.json) | Glossy cyan blobs, recessed blue mesh, and neon pink/yellow helical tubes. A/B surfaces, C/D tubes. | [<img src="examples/presets/cyan_candy_mesh.png" alt="cyan_candy_mesh preview" width="220">](examples/presets/cyan_candy_mesh.png) |
| [northern_nights](mol_blender/style_preset_northern_nights.json) | Teal-blue-violet ribbons, a mint peptide and glowing element-colored PTR. [Selection details](docs/reference-presets.md#northern-nights-bright-ribbons-and-dark-cutaway). | [<img src="examples/presets/northern_nights.png" alt="Northern Nights preview" width="220">](examples/presets/northern_nights.png) |
| [northern_nights_cutaway](mol_blender/style_preset_northern_nights_cutaway.json) | Rounded pocket cutaway, violet-blue binder and glowing PTR on black. [Turntable preview](examples/presets/northern_nights_cutaway_turntable.gif). | [<img src="examples/presets/northern_nights_cutaway.png" alt="Northern Nights cutaway preview" width="220">](examples/presets/northern_nights_cutaway.png) |
| [dried_paint_green](mol_blender/style_preset_dried_paint_green.json) | Textured matte green paint with rounded ribbon edges. | [<img src="examples/presets/dried_paint_green.png" alt="dried_paint_green preview" width="220">](examples/presets/dried_paint_green.png) |

```sh
mol-blender render structure.pse --preset pastel_duplex_rounded -o renders/duplex.png
```

Use the cyan candy style with `--preset cyan_candy_mesh` (or
`preset=cyan_candy_mesh` in PyMOL). Its selections are explicit: chains A/B form
the cyan surface, C the pink tube, and D the yellow tube. Adapt the JSON
selections for other structures. The [example preparation](docs/reference-presets.md#cyan-candy-mesh)
explains the public 1QJB preview; loading unmodified 1QJB does not create its C/D selections.

All presets also support `--animation 360` (turntable) and `--animation rock`. Use
`mol-blender presets` to include your own saved presets in the catalog.

## Turntable and rocking GIFs

Install FFmpeg with the `libx264` and GIF encoders on your PATH. On macOS:

```sh
brew install ffmpeg
```

Then render with any built-in or saved preset:

```sh
mol-blender render examples/1sps_pose.pse --animation 360 \
  --duration 6 --fps 24 --gif --width 640 --height 480 -o renders/spin.png
mol-blender render examples/1sps_pose.pse --animation rock --rock-angle 12 \
  --duration 3 --fps 24 --gif --width 640 --height 480 -o renders/rock.png
```

Each animation writes PNG, MP4 and lossless `.frames.zip` files beside the requested
PNG; `--gif` adds a looping GIF. `rock_angle=12` means ±12° (24° total swing).
The camera orbits around the structure using its initial up axis; the initial pose
is retained as the PNG and restored in an optional saved Blender scene. Movies
composite transparent pixels onto white; saved PNG frames retain alpha.

Re-encode saved frames without launching Blender:

```sh
mol-blender movie-export renders/rock.frames.zip -o renders/rock-again.gif
```

See [animation controls and limits](docs/animation.md), including PyMOL playback
and MCP usage. Animation takes one render per frame, so start with small dimensions
and low samples and increase `--timeout` for longer runs.

## PyMOL bridge

Run `mol-blender setup` from the installed environment first; this records the
Python used to run renders. In PyMOL, load the plugin using its absolute checkout path:

```pymol
run /absolute/path/to/mol_blender/mol_blender/pymol_bridge_plugin.py
mol_blender
png figure.png
mol_blender selection=(all), preset=dried_paint_green, output_dir=renders, output_name=paint.png
mol_blender style_json=/absolute/path/to/my-style.json, output_name=custom.png
```

The adapter exports enabled objects, transforms, colors, secondary structure and
camera, calls local Blender through the configured Python, then loads the PNG into
PyMOL. An unnamed local still behaves like PyMOL's `ray`: each call replaces the
displayed image and removes its temporary render files after loading. Repeat
`mol_blender` freely, then use PyMOL's `png figure.png` to save the current image.
An existing `render.png` in the working directory does not affect these previews.
If PyMOL cannot display a preview, its files are retained and their path is printed.

Supplying `output_name` or `output_dir` saves the PNG and its `.render/` folder.
Movie, saved-scene, `keep_temp=1` and `auto_preview=0` requests also keep
their files; those exports still require a new output name. Rendering is synchronous.
`width`, `height`, `samples`, `rough`, `save_blend`,
`color_follow`, `preserve_view`, `blender_exe` and `timeout` are optional arguments.
For animations, add `animation=360` (turntable) or `animation=rock`, `gif=1`, `duration`, `fps`
and (for rocking) `rock_angle`. Optional movie preview needs Pillow installed in
PyMOL's Python; for the repo-local Pixi environment:

```sh
pixi add --manifest-path .runtime/pymol/pixi.toml pillow
```

```pymol
mol_blender animation=360, gif=1, width=640, height=480, output_name=spin.png
blender_movie_export filename=spin-again.gif
```

Movie preview replaces PyMOL's current movie timeline when supported; `auto_preview=0`
keeps the timeline untouched. Headless/unsupported GUI contexts return saved movie
paths. `blender_movie_export` uses the latest verified frames or an explicit
`source=/path/to/rock.frames.zip`; FFmpeg must be on PyMOL's PATH.
For a shared cluster with open access, configure its URL once:

```sh
mol-blender cluster configure --profile lab --url https://render.cluster.example:8772
```

Use `--ca-file` if your administrator provides a private CA certificate. Services
with individual authentication use `--credential-file` instead of `--url`.
For a Linux GPU workstation, use the same client through
[SSH forwarding](docs/shared-cluster.md#gpu-workstation-over-ssh). A workstation
profile runs Apptainer on that host's GPU; the default Slurm profile submits to
the cluster. Both support repeated unnamed PyMOL previews.
Then explicitly select the profile in PyMOL:

```pymol
mol_blender cluster=lab
png figure.png
```

An unnamed cluster still waits for completion and replaces the displayed image,
just like a local preview. Repeat `mol_blender cluster=lab` after it finishes
to render the current view again. Successful previews remove their local temporary
files. Save the displayed image with PyMOL's `png` command.

With `wait=0`, submission returns immediately. A timeout, connection loss, or
interrupted display keeps the original receipt; repeating the same command resumes
that job, including after restarting PyMOL. The active preview is tracked per cluster
profile under the cluster configuration directory. An uncertain Slurm submission
never starts a replacement job. Confirmed failed, cancelled, or expired jobs keep
their receipts for inspection and allow a new preview on the next call.

Named exports retain their asynchronous submission and explicit fetch workflow:

```pymol
mol_blender cluster=lab, rough=1, output_name=cluster-preview.png
mol_blender_status JOB_ID, cluster=lab
mol_blender_fetch JOB_ID, cluster=lab, output_name=cluster-preview.png
```

Named submission returns a job ID; fetching loads the verified image or animation.
Omitting `cluster` keeps direct local rendering. See the
[shared cluster guide](docs/shared-cluster.md) for open or authenticated access,
cancellation, persistent service setup, Slurm configuration and reuse of the uv
runtime inside Apptainer. The gateway stays running; each Slurm job releases its
GPU allocation when it finishes. Direct local rendering can use your local GPU,
and starts a fresh Blender process by default. For repeated local renders, add
`warm=1` to reuse Blender between requests:

```pymol
mol_blender warm=1, width=640, height=480, samples=32, output_name=local-first.png
mol_blender warm=1, preset=dried_paint_green, output_name=local-paint.png
```

The worker shuts down after 15 idle minutes. Use `mol-blender worker status`
and `mol-blender worker stop` from the configured terminal environment to
inspect or stop it. CLI rendering accepts `--warm`; see the
[warm local worker guide](docs/local-worker.md) for configuration, lifecycle and
Apptainer usage. Each Slurm render request still gets a separate job; an animation
keeps its frames within that job. Choose either `warm=1` or `cluster=lab`.
Legacy service/profile/remote arguments are unsupported.

## PyMOL agent (co_pymol)

`co_pymol` runs an MCP server inside PyMOL. Claude Code, Codex or Cursor can then
drive the live session: `run`, pLDDT colouring, metrics, triage and PyMOL images.
The same server renders the current view with this checkout's Blender renderer:
`mol_blender`, `mol_blender_batch`, `blender_status`, `blender_result`,
`blender_cancel` and `blender_presets`. Renders run in the background and save
`scene.pse` beside each PNG.

Install it into PyMOL's own Python, then configure a client:

```sh
/Applications/PyMOL.app/Contents/bin/python -m pip install --user -e ".[pymol]" --ignore-requires-python
/Applications/PyMOL.app/Contents/bin/python -m co_pymol setup claude
```

Optional settings live in `~/.config/co-pymol/blender.json`:

```json
{"backend": "cluster", "cluster_profile": "lab", "output_directory": "/absolute/renders"}
```

`backend` is `local` (default) or `cluster`. In chat, ask for "on the lab GPU" or
"locally" to override the default for a single render. Lab renders use the
`mol-blender cluster connect --profile lab` connection. Run that in a terminal
first, since SSH and MFA prompts stay with you.

## Shared render defaults

[`render_defaults.yaml`](render_defaults.yaml) is the shared source for image
preview quality, CPU caps, animation FPS, recipe timings and animation limits.
After editing it, run `python scripts/sync_render_defaults.py`; use `--check` to
verify that the generated data is current. Wheel builds reject stale data, and
molblender-studio packages the same values. PyMOL reads the data without importing
the renderer. PyYAML is needed only by the authoring/build tool.

Preset-specific output settings and explicit request options keep their existing
precedence. The `limits` section defines enforced bounds, not request overrides.
Recipe timings and the shorter legacy camera orbits have separate entries so
centralizing their settings preserves their existing behavior.

PyMOL and molblender-studio also share the recipe functions in
[`co_pymol/scene/animation.py`](co_pymol/scene/animation.py) and PyMOL camera/capture
helpers in `co_pymol/scene/`. All six named recipes produce the same camera
paths, molecular-state indices and cutaway opacity for the same inputs. Studio
imports these helpers from its bundled wheel; source checkouts use the sibling
package directly. The PyMOL preview maps cutaway opacity to its ghost-shell
display. Local rendering, cluster submission and each client's job workflow
continue to use their existing entry points.

## Package boundaries

`co_pymol` and `mol_blender` are sibling packages, shipped together. Scene
preparation has an explicit home in each:

- `co_pymol/scene/`: `session.py` owns PyMOL access, `camera.py` measures camera
  keys, `animation.py` compiles shared recipes, and `capture.py` freezes scene
  payloads. `preview.py` produces native PyMOL images and movies. molblender-studio
  reuses these modules directly; importing them does not start the MCP server.
- `co_pymol/render/`: `client.py` manages render requests and results, `worker.py`
  runs in the renderer environment, and `gallery.py` builds preset contact sheets.
  MCP image wrappers and tool registration stay in `co_pymol/tools/`.
- `mol_blender/scene/`: `export.py` converts PyMOL scenes to data-only payloads,
  `capture.py` records saved Blender scene metadata, and `edit.py` extracts preset
  edits. The exporter also runs standalone inside PyMOL without importing the
  renderer package.

Execution still enters through `mol_blender.local.render_payload` or the
existing cluster client. `mol_blender/animation.py` validates and encodes
renderer animations; the shared recipe compiler in `co_pymol/scene/animation.py`
produces their camera paths. Only the detached worker imports renderer execution
code from the PyMOL client. The renderer's identity and private snapshots cover
its scene subpackage without including the sibling client package.

## Claude Code and Codex

Open this checkout in either agent and use the CLI above. The repository includes
`recreate-render-preset` in `.agents/skills/` and a linked entry in `.claude/skills/`.
Example request:

> Use recreate-render-preset to match reference.png on structure.pse. Save the full
> style JSON, compare local previews, and register it as user/my-reference.

The skill inspects the actual image, records observable features, generates a
validated starting JSON, refines it through local renders, then saves and reloads
the preset. It explicitly reports remaining mismatches. See the
[skill](.agents/skills/recreate-render-preset/SKILL.md).

Optional MCP configuration, using the absolute path to this environment:

```sh
codex mcp add blender-local -- /absolute/path/to/mol_blender/.venv/bin/mol-blender mcp
claude mcp add --transport stdio blender-local -- /absolute/path/to/mol_blender/.venv/bin/mol-blender mcp
```

The four tools are `presets`, `inspect_style`, `render_structure` and `read_render`.
`render_structure` accepts `animation`, `duration`, `fps`, `rock_angle`, `gif`,
`width`, `height`, `samples` and `timeout`. Rendering returns local artifact paths;
`read_render` returns the PNG as MCP image
content for inspection. Configure a client tool timeout appropriate for a render
(up to the default 600 seconds). The MCP process uses the same user's local config
and never opens an HTTP listener. Client registration is optional and is not done
by installing this package. These commands follow the official
[Codex MCP](https://developers.openai.com/codex/mcp/) and
[Claude Code MCP](https://code.claude.com/docs/en/mcp) documentation; repository skill
locations follow [Codex skills](https://developers.openai.com/codex/skills/) and
[Claude Code skills](https://code.claude.com/docs/en/skills).

To edit an open Blender scene through chat, use the optional `mcp --editor`
tools and attach the bridge to that window. Chat discovers existing sessions
with `editor_sessions` and keeps the selected `session_id` for edits and
reconnection. Another desktop window opens only when you explicitly request it.
You can change materials, lights and cameras, adjust supported animation loops,
switch to a fast color view, play or pause, see background previews, save the
complete `.blend` or a reusable preset, and trigger a final render. Manual edits
are detected before chat applies changes, and unsaved work stays in the selected
window. When explicitly opening a new scene, `edit_bridge=1` in PyMOL or
`--edit-bridge` on the CLI enables its bridge. See [chat editing](docs/chat-editing.md)
for setup and example conversations.

## Preset authoring

You can also make a preset by editing in Blender. Start from a look you like:

```pml
mol_blender preset=mint_sky_assembly, open_blender=1, output_name=edit.png
```

After changing materials, lights or the background, use **File → Save As** in
Blender, then save the supported changes as a reusable preset from PyMOL:

```pml
blender_preset_from_blend /path/to/edited.blend, user/my-look
mol_blender preset=user/my-look, output_name=my-look.png
```

The terminal equivalents are `render --open-blender` and
`dev from-blend edited.blend --preset user/my-look`. Camera capture is optional;
unsupported geometry or shader edits are reported before a preset is saved.
See [editing in Blender](docs/blender-editing.md) for supported controls, camera
choices, saved preset revisions, and use with local or Slurm rendering.

For JSON and reference-image authoring:

```sh
mol-blender dev catalog
mol-blender dev inspect pastel
mol-blender dev schema
mol-blender dev reference reference.png --observations observations.json \
  --style-output candidate.json > decision.json
mol-blender dev validate candidate.json
mol-blender dev save user/my-reference candidate.json \
  --description 'My reference style' --provenance decision.json
mol-blender render structure.pse --preset user/my-reference -o renders/saved.png
```

`dev reference` records agent-supplied observations; it is not an automatic image
recognizer. `--style-output` emits a complete style document and refuses to overwrite
an existing file. A saved catalog preset is a metadata envelope; use its ID with
`--preset`, or extract its `style_doc` for `--style-json`. Save defaults to
`--camera-policy preserve_input`; choose `authored` deliberately for a reusable
camera composition. `dev preview --help` exposes explicit runtime paths and source
snapshots for renderer development. Built-ins cannot be overwritten.

## Development and license

```sh
python -m pytest
python -m build --wheel
```

Developers embedding completed images in an authenticated host can use
the optional [delivery adapter API](docs/hosted-adapter.md). Public example
structures and citations are in `examples/structures.json`. The client code is MIT;
Blender API scripts and Molecular Nodes assets retain their GPL terms. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

CI runs tests, packaging, secret scanning and dependency-advisory checks on pull
requests and weekly. See [repository maintenance](docs/repository-maintenance.md)
for local commands and administrator settings, and [SECURITY.md](SECURITY.md)
for vulnerability reporting and execution boundaries.

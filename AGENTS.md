# Local molecular renderer

## Installation requests

Choose the route from the user's intended workflow. Run the selected guide's
commands and verify the result; do not stop at giving the user a list of commands.
Reuse this checkout and existing configuration. Resolve machine-specific paths
from the installed tools, and configure only the requested client.

### Install through co-PyMOL

For controlling a live desktop PyMOL session from Codex, Claude Code or Cursor,
follow [the co-PyMOL install guide](docs/install-co-pymol.md). Install `.[pymol]`
into PyMOL's own Python, then run `python -m co_pymol setup CLIENT` with that same
interpreter. Blender rendering uses the separate Python 3.11 environment. Verify
a real PyMOL tool response after setup; client registration alone is not enough.

### Install the Claude plugin

For Structure Studio's file-based viewer, sessions and renders in Claude, follow
[the Claude install guide](docs/install-claude.md). Build the plugin with
`scripts/build_plugin.py`: the source marketplace alone omits its renderer wheel.
Use the staged marketplace for Claude Code, the plugin ZIP for Claude's plugin
upload, or the skill ZIP for its skill upload. Run `studio.py doctor` in the
target Claude environment and finish the requested runtime setup there. A local
Claude Code install does not install into the user's Claude web account.

## Connect to an existing cluster

For an authorized lab render or recovery, reuse the selected profile (`lab` in
the examples) and the client's installed `blender-render` executable. In this
checkout, use `.venv/bin/blender-render` if it is not on `PATH`. Keep local
rendering as the default unless the user requested the cluster.

Preserve `BLENDER_RENDER_HOME` for the renderer's presets/runtime. Lab profiles
are under `BLENDER_RENDER_CLUSTER_HOME`, falling back to `BLENDER_RENDER_HOME`
and then `~/.config/blender-render/`. co-PyMOL and Studio normally use the shared
default lab folder independently of their runtime; respect co-PyMOL's configured
`cluster_home` override. Check the correct profile root before treating a
connection as missing. Use the CLI's summaries rather than printing credential
files.

Inspect the saved SSH connection first:

```sh
blender-render cluster connection --profile lab
```

A running gateway can have a closed local tunnel. If the saved connection has a
closed tunnel or a gateway error, try reconnecting once with its saved settings,
then verify the gateway responds:

```sh
blender-render cluster connect --profile lab
blender-render cluster info --profile lab
```

`connect` uses noninteractive SSH and can reuse existing authentication. Ask the
user to reconnect in their terminal only if that attempt needs login/MFA or
fails with an actionable connection problem; never request or handle MFA codes.
For a URL/credential-only profile, `connection` returns `null`: use `cluster
info` directly, without inventing SSH settings. First-time client configuration
uses the administrator-provided URL/credential or SSH alias, pinned node,
controller and site-config paths in the [cluster guide](docs/shared-cluster.md).
Setting up a new Slurm service is a separate task covered by
[cluster-onboarding](.agents/skills/cluster-onboarding/SKILL.md).

After a connection loss, recover the saved receipt through `blender-render job
status RECEIPT` and `blender-render job fetch RECEIPT --wait 900`. Keep a local
receipt distinct from gateway acceptance and GPU execution. If observation
reports `prepared`, resend only through `jobs.submit`/`job submit` to the same
output with the same request ID; never create a replacement job to repair a
connection. Leave `submission_unknown` for administrator reconciliation.

Use the job API/CLI for render operations and its existing connection leases.
It connects once and closes only a tunnel it opened when no other client holds
a lease. Preserve pre-existing/shared tunnels; do not use disconnect or credential
revocation as a generic recovery step. Connection checks do not establish that
a GPU job ran: report gateway connectivity and render status separately.

## Development constraints

This branch supports PyMOL export, direct local Blender rendering, preset authoring,
and optional stdio MCP for Claude/Codex. Keep one shared renderer in
`blender_render/blender_custom_render.py`. CLI, PyMOL and MCP use `local.py` and the
isolated `developer.preview` runner. The optional `blender_render.hosted` adapter
presents already-authorized outputs; keep service queues and remote execution out
of the renderer. Import hosted dependencies only when explicitly selected.
The optional `blender_render.cluster` service owns authentication, the private job
spool and Apptainer submission through Slurm or the explicit Linux workstation
scheduler (systemd user units), or the explicit `warm` scheduler's persistent
container. Workers call the same `local.py` renderer. The warm HTTP backend uses
the existing spool and `cluster.worker`, one active request at a time; keep its
private control socket data-only and retire the entire service on cancellation.
Keep local rendering the default; never accept executable session uploads or
client-supplied paths/commands on the cluster API. Reuse uv runtimes built from
the shared pins; Linux environments must stay at their original absolute paths.
The explicit local `--warm`/`warm=1` option may reuse an identical, privately
captured Blender process through `warm_worker`. Keep source/runtime identity
checks, bounded local IPC and idle shutdown. Fresh local rendering remains the
default; Slurm jobs keep one independent Blender process per submitted request.

- Every client submits through one job API, `blender_render/jobs.py` (submit,
  observe, wait, fetch, cancel), exposed to processes that must not import the
  renderer as `blender-render job … --json`. A lab run is identified only by the
  receipt the client writes before uploading; recovery observes it by request ID,
  and an upload the gateway never received is sent again only under that same
  request ID (`jobs.submit` to the same output), never as a new job. Lab commands
  connect once with the saved profile and close only a tunnel they opened and
  nobody else holds a lease on. `jobs.poll` is the only poll loop; do not add
  another poll loop, job record or launcher.
- `co_pymol/` is the PyMOL-side MCP server; it runs in PyMOL's Python (3.10+) and must
  not import the renderer. Its detached `render/worker.py` runs in the render
  environment and calls `blender_render.jobs`. Keep it a
  sibling of `blender_render/` so the engine inventory is unaffected. Test it with
  PyMOL's Python: `python -m pytest tests/co_pymol`.
- Every PyMOL capture goes through `build_render_payload`, which restores the
  session's secondary structure and camera itself. A client that prepares a
  session (co-pymol's snapshot, Studio's styled `.pse`) exports its scene bundle in
  that same PyMOL run and hands the renderer the JSON, never the session to reload.
  A plain trusted file the client did not change (`studio.py render x.cif`,
  `blender-render render x.pse`) is exported once by the renderer's `export_file`.
- Keep the pure recipe compiler in `co_pymol/scene/animation.py` and reuse its
  PyMOL `scene/camera.py`, `scene/capture.py` and `scene/ligands.py` helpers (and
  `co_pymol/defaults.py`) from Structure Studio. Studio reads these helpers and
  their data from the bundled wheel; do not copy their algorithms or data files
  into plugin scripts or assets.
- `BLENDER_RENDER_HOME` is a client's renderer configuration (runtimes, presets);
  lab profiles may live apart under `BLENDER_RENDER_CLUSTER_HOME`, so a lab render
  resolves presets exactly like a local one.
  Compiled paths enter the existing renderer payload. PyMOL's ghost-shell
  preview is a display mapping of the same cutaway opacity timeline.
- Group host scene operations under `scene/` in both sibling packages. Keep
  PyMOL's render client, detached worker and gallery in `co_pymol/render/`.
  Scene helpers return data; MCP wrappers belong in `co_pymol/tools/`. Preserve
  standalone exporter loading and the renderer's source identity when moving files.
- Export CIF first and assign secondary structure in PyMOL before export.
- Preserve enabled selections, transforms, colors and the saved camera by default.
- Keep `mint_sky_assembly` as the default, native Molecular Nodes ligand sticks, and rough
  mode limited to performance settings. Reject unsupported JSON fields.
- Render trusted local files only. PSE files can execute code during loading.
- Use `.agents/skills/recreate-render-preset/SKILL.md` for reference-image-to-preset
  requests. Keep the original reference separate and available throughout. Compare
  the whole image every round, but change one visual feature at a time. Judge each
  candidate against the reference and the best previous render, and preserve the
  render and settings for features the user has approved.
- Use `.agents/skills/cluster-onboarding/SKILL.md` to configure the cluster service
  for a new Slurm site. Keep site differences in `server.json`, not launcher code.
- Write manifests and resolved styles; clean temporary source snapshots unless
  debugging retention was requested. Never commit research data or local paths.
- Saved Blender scenes carry data-only preset editing metadata. Disable embedded
  script execution when opening/importing them, report unmapped edits, and keep
  camera capture explicit. Desktop editing stays outside local/Slurm workers.
- The optional `editor` bridge accepts bounded, typed control changes on a
  revision-checked desktop scene over private local IPC. Keep bpy on the main
  thread, use captured copies for previews, and never expose code execution or
  desktop commands through the cluster API. Disconnect leaves the editor open.
- Reuse the selected Blender editor session for all scene edits. Never launch
  another desktop Blender instance unless explicitly requested. Preserve unsaved
  edits. If an operation is unsupported, add a bounded bridge control or explain
  the limitation; do not create and open a replacement scene.
- Discover existing windows with `editor_sessions`, retain the selected
  `session_id`, and reconnect that session when needed. `editor_open` is only for
  an explicit request for another window. Background preview renders may run
  separately without opening desktop windows.
- Run `python -m pytest` for relevant changes. For packaging changes, inspect the
  source distribution and check a built wheel outside the checkout. Report
  actual checks and limits in the pull request;
  mocks do not establish Blender or host integration. Store detailed logs,
  deployment records and research-specific evidence in ignored `.validation/`
  or private storage outside the public repository. Add public guides to
  `docs/README.md` and the explicit documentation list in `MANIFEST.in`.
- Preserve upstream notices. Make coherent commits and respect the user's
  publication boundary.

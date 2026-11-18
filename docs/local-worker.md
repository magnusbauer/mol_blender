# Optional warm local GPU worker

Use the warm worker when you want to make several renders on the same computer.
It keeps Blender, its Python imports and GPU process alive between requests. Each
request still rebuilds the scene from the exported structure and validated style;
there is no image-result cache. The first render can still incur GPU initialization.

The ordinary local renderer remains the default. Slurm remains a separate option:
each submitted render gets one Slurm job, and an animation runs all its frames
inside that job. Status checks and downloads do not submit additional jobs.

## From PyMOL

Configure the local runtime and load the bridge as described in the
[README](../README.md#pymol-bridge).

If PyMOL had the bridge loaded before this update, restart PyMOL and load the
updated bridge so its imported Python helpers recognize the new option.

Explicitly choose `warm=1`:

```pymol
blender_render warm=1, width=640, height=480, samples=32, output_dir=renders, output_name=first.png
blender_render warm=1, preset=dried_paint_green, output_dir=renders, output_name=paint.png
blender_render warm=1, animation=360, duration=2, fps=12, gif=1, output_dir=renders, output_name=spin.png
```

The first request starts the worker; subsequent requests reuse it. Rendering is
synchronous and the bridge presents the completed PNG or animation as usual.
Use a new output name for each render. Omit `warm`, or use `warm=0`, for a fresh
local Blender process. Use `cluster=lab` for a remote gateway; combining a cluster profile
with `warm=1` is rejected before exporting or submitting anything.

The worker runs on the controller's computer, under the same user and
configuration. This can be a server when these commands are run there. To reuse
Blender through a cluster profile and HTTP, configure the separate
[warm gateway backend](shared-cluster.md#warm-http-gateway). The Slurm and
workstation schedulers still start fresh Blender for each remote request.

## From the command line

Use the configured uv controller environment:

```sh
blender-render render structure.cif --warm --width 640 --height 480 --samples 32 -o renders/first.png
blender-render render structure.cif --warm --preset dried_paint_green -o renders/paint.png
blender-render worker status
blender-render worker stop
```

You can start Blender ahead of the first request, or change the idle limit when
starting a new worker:

```sh
blender-render worker start --idle-timeout 900
```

The default is **900 seconds (15 minutes)** after the last render. Status polling
does not extend this timer, and active renders do not expire from inactivity.
`--idle-timeout 0` keeps the worker running until stopped. Starting an already
running, compatible worker returns its existing status and idle limit. To change
that limit, stop it first. `worker stop` refuses to interrupt active work;
`worker stop --force` explicitly interrupts the active render and releases Blender.

Start, status, stop and render must use the same `BLENDER_RENDER_HOME` when a custom
configuration root is selected. The CLI start command also accepts `--blender`,
`--runtime-path`, `--runtime-wheels` and `--ccd` for an explicitly configured
runtime. Later renders must select the same runtime. The stdio MCP
`render_structure` tool accepts `warm=true` as the same opt-in.

On a shared filesystem, use a separate `BLENDER_RENDER_HOME` for each computer or
container session. The worker's socket and process state belong to that local
session; the pinned Linux runtime itself can still be shared read-only.

## Runtime reuse and verification

The worker reuses the existing native Blender or Studio uv runtime. It installs
nothing and does not change dependency pins. Linux and macOS environments remain
separate, as described in [runtime setup](runtime-environments.md). The default
style requests an available GPU; set `renderer.require_gpu` to `true` in a style
to fail if a GPU is unavailable. The manifest records the device actually used.

The worker captures package source and the pinned CCD privately at startup. A
change to package source, configured Blender, runtime paths, bundled wheels, CCD
or compute-allocation settings requires an explicit stop before reuse. Restart
after changing installed runtime dependencies too. New structures, styles and
camera positions are ordinary requests and do not require restarting.

`worker status` reports the Blender PID, worker ID, completed-request count and
idle limit. Each warm render's artifact directory contains `manifest.json` with
`warm_worker.id`, `pid`, `request_index` and `reused_process`; `preview.json` also
contains worker timing. Two completed requests with the same ID/PID and increasing
indices establish process reuse. Performance depends on scene construction,
render settings and hardware; a warm process does not remove those costs.

One worker serves one request at a time. A concurrent render is rejected as busy
without being queued or submitted elsewhere. A failed or timed-out Blender
process is retired. The request is not automatically replayed or sent to Slurm.
The next explicitly requested warm render can start a new worker. Source
snapshots are removed on normal or idle shutdown; per-request snapshots are
removed after rendering unless `--keep-temp` was requested. Diagnostics from the
most recent worker are in `BLENDER_RENDER_HOME/warm-worker/worker.log`.

Communication uses a private Unix socket and bounded JSON, accessible only to
the local user. It opens no network port and adds no shared-server authentication
mode. PSE files still execute code when PyMOL loads them: use trusted sessions.

## Inside Apptainer on Linux

A warm worker can also run inside the pinned Linux image, using its existing uv
interpreter. Keep the worker and its requests in the **same container session**,
with a writable private configuration directory. For an image built from this
revision, a small batch can use:

```sh
apptainer exec --cleanenv --nv --bind "$PWD:/work" renderer.sif sh -eu <<'SH'
mkdir -p /work/warm-config
test -f /work/warm-config/config.json || cp /opt/studio-runtime/blender-render/config.json /work/warm-config/config.json
export BLENDER_RENDER_HOME=/work/warm-config
blender-render worker start --idle-timeout 900
trap 'blender-render worker stop --force' EXIT
blender-render render /work/structure.cif --warm -o /work/first.png
blender-render render /work/structure.cif --warm --preset dried_paint_green -o /work/paint.png
SH
```

This is local execution on that Linux host. For Slurm, use the
[shared cluster service](shared-cluster.md); it continues to start and release an
independent allocation for each render request. Reusing an existing Linux Studio
runtime through a read-only bind at its original absolute path is also supported;
keep the worker's configuration, socket and temporary files on writable storage.
For a long-lived server session, use `worker start --idle-timeout 0` and keep that
container session running. The warm worker then stays available until explicitly
stopped. For the forwarded HTTP job API, use the warm gateway backend instead;
it owns its container and worker lifecycle.

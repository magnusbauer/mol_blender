# Optional shared cluster rendering

The same renderer can run locally or through a shared cluster service:

```text
PyMOL / CLI → HTTPS gateway → Slurm → Apptainer → existing local renderer
                 ↑                                  ↓
                 └──── status and verified downloads ┘
```

The gateway runs on a cluster service host with Slurm client tools. It accepts
exported structure data and validated styles, submits one job, and serves the
results. Token authentication gives each user a private queue. An administrator
can explicitly select open access for a trusted cluster network, with one shared
queue and no credentials. It never renders in the web process. Local `blender_render`
and `blender-render render` keep their existing behavior unless a cluster is
explicitly selected. The optional stdio MCP and hosted image-delivery adapter
remain separate interfaces.

For an existing service, start with [client setup](#use-from-pymol),
[job submission and recovery](#one-job-command-for-every-client), or
[a saved SSH gateway](#your-own-gateway-over-ssh). Ask your administrator for the
service URL/credential or the SSH settings; local rendering needs none of these.

Administrators setting up a new server should follow
[build the image](#build-once-reuse-the-uv-environments),
[configure the service](#configure-the-service), then
[install the persistent user service](#persistent-user-service-and-shared-clients).
The configuration and systemd templates are included in this repository.
For a Linux desktop GPU without Slurm, see [GPU workstation over SSH](#gpu-workstation-over-ssh).

## GPU use and warm starts

The gateway stays running between submissions. With the default Slurm scheduler,
each submitted render gets its own
Slurm job, Apptainer invocation and fresh Blender process. When the job finishes,
its GPU allocation ends. The image and pinned uv environments are reused on disk;
Blender and its GPU context are not retained between jobs. Increasing a job's
walltime does not keep a completed worker running.

| Mode in this branch | Where rendering runs | Blender lifetime |
| --- | --- | --- |
| Direct local rendering | The local computer, using its configured Blender runtime and available GPU | A fresh process for each render request |
| Optional warm local rendering | The same local computer and configured runtime | One process reused until stopped or its idle limit expires |
| Shared cluster rendering | A Slurm compute node inside Apptainer | A fresh process for each submitted job |
| Workstation gateway | The Linux gateway host's selected GPU inside Apptainer | A fresh process for each submitted job |
| Warm HTTP gateway | The Linux gateway host inside one persistent Apptainer container | One Blender process reused across successful jobs until stopped |

Local GPU rendering remains available without a server. After configuring the
local runtime as described in the [README](../README.md#install-and-configure),
omit `cluster` in PyMOL:

```pymol
blender_render selection=(all), width=384, height=288, samples=8, output_dir=renders, output_name=local-test.png
```

The default preset requests GPU rendering and detects an available backend.
Local rendering can fall back to CPU; the artifact directory's `manifest.json`
records the actual device in `cycles_device`. A style can set
`renderer.require_gpu` to `true` to require a GPU.

For repeated local renders, explicitly add `warm=1` in PyMOL or `--warm` in the
CLI. This keeps a private local Blender worker alive between requests, with a
15-minute idle shutdown by default. See [the warm local worker guide](local-worker.md)
for start/status/stop commands, source checks and Apptainer usage. The cluster
profile selects the gateway's configured scheduler, and cannot be combined with `warm=1`.

Each Slurm render submission creates one job. An animation renders all its frames
inside that job; status polling and fetching results do not create jobs. The
gateway has no persistent GPU allocation. Reusing the uv environment alone is
not a warm Blender process.

The existing warm worker can stay running on a configured server with
`blender-render worker start --idle-timeout 0`. Subsequent server-side
`blender-render render ... --warm` calls reuse it. Start and render must use the
same configuration and, for Apptainer, the same container session; see
[the warm worker guide](local-worker.md#inside-apptainer-on-linux).
The Slurm and workstation schedulers still start fresh Blender for each request.
For process reuse through HTTP and `cluster=lab`, the administrator selects the
[warm gateway backend](#warm-http-gateway).

## Warm HTTP gateway

Set `"scheduler": "warm"` in a copy of
[workstation-server.json](../examples/workstation-server.json). Choose a **new
private spool**, a pinned SIF and matching controller built from this revision.
Keep `limits.max_active_total` at `1` and `limits.render_timeout` at least `30`.
The Linux, systemd user manager, Apptainer, CUDA device selection and CPU/memory
requirements are the same as for the [workstation backend](#gpu-workstation-over-ssh).
Use `gpus: 0` for CPU or `gpus: 1` with the administrator's `gpu_index` for CUDA.
This backend runs on the gateway host; it does not acquire a Slurm allocation.

On the server, using the controller environment:

```sh
blender-render cluster doctor --config /srv/blender-render/warm/server.json
blender-render cluster warm start --config /srv/blender-render/warm/server.json
blender-render cluster warm status --config /srv/blender-render/warm/server.json
blender-render cluster serve --config /srv/blender-render/warm/server.json
```

`warm start` is optional: the first accepted job starts the service if needed.
Explicit startup pays Blender's import cost before accepting HTTP work. The
first render may still initialize CUDA kernels. A single systemd user service
keeps the container and private warm Blender worker alive without an idle
timeout. Subsequent successful jobs rebuild their scenes in that process;
images are never returned from a result cache. Render manifests record
`warm_worker.id`, `pid`, `request_index`, and `reused_process` to verify reuse.

Use the existing authenticated HTTP endpoint, credentials and job operations.
`GET /v1/info` reports `scheduler: "warm"`; request schemas are unchanged. The
existing SSH `cluster connect` command also accepts this server configuration.
It creates a per-connection spool, so its warm service is separate from the
base configuration's service. To prestart, inspect or stop that worker on the
server, pass the connection directory's generated `server.json` to `cluster warm`.
In PyMOL the command remains:

```pymol
blender_render cluster=lab
```

Repeat it for another preview. Omit the local `warm=1` flag: the server chooses
the execution backend. Existing receipts, upload recovery, status, cancellation
and verified artifact downloads work as before. A second concurrent job gets
the existing quota response; there is no additional queue.

The worker survives an HTTP gateway restart or SSH tunnel disconnect. CPU and
memory limits apply to its whole process tree, including while idle. Each job
has its normal render timeout plus an independent service watchdog bounded by
`walltime_seconds`. A render failure retires the container. Cancelling an active
job stops the whole systemd service and GPU process; the next **new** request
starts a fresh worker. Interrupted or ambiguous requests are never rerendered
automatically. Use the existing administrator `cluster reconcile` command after
an ambiguous acceptance; it recovers the same job's `started.json` or result.

Stop the idle worker to release GPU memory:

```sh
blender-render cluster warm stop --config /srv/blender-render/warm/server.json
```

Stopping a busy worker requires `--force`, and makes its interrupted job fail.
Use the normal job cancellation API when you want a cancelled job status. Stop
before replacing the image, controller, runtime or resource policy; a live
worker with different source/configuration is rejected. Private diagnostics are
in the spool's `.warm/worker.log` and each job's `worker-error.log`. Service
scratch is private and removed on graceful shutdown or the next service start.

## Use from PyMOL

Install the base package in a local uv environment and run `blender-render setup`
there to record its Python interpreter. A local Blender installation is not
required for remote submission from an already running PyMOL. The administrator
provides either an open service URL or a private credential JSON file. For an
open cluster, configure the URL once:

```sh
blender-render cluster configure --profile lab --url https://render.cluster.example:8772
blender-render cluster info --profile lab
```

Add `--ca-file /shared/render-ca.pem` if the service uses a private CA. There is
no token or login in open mode. Everyone who can reach the endpoint can list,
fetch, cancel and delete jobs in its shared queue. The per-user active-job limit
therefore applies to the whole shared queue, alongside the total service limit.

For a service using individual credentials, import the provided file instead:

```sh
blender-render cluster configure --profile lab --credential-file /private/alice.json
blender-render cluster info --profile lab
```

The file must be owned by you and mode `0600`. The client stores a private profile
under `clusters/` in the lab profile folder (see "Your own gateway over SSH").
Delete the transfer copy after importing it. Tokens never appear in command-line
arguments or normal output. Use `--ca-file` with `configure` for a private CA;
certificate verification cannot be disabled. HTTPS is required except for a
loopback URL reached through an SSH tunnel. The client refuses redirects and
does not use environment-configured HTTP proxies.

On the server, open access requires `"authentication": "none"` in its configuration;
`tokens_file` can then be omitted. The default is `"authentication": "token"` and
requires a private credential database. Keep an open service bound to the cluster
network address. TLS, input validation, resource limits and Slurm isolation apply
in both modes. All jobs use the gateway's Unix identity and configured Slurm
account, including jobs submitted by other cluster users.

Load `blender_render/pymol_bridge_plugin.py` as described in the README, then:

```pymol
blender_render cluster=lab
png figure.png
blender_render cluster=lab, preset=mint_sky_assembly, output_dir=renders, output_name=protein.png
blender_render_status JOB_ID, cluster=lab
blender_render_fetch JOB_ID, cluster=lab, output_dir=renders, output_name=protein.png
blender_render_cancel JOB_ID, cluster=lab
```

Submission exports the current enabled selection, transforms, colors, secondary
structure and view. An unnamed still (`blender_render cluster=lab`) waits for the
verified image and displays it in PyMOL. After successful display, repeating the
command starts a new render of the current view. Temporary local files are removed;
use `png figure.png` to save the displayed image.

An interrupted or timed-out preview retains its `.cluster.json` receipt under the
cluster configuration's `pymol-previews/` directory. Repeating the command resumes
that same request, even after restarting PyMOL; it never duplicates an uncertain
submission. `wait=0` returns a job ID immediately, and repeating the command with
the default wait retrieves that preview. One unnamed preview per profile can be
active at a time; named outputs can be used for independent concurrent jobs.

Named exports and movie/saved-scene requests return a job ID and receipt without
waiting by default. Fetch checks every artifact before loading it. Use
`wait=1, timeout=600` to wait and fetch in one command. Waiting blocks PyMOL, and
the timeout includes queue time. A timeout leaves the original job running.

Animation controls work as they do locally:

```pymol
blender_render cluster=lab, animation=360, duration=2, fps=12, gif=1, width=640, height=480, samples=16, output_name=spin.png
```

Omit `cluster=lab` to render locally. Custom styles and saved local presets are
resolved on the submitting computer and sent as complete validated JSON. Server
resource policy chooses CPU or CUDA; a GPU allocation requires an actual GPU and
does not silently fall back to CPU.

## CLI submission and recovery

```sh
blender-render cluster submit structure.cif --profile lab --rough -o renders/first.png
blender-render cluster status renders/first.cluster.json
blender-render cluster fetch renders/first.cluster.json
blender-render cluster list --profile lab
blender-render cluster delete JOB_ID --profile lab
```

CLI imports of CIF/PDB/PSE require the configured local PyMOL interpreter. An
exported bundle JSON does not. A PSE is loaded only on the submitting computer:
it must be trusted because loading it can execute code. The service accepts no
PSE, Python, Blender scene uploads, arbitrary file paths or user-provided shell
commands. Only data exported from the session crosses the network.

The receipt is written before upload and contains recovery identity, not tokens
or molecular coordinates. After a lost response, reconnect and observe that
receipt through `blender-render job status`, even if it has no job ID yet. A
confirmed missing request is reported as `prepared`: repeat `job submit` with
the same input/options and output to resend the same request ID. If the service
reports `submission_unknown`, ask its administrator to reconcile it; do not
create a replacement job speculatively. Downloads never overwrite existing
outputs. A failed integrity check leaves no partial render in the requested
output location.

### One job command for every client

co-pymol, Structure Studio and the PyMOL bridge all submit and recover through
`blender_render.jobs`, available as one JSON command line:

```sh
blender-render job submit scene.bundle.json -o renders/first.png --backend lab --profile lab
blender-render job status renders/first.cluster.json
blender-render job fetch renders/first.cluster.json --wait 900
blender-render job cancel renders/first.cluster.json
```

`--backend local` renders on this computer instead. Lab subcommands connect once
with the saved profile when its tunnel is closed and close only a tunnel they
opened (`--keep-connected` leaves it running). Each client holds a lease while it
uses the tunnel, so a tunnel another render (a co-pymol helper, another chat) is
still using stays open; the last one to finish closes it. Everything after submission starts
from the receipt, so a run submitted by one client can be checked or fetched by
any other. A receipt without a job ID is looked up by its request ID: a job the
gateway accepted is adopted and its ID saved to the receipt; a request that never
arrived is reported as `prepared`. Submitting again to the same output sends the
same request ID, which the gateway answers with the job it already has if the
first upload did arrive, so a retry can never become a second job. A Slurm submission
the gateway could not confirm (`submission_unknown`) stays recoverable rather
than failed, because the administrator's `reconcile` may still turn it into a job.

## Your own gateway over SSH

When you can log in to a Slurm login node with SSH keys and an administrator has
installed a matching controller and image (see below), one command connects this
computer without a shared service:

```sh
blender-render cluster connect --profile lab --ssh-host mycluster --node login-03.cluster.example \
  --remote-executable /shared/install/controller/bin/blender-render \
  --server-config /shared/install/server-gpu.json
blender-render cluster submit structure.cif --profile lab -o renders/first.png
blender-render cluster disconnect --profile lab
```

`connect` sends a small helper over SSH stdin to the controller's Python. It
creates a private per-connection directory beside the site configuration, with
its own spool and token database; Slurm, image and limits stay as the
administrator validated them. It starts (or reuses) a gateway bound to
`127.0.0.1` on the **pinned node**, opens an SSH tunnel to that same node, and
imports a fresh credential into the ordinary profile. The token is streamed from
SSH into a private file, imported and deleted; it is never printed. Jobs run under
**your own** Slurm account. The gateway is always token-authenticated, even if the
site configuration selects open access, because every user on a login node can
reach its loopback interface. Before trusting a port, the helper checks with `ss`
(or `lsof`) that its own gateway process holds it. The client also requires `ss`
or `lsof` to verify that its dedicated SSH process owns the local forwarding port
before sending credentials. The tunnel does not use a shared SSH ControlMaster.

Later `connect --profile lab` calls reuse the saved settings, reopen a dead tunnel
and restart a stopped gateway with the same credential, so receipts of earlier
jobs still fetch. `disconnect` closes the tunnel and stops the gateway; queued
Slurm jobs keep running. `--revoke` also revokes the credential and forgets the
connection. `connection` shows the saved settings and whether the tunnel is open.
Changing the saved host, node, controller or site-config path stops the old
connection before opening the new one; it cannot silently reuse the old tunnel.
Local profile changes and remote gateway lifecycle operations are serialized.
If another operation is still running after ten seconds, retry once it finishes.

After a site-config update, run `disconnect` without `--keep-gateway`, then
`connect` again. A stopped gateway's derived configuration is validated and
refreshed while keeping its job spool, credentials and local port, so existing
receipts still work. Revocation is not needed. Invalid configuration updates
leave the previous derived configuration intact.

Profiles and SSH connection state live under `BLENDER_RENDER_CLUSTER_HOME` when
it is set, otherwise under `BLENDER_RENDER_HOME`, otherwise in the default
`~/.config/blender-render/`. Structure Studio and co-pymol pin
`BLENDER_RENDER_CLUSTER_HOME` to that default folder (co-pymol: `cluster_home`
when configured) while keeping their own `BLENDER_RENDER_HOME`, so they share the
terminal's lab profiles and a lab render resolves presets exactly like a local one.
Studio saves its `.lab.json` recovery context before submission. If a response is
lost or times out after the client has written a receipt, Studio returns
`submission_unknown` with that receipt and a `lab-fetch` command; `lab-pending`
also finds it. Recover that request before considering another submission.

Check whether your site permits short-lived user services on login nodes.

## GPU workstation over SSH

An explicit `"scheduler": "workstation"` server configuration runs the same
Apptainer worker on the gateway machine itself. Use this for an authorized Linux
desktop GPU. The default remains `"scheduler": "slurm"`; a rejected Slurm job
never falls back to the workstation. Clients use the same job API and receipts.

The workstation needs Linux x86_64, Apptainer 1.1+, systemd 254+ with a working
user manager, and an NVIDIA driver for CUDA. Its user cgroup must support CPU and
memory controls. Keep the user manager available for the lifetime of submitted
jobs (for example, through an administrator-enabled lingering user session).
Install a matching controller and SIF as described below; nothing is installed
when a render is submitted. A local Blender installation on the submitting Mac
is unnecessary for rendering the current PyMOL session.

Start from [workstation-server.json](../examples/workstation-server.json). Set
the private spool and image paths, SIF checksum, CPU/memory/time limits, and
`gpu_index` (zero-based CUDA device index; use `0` on a single-GPU workstation).
Set `gpus` to `0` for CPU rendering. Slurm resource selectors are rejected for
this mode, and `limits.max_active_total` must be `1`: one job runs per gateway
at a time. Separate gateways and other workstation applications can still share
the GPU, so use one gateway/profile per workstation for sequential previews.

```sh
blender-render cluster doctor --config /srv/blender-render/workstation/server.json
```

Doctor checks the matching image and systemd user manager without launching a
render. It reports `workstation_check` instead of a Slurm dry run; only an actual
render establishes working CUDA and resource controls. Each accepted request
gets one systemd user service with a fixed container command, clean environment,
CPU quota, memory ceiling and runtime deadline. The unit survives a gateway or
SSH tunnel restart and is collected after exit. Cancellation stops its process
group. The existing `job.json` records its `worker_id`; the worker's verified
result is still required before completion can be reported. An uncertain launch
is reconciled by the exact unit name, never by starting another render.

On the submitting computer, configure the IP and SSH forwarding once (replace
the example values with your workstation's settings):

```sh
blender-render cluster connect --profile lab --ssh-host my-server --node 192.0.2.10 \
  --remote-executable /srv/blender-render/workstation/controller/bin/blender-render \
  --server-config /srv/blender-render/workstation/server.json
```

`--ssh-host` is the SSH alias containing your user/key/jump settings; `--node`
may be its reachable IP. The gateway listens only on remote loopback and the
connection forwards a local loopback port over authenticated SSH. Credentials
are provisioned privately by `connect`; no public HTTP port or manual token
copy is needed. Then, in local PyMOL:

```pymol
blender_render cluster=lab
png figure.png
blender_render cluster=lab
```

The image returns to PyMOL on this computer. Repeating the command after display
renders the current view again. If the connection drops during a render, repeat
the command to recover that same request. Saved profiles reconnect automatically
through the job API; `blender-render cluster connect --profile lab` also
reopens the tunnel explicitly. Disconnecting stops the gateway and tunnel while
already submitted systemd workers continue within their deadline.

## Build once, reuse the uv environments

Use Linux x86_64. From a clean, committed checkout, build the wheel and staged
plugin from one revision, then build the container from those exact artifacts:

```sh
uv sync --locked --extra dev --extra cluster
uv run --no-sync python scripts/build_release.py --output dist/cluster-release
apptainer build --build-arg RELEASE_DIR=dist/cluster-release renderer.sif containers/Apptainer.def
sha256sum renderer.sif
```

The image calls the existing `studio.py setup` once during its explicit build.
That uses uv, Python **3.11.15**, and the same hash-locked render/PyMOL requirements
used locally. The renderer wheel is the one just built from the committed source.
Each Slurm job directly executes `/opt/studio-runtime/render/bin/python` in that
image. Jobs never run `uv sync`, pip, installers or dependency resolution.
The PyMOL and Blender environments remain separate because they have different
binary dependencies. Nothing copies or relocates a Mac virtual environment.

For an application-only update to an already validated Linux SIF, first verify
the base image against its recorded SHA-256. Then use
[`Apptainer-update.def`](../containers/Apptainer-update.def) with `BASE_IMAGE` and
`RELEASE_DIR` build arguments. It requires identical render/PyMOL requirement
files and an identical runtime contract, then replaces only the application wheel
with uv offline. The Linux environments stay at their original absolute paths.
Changed dependency pins require a full image build.
Record the new SIF hash and run `cluster doctor` before switching the gateway;
gateway and image must contain the same wheel.

You can also **reuse an existing Linux Studio uv runtime**. Add this administrator
setting to the server config:

```json
"runtime_home": "/shared/blender-runtime"
```

The gateway binds this directory read-only **at the same absolute path** inside
Apptainer and uses its `render/bin/python` and `blender-render/config.json`.
It must be a complete Linux runtime created with the same Studio setup and pins,
including uv's managed Python beneath that directory. Install the exact release
wheel there before serving. Its configured Blender shim, dependencies and CCD
must all be reachable through that bind. Do not move a venv or bind a Mac venv
into Linux. Run `cluster doctor` to check the interpreter, configured files and
renderer identity inside the container. The worker also refuses a gateway/image
source mismatch before rendering; ordinary render-time dependency probes still
apply. A compatible existing Linux environment is reusable locally and through
the container without installing anything per job.

The SIF supplies Linux libraries and FFmpeg in either mode. Host GPU drivers are
exposed with `--nv`. The pinned Debian 13 base supplies glibc 2.41; the original
Debian 12 base could not load the target compute nodes' newer graphics libraries.
Other clusters still need their own real CUDA check because `--nv` binds host
libraries into the image. The base-image digest and Python dependencies are pinned;
Debian repository packages are not snapshot-pinned, so byte-identical OS builds
are not claimed. See [runtime environments](runtime-environments.md).

On hosts without subordinate UID/GID mappings, Apptainer may bind the host's
`fakeroot` into the build. A newer host glibc can make that fail inside the pinned
Debian image. Use an administrator-supported build method, such as an authorized
`sudo apptainer build` on local scratch storage; root-squashed NFS directories may
be unreadable to that build. This does not require running render jobs as root.
See [Apptainer's fakeroot compatibility guidance](https://apptainer.org/docs/user/latest/fakeroot.html).
An optional `--build-arg BUILD_PROXY=http://127.0.0.1:PORT` supplies a dependency
proxy during the build only. It does not configure a proxy for render jobs.

## Configure the service

Use a dedicated, unprivileged cluster account. Install the gateway from the same
revision with `uv sync --locked --extra cluster`, or install the built wheel with
its `cluster` extra in an isolated environment. Copy
[the configuration example](../examples/cluster-server.json) to a private
administrator-controlled location and adapt:

- Shared spool and SIF paths visible at the same paths on the gateway and compute
  nodes. The spool must be owned by the service account, mode `0700`, and support
  reliable POSIX file locking. Use a filesystem quota for a hard disk limit.
- The SHA-256 of the built SIF and absolute paths to the Slurm/Apptainer tools.
- Partition, account, QoS, CPUs, memory, walltime and zero or one GPU per job.
  For CUDA set `gpus` to `1`. The Slurm walltime must exceed the renderer timeout
  by at least 60 seconds.
- The site's GPU request syntax. `gpu_request` is `"gres"` (default, `--gres=gpu:1`)
  or `"gpus"` (`--gpus=1`). An optional `gpu_type` such as `"a100"` produces
  `--gres=gpu:a100:1` or `--gpus=a100:1`. An optional `constraint` such as
  `"a100|h100"` adds `--constraint=...` to CPU and GPU jobs. Only NVIDIA CUDA GPUs
  are supported; place AMD Instinct nodes on CPU (`"gpus": 0`). The
  [cluster onboarding skill](../.agents/skills/cluster-onboarding/SKILL.md)
  explains how to discover a site's values.
- Limits for uploads, image size, samples, frames, active jobs and retention.
  Defaults accept at most 20 MiB of uncompressed JSON, 16 megapixels, 512 samples,
  360 frames, four active jobs per user, and 32 active jobs overall. The existing
  animation memory/archive limits also apply. Individual downloads are capped at
  1 GiB. The spool-byte setting is an admission check, not a filesystem quota.

Create the private spool and configuration directory. For an open service on a
trusted cluster network, set `"authentication": "none"` and omit `tokens_file`;
no `user-add` step is needed. For individual authentication, keep
`"authentication": "token"`, configure the private credential database, and issue
credentials before starting the service:

```sh
blender-render cluster user-add --config /srv/blender-render/server.json \
  --user alice --url https://render.example.org \
  --credential-output /private/alice.json
```

`user-add` writes a random token to the private transfer file; the service stores
only its SHA-256 digest. Deliver the file privately to its owner. Use
`user-revoke --config ... --user alice` to revoke access immediately, then issue a
new credential if needed. Revocation does not cancel already submitted jobs.

For either access mode, check the configuration and start the gateway:

```sh
blender-render cluster doctor --config /srv/blender-render/server.json
blender-render cluster serve --config /srv/blender-render/server.json
```

`doctor` runs on the gateway host and never installs, queues or renders. It checks
the configuration and SIF checksum, the Slurm/Apptainer executables, and the
Apptainer version (Apptainer 1.1 or newer; SingularityCE or legacy Singularity is
reported as an untested warning). It then runs the container's identity check with
the job flags, and asks Slurm to validate the exact job request with
`sbatch --test-only`, using a temporary directory inside the spool (which must
already exist) as the job's working and log directory. A rejected GPU request
fails here with Slurm's own message. A site `job_submit` plugin may still reject a
real submission, and the check does not reach a compute node or GPU;
`scripts/validate_cluster_slurm.py` remains the real execution gate. Each job also
records its compute node's `apptainer --version` in the private job directory,
and the acceptance script parses it.

Scheduler and container commands run with a minimal environment: `PATH` holds
only the directories of the configured executables plus `/usr/bin:/bin`, and no
`LD_LIBRARY_PATH`, `SBATCH_*` or `APPTAINER_*` variables are inherited. A
module-provided tool that only works after `module load` needs a wrapper or an
install with RPATH.

The service defaults to `127.0.0.1:8765`. Put an HTTPS reverse proxy in front, or
use SSH forwarding to that loopback address. Direct TLS is available with
`serve --bind ... --tls-cert ... --tls-key ...`. Non-loopback plaintext serving
requires explicit `--behind-proxy`; restrict that listener to the proxy. Configure
the proxy's upload limit and timeouts to match the service. No deployment, firewall
change or external listener is created by installing the package.

All Slurm jobs run as the **service Unix account**, using the configured Slurm
account. API identities provide ownership and quotas; they do not impersonate
users' Unix accounts or create separate Slurm billing identities. This is intended
for an authenticated, trusted cluster community. Apptainer containment and private
job binds reduce exposure but are not a sandbox for arbitrary uploaded programs.
For mutually untrusted tenants or per-user Unix accounting, add an administrator-
approved identity/batch submission mechanism before deployment.

Only one job directory is bound writable into each worker; the token database and
other jobs are not bound. `srun` supplies CPU affinity and GPU allocation, and the
launcher forwards its `CUDA_VISIBLE_DEVICES` through the clean environment. Slurm
must enforce the site's CPU, memory and device limits. SIF/runtime directories
should be read-only to service clients and immutable while jobs are active.
The isolated renderer retains these compute limits and Apptainer's fixed driver
library path, while discarding unrelated library paths and credentials.

## Persistent user service and shared clients

[`cluster-user.service`](../examples/cluster-user.service) is a systemd user-unit
template. Put a uv controller environment at `~/blender-render-cluster/controller`,
created at that final path using the pinned Linux Python. Export controller
dependencies from `uv.lock` with `uv export --frozen --no-dev --extra cluster
--no-emit-project`, install them with `uv pip sync --require-hashes`, and install
the release wheel with `uv pip install --no-deps`. The service does no installation
at startup or per job.

Keep `server.json`, `server.crt` and `server.key` under the private
`~/.config/blender-render-cluster` directory. Keep the spool private and visible
on compute nodes. Set `authentication` to `none` only for an intentionally shared
cluster queue. Choose the appropriate Slurm account/partition and bounded active
job limits; open callers all count toward `max_active_per_user`.

Replace `@CLUSTER_ADDRESS@` and `@PORT@` in the unit with an available cluster
interface address and free port, and install it as
`~/.config/systemd/user/blender-render-cluster.service`. Supply a certificate
whose SANs match the service hostname/IP. For a private CA, share only its public
certificate as `~/blender-render-cluster/ca.pem`; keep both private keys private.
Track certificate expiry and renew the leaf before it expires.

```sh
systemctl --user daemon-reload
systemctl --user enable --now blender-render-cluster.service
systemctl --user status blender-render-cluster.service
journalctl --user -u blender-render-cluster.service
loginctl show-user "$USER" -p Linger
```

The unit restarts after failures. `Linger=yes` is needed to keep the user manager
running after logout and start it at boot; if disabled, the administrator can
enable it with `loginctl enable-linger USER`. See the upstream
[service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
and [linger](https://www.freedesktop.org/software/systemd/man/latest/loginctl.html)
documentation. Check a real restart and a compute-node connection before
advertising the endpoint. A configured boot policy is not a tested reboot.

For users sharing the Linux filesystem, the controller and its uv-managed Python
must be readable/executable by those users and writable only by the administrator.
Copy [`cluster-connect.sh`](../examples/cluster-connect.sh) next to `controller`
and `ca.pem`, replacing `@SERVICE_URL@` with the HTTPS origin. Each colleague runs
that script once, optionally passing a profile name (default `lab`). It writes
their own profile and reports the `run .../pymol_bridge_plugin.py` command to use
inside PyMOL. It installs nothing into their PyMOL or system Python, and preserves
their configured local Blender/PyMOL paths. Users on another computer can install
the release wheel into their own uv environment and use the URL/CA configuration
shown above.

Stop only this service with `systemctl --user stop blender-render-cluster.service`;
disable boot startup with `systemctl --user disable blender-render-cluster.service`.
Stopping the gateway does not cancel already submitted Slurm jobs. For an update,
finish active jobs, stage a new immutable image/controller with matching identities,
update the configuration and unit, then restart. Keep the previous release for rollback.

## Lifecycle and administrator recovery

Jobs are durable across gateway restarts. Each request ID can submit only once
per API user; resending different input under that ID fails. If `sbatch` times out
or the gateway exits after submitting, the service reports uncertainty rather than
retrying. Reconcile using the exact Slurm job name and service Unix owner:

```sh
blender-render cluster reconcile JOB_ID --config /srv/blender-render/server.json
```

Reconciliation attaches an existing allocation. It never submits a new job. If
accounting is unavailable or no allocation can be established, the job stays
uncertain for administrator investigation. A Slurm requeue also cannot silently
rerender an already started job.

Completed results are available only after the worker records their sizes and
hashes. Scheduler completion without a verified worker result is failure. Cancel
is asynchronous; deletion waits for a terminal state. Inputs are removed when the
worker finishes. A background sweep deletes remaining job data and artifacts after
the retention period (24 hours by default), including failed jobs. A small ownership
and request-hash tombstone remains to prevent expired requests from being rerun.
User-initiated deletion removes artifacts sooner. Private worker/Slurm logs remain
in the job directory until cleanup; the API does not expose those logs.

## Validation and upstream behavior

Run source and installed-wheel tests with the `dev`, `mcp` and `cluster` extras.
Before opening a service to other users, exercise a real Slurm allocation, GPU
selection if enabled, cancellation, image/movie download and cleanup on the target
cluster. Local HTTP tests do not establish Slurm or Apptainer integration.
Keep site-specific acceptance evidence private.

For reproducible real acceptance, run this on the Linux gateway with the installed
wheel or the source environment. The output directory must be new and visible on
compute nodes at the same absolute path:

```sh
python scripts/validate_cluster_slurm.py \
  --server-config /private/acceptance-server.json \
  --pymol /shared/blender-runtime/pymol/bin/python \
  --fixture examples/1stp.cif \
  --skill dist/cluster-release/plugin/structure-studio/skills/structure-studio \
  --output /shared/acceptance/cpu
```

The script uses the configured image and resource policy, but creates its own
spool, loopback gateway and short-lived credentials. It submits two small renders
and one cancellation probe. Checks include PyMOL submission, original-request
recovery, actual CPU/CUDA selection, checksummed downloads, saved-scene reopening,
movie decoding, frame and session bundle restoration, per-user ownership,
idempotency, revocation and deletion. Failures retain private diagnostic logs and
request cancellation of any outstanding test allocations. `evidence.json` records
whether the run passed. Repeat with a GPU configuration if GPU service is enabled,
and with `runtime_home` set to validate reuse of an existing Linux uv runtime.
Add `--cancel-running` to exercise cancellation after Slurm starts the probe;
this requests a longer animation and cancels it as soon as running is observed.

The launcher follows the documented behavior of
[Slurm sbatch](https://slurm.schedmd.com/sbatch.html) (`--parsable`, `--export=NIL`),
[Slurm sacct](https://slurm.schedmd.com/sacct.html),
[Apptainer binds](https://apptainer.org/docs/user/latest/bind_paths_and_mounts.html),
[Apptainer environment overrides](https://apptainer.org/docs/user/latest/environment_and_metadata.html),
[Apptainer NVIDIA support](https://apptainer.org/docs/user/latest/gpu.html),
and [uv container environments](https://docs.astral.sh/uv/guides/integration/docker/).

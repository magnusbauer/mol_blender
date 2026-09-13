# Interactive PyMOL rendering

The PyMOL adapter has one submission path and an explicit, thread-free
interactive lifecycle:

```text
blocking render:    submit -> wait -> result -> presentation
nonblocking render: submit -> return handle
status:             explicit read-only service query
result:             explicit receipt-backed retrieval -> optional presentation
cancel:             explicit cancellation of the existing job
recover:            existing job, receipt, or transfer recovery
```

It uses the shared client, delivery receipts, preset schema, and renderer. It
does not call MCP and does not implement a profile-specific renderer.

## Blocking compatibility

Omitting `blocking` remains historically blocking; `blocking=1` is identical:

```python
blender_render
blender_render preset=dried_paint_green, profile=local, blocking=1
```

Both forms synchronously export once, upload once, submit one logical
operation, wait for that exact durable job, recover its receipt-verified
artifacts, present or report the verified PNG, and return the absolute PNG
path. Preset/style/override/profile, selection/state, camera and view,
color-follow, transparency, resolution/samples, optional `.blend`, and output
arguments use the same option builder in both modes.

The historical positional tail remains, in order:

```text
blend_output, _self, overrides_json, preserve_view
```

D3 controls are appended after that tail, so an older positional invocation
cannot bind a value to a new lifecycle argument accidentally.

## Explicit nonblocking workflow

Nonblocking behavior requires the canonical value `blocking=0`:

```python
submission = blender_render preset=dried_paint_green, profile=ipd, blocking=0
```

Before PyMOL scene access, the adapter validates lifecycle controls, timeouts,
operation identity, options, and destinations. Scene capture then completes on
the PyMOL/application thread. Upload and submission complete once, and the
command returns only after the service supplies a validated durable job ID.
The returned bounded dictionary includes `handle`, `job_id`, `request_key`,
`profile`, `status`, `stage`, `output`, and `next_action`.

Returning from `blocking=0` starts no watcher or helper thread. In particular,
it performs no wait, result inventory request, download, acknowledgement,
receipt creation, `.blend` copy, preview, or `load_png`. `auto_preview` records
the default presentation preference for a later explicit result call; it never
causes automatic nonblocking presentation.

Admission has a separate whole-operation bound:

```python
blender_render blocking=0, admission_timeout=30
```

The bound covers connection/service preparation, attachment or allocation
negotiation, upload, and the submission response. A timeout before submission
can be retried only with the same operation key. If a submit response may have
been lost after the service committed, the adapter retains the stable key,
reports `execution_unknown`, and instructs the caller to inspect without
resubmitting. The typed error carries the local handle and unchanged
`request_key`/`operation_key`. Because the direct v1 client has no general
request-key lookup, this state may require operator-side inspection; D3 does not
pretend that a job ID is known. It never invents a replacement key or retries
automatically.

Lifecycle booleans are strict. `blocking`, `auto_preview`, `rerender`, result
`wait`/`present`, and recovery `transfer_recovery` accept only `0`, `1`,
`false`, or `true` (including the corresponding Python booleans). Empty,
numeric `2`, and arbitrary strings fail before client construction, scene
capture, upload, or submission.

## The five public commands

The public PyMOL surface is exactly:

| Command | Role |
|---|---|
| `blender_render` | Admit one operation; historical blocking default, explicit `blocking=0` handle return. |
| `blender_render_status` | Inspect authoritative durable state without service startup or mutation. |
| `blender_render_result` | Explicitly wait when requested, receipt-recover artifacts, then optionally present the verified PNG. |
| `blender_render_cancel` | Cancel exactly one existing durable job and return its authoritative state. |
| `blender_render_recover` | Re-establish a local handle for an explicit existing job or invoke transfer recovery. |

There are no `preview` or `resume` aliases. Repeated registration, including
import-time followed by explicit registration, leaves one entry per command.

### Status

Status accepts a local handle or an explicit durable job ID:

```python
blender_render_status HANDLE
blender_render_status job_id=SERVICE_JOB_ID, profile=ipd
```

It constructs a transient client with the existing `inspect_cached` connection
intention and performs one authoritative query. It never exports, converts,
uploads, submits, starts or allocates a service, repairs recovery state,
downloads, or acknowledges a result. It preserves service status, stage,
condition, next action, queue position, result availability, and expiry,
plus the validated preset decision, style identity, selection phase, and
resolved RenderSpec digest when the service supplies D2 consumer metadata,
including queued, running, done, delivered, cancelled, failed, expired,
`transfer_pending`, `restart_required`, `compatibility_blocked`,
`execution_unknown`, and `recovery_required` states.

Only bounded, recognized lifecycle fields enter the local snapshot. Unknown
token-shaped statuses, stages, conditions, actions, checksums, or expiries are
mapped to safe categories instead of being echoed. A recognized
`compute_unavailable` error category remains distinct from generic render
failure.

With no identifier, status queries each bounded session-local handle that has a
durable job ID. Raw remote exception text, signed URLs, and private result data
are not copied into status output.

### Result

Result does not wait unless explicitly requested:

```python
blender_render_result HANDLE, wait=0
blender_render_result job_id=SERVICE_JOB_ID, profile=ipd, wait=1, timeout=900
```

`wait=0` queries authoritative state and returns `result_not_ready` without
inventory, download, or acknowledgement while a job is pending. `wait=1` uses
one end-to-end monotonic `timeout`, including contention on the adapter action
lock and the receipt lock; there is no indefinite implicit wait. Once result
retrieval is valid, the command calls the shared `Client.download()` path for
the exact connection/owner scope and job ID. That path binds immutable artifact
inventory, verifies job identity, checks size and SHA-256, persists its receipt
before acknowledgement, reconstructs moved destinations from an existing
verified receipt, and never submits or rerenders.

The adapter verifies the bytes again before presentation. A modified file is
repaired only when the receipt-backed delivery contract can obtain the exact
artifact; otherwise it is excluded from `outputs` and reported missing. Another
job's bytes are never loaded merely because they occupy the expected pathname.

The return value is structured. A partial example is:

```json
{
  "job_id": "...",
  "status": "done",
  "complete": false,
  "outputs": {"render.png": "/verified/path/render.png"},
  "missing_required": ["render.blend"],
  "error_code": "artifact_unavailable",
  "next_action": "recover_verified_local_copy_or_stop",
  "presentation_status": "not_attempted"
}
```

A successful PNG remains verified, receipted, and returned when a requested
`.blend` transfer fails. A later result-only call can recover the missing
artifact where the service or receipt permits; no compute is started. Complete
manifest documents and raw download responses are not retained in the PyMOL
registry.

Historical blocking execution still returns a string path only after complete
success. If required delivery is partial, it raises
`IncompleteResultError`; its structured `result` attribute carries the durable
job ID, verified outputs, missing required artifacts, and next action. It never
claims partial delivery as success or starts a replacement render.

Presentation happens only in `blender_render_result` (or the synchronous
blocking result primitive) on its calling PyMOL/application thread. No GUI
dispatcher or worker wait is used. Headless PyMOL's identical immediate
GUI/OpenGL hooks select `presentation_status=fallback_path`; the verified local
path is returned without attempting `load_png`. A `load_png` exception is also
presentation-only: delivery stays complete, the verified path remains in
`outputs`, and no redownload or rerender is triggered solely for presentation.

### Cancellation

```python
blender_render_cancel HANDLE
blender_render_cancel job_id=SERVICE_JOB_ID, profile=ipd
```

Cancellation first queries the exact job, invokes the existing client
cancellation once only if the authoritative state is cancellable, and returns
the resulting state. Repeated cancellation observes the current terminal state.
Done, delivered, cancelled, failed, and expired jobs are not cancelled again.
The command never waits, downloads, presents, submits, or deletes output files.
Because nonblocking submission has no watcher, cancellation cannot race an
automatic delivery or preview.

Before durable admission there is deliberately no cancellable local job
handle: the submission call has not returned. An ambiguous submit response is
reported under its stable operation key and must be inspected rather than
cancelled through a fabricated job ID.

### Recovery

```python
blender_render_recover SERVICE_JOB_ID, profile=ipd
blender_render_recover SERVICE_JOB_ID, profile=ipd, transfer_recovery=1
```

Recovery treats the explicit job ID and original service/profile scope as
authoritative. It exports nothing, reads no current molecular scene, uploads
nothing, submits nothing, and preserves status and condition. It returns a
local handle by default; `blocking=1` explicitly waits/retrieves through the
same result primitive and returns the PNG path only after complete delivery.

For `transfer_pending`, the default next action remains `recover_transfer`.
`transfer_recovery=1` calls `client.recover_transfer(job_id)` for that existing
operation; it never calls ordinary download or creates a replacement render.
The direct v1 client does not expose operation-key lookup, so the adapter
documents and rejects request-key-only recovery rather than fabricating a key
such as `recovered:<job_id>`.

After a module reload, every job-ID-only status, result, cancel, or recovery
call must restate its original `profile`, legacy `mode`, or direct
`server_host`. The adapter never infers `local` and never takes a route from
the persistent reservation file.

## Destination and registry safety

Nonblocking default PNG names are qualified with a digest of the durable job ID
(for example `render-a1b2c3d4e5f6.png`). Explicit destinations reserve the PNG,
implicit `.blend`, manifest, explicit `.blend`, and scoped receipt identities.
Reservations are process-locked, persist across module reload, and remain until
delivery completes or the local operation is abandoned/cancelled. A blocking
call that returns a typed intervention error has no returned convenience handle,
so it releases its local destination reservation; the error retains the durable
job ID and explicit recovery reserves a destination again. Nonblocking handles
retain their reservations across reload until delivery or cancellation.
Concurrent operations cannot reserve overlapping targets. A later reuse after
complete delivery is safe because every subsequent result call re-enters the
job-scoped receipt/checksum path before returning or presenting bytes.

Persistent records contain only a hashed service scope and collision targets.
They can prevent an overlapping destination, but they are never authority for
a service route, profile, output path, required artifact, or write. Recovery
destinations come only from the current explicit arguments; an omitted standard
name becomes a deterministic job-qualified path.

The in-memory registry is bounded to 64 tasks and contains only safe metadata:
local handle, durable job ID, operation key, profile/connection identity without
credentials, intended paths, request digest, latest safe state, and verified
artifact paths/checksums. The persistent reservation registry is bounded to 256
records. Neither registry holds a client, bearer token, signed URL, molecular
payload, complete manifest, raw result, private exception, or thread.

Reloading the module can discard session-local convenience handles, but no old
activity can continue because no helper thread exists. Durable job IDs remain
recoverable from the original service/profile, and delivered artifacts remain
recoverable through receipts.

Every new render has one opaque bounded operation key, equal to its generated
local handle unless supplied explicitly. The key is unchanged across transport
retries. The service rejects the same key with changed normalized molecular or
render intent. Only explicit `rerender=1` on a new intended operation may
authorize another render; status, result, cancellation, and recovery have no
submission path.

Legacy `interactive`, `slurm`, and `hpc` mode names still select the configured
default HPC profile. New calls should pass `profile` directly. Per-command
Slurm resources, launchers, and polling knobs are rejected; configure them in
the named profile. `attach_only=1` uses the shared `submit_existing` intention:
it may upload and submit to an already-running controller, but never starts a
controller or allocates replacement compute. Obsolete `attach=0` is
unsupported.

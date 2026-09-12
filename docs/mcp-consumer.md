# D2: MCP consumer contract

D2 adapts D1 selection to the existing `RenderService`, durable FIFO operations
and C2/C3 recovery. It adds no renderer, queue, backend or model. The normal tool
sequence is `render_structure -> render_status -> render_result`.

## Tools and trust surfaces

Default discovery exposes nine tools: `render_structure`, `render_status`,
`render_result`, `cancel_render`, `list_presets`, `list_recent_renders`,
`recover_render`, `recover_transfer` and `available_quota`.
Factories accept `compatibility=True` to additionally register `import_structure`,
`submit_render`, `list_structures` and `delete_structure`. Existing REST/client
compatibility APIs remain available. An additional identical render uses the
compatibility submission with `rerender=true` and a new stable key; ordinary
`render_structure` has no rerender parameter.

Compatibility `submit_render` uses the same authoritative consumer preflight and
exact-context policy boundary as normal submission. It reads an owner-scoped,
already validated import and validates the frozen selection before job creation
or reservation. Raw deferred imports require the normal file workflow. The
legacy direct HTTP and CLI submission paths retain their existing behavior.
Compatibility tools do not relax capabilities, public resolver restrictions,
scene/output ceilings, approximation gates or operation identity.

Configured MCP clients carry an explicit `consumer_policy` envelope with
`format_version=1` and `public=true` for restricted adapters. This is adapter
configuration, absent from discovered render-request schemas. The consumer API
combines it with server trust and selection policy; a private underlying service
cannot widen a restricted adapter. Normal preflight, compatibility admission and
deferred exact-context validation use that effective policy. It is separate from
the unchanged logical request fingerprint. Earlier direct consumer clients may
omit the envelope and retain their server's policy; legacy non-consumer APIs are
unchanged. Consumer file requests use a generic transport filename, keeping local
filenames out of HTTP access logs. Compatibility file upload opts into the same
safe consumer error handling with `consumer=1`.

Default resource discovery contains no legacy widget. `compatibility=True` enables
only tools. The separate `legacy_widget=True` HTTP factory option registers the
old resource and private FastMCP resource adapter; native delivery requires neither.

`create_http()` advertises the restricted `HostedRenderRequest`: installed
built-ins and validated edits, with the existing public resolver restrictions.
`create_private_http()` and explicitly private `create_http(trusted=True)` also
advertise `style_doc`. Public services cannot enable that schema. STDIO defaults
to the trusted schema for local, Apptainer and HPC profiles; other configured
profiles advertise the restricted schema. An explicitly private factory can
select `trusted=True`. Final server policy is always authoritative.

Custom styles must be complete existing style JSON. They cannot accompany an
installed preset, visual selection brief, content/representation/capability/camera
selection requirements, or approximation permission. Output requirements and
validated edits remain available. They use `style_source="custom"` and a null
`preset_decision`; no built-in identity or fit is invented. The normal resolver
binds their final specification. Python, scripts, import paths and `.blend` input
are not accepted.

## Request

The molecular input stays separate: HTTP accepts the declared `file` object with
`file_id`, `download_url`, optional `file_name` and `mime_type`; STDIO accepts
`path`. Only PSE, CIF/mmCIF and explicit PDB input are supported. A reference PNG
is not a second file parameter. ChatGPT/Codex interprets references and supplies
structured observations and explicit edits. The server does no image or prose
inference and never generates palette, material or geometry assignments.

```json
{
  "file": {"file_id": "attachment-id", "download_url": "https://example.org/structure.cif", "file_name": "structure.cif"},
  "request": {
    "format_version": 1,
    "request_key": "e33abfdd-21a3-4b12-a74f-d2c6a4b77b19",
    "preset": "green_paint",
    "revision": "1",
    "hard_requirements": {"content": ["protein"], "width": 1024},
    "visual_brief": {"material_family": "dried_paint"},
    "overrides": {},
    "save_blend": false
  }
}
```

The request carries D1's `preset`, `revision`, `hard_requirements`, `visual_brief`,
`overrides` and `allow_low_fit_approximation`. It also accepts `width`, `height`,
`samples`, `transparent_background`, `save_blend`, `color_follow` and `rough`.
PNG and transparency are the defaults; a scene requires an explicit request.
The key is 1–128 letters, digits, `_` or `-`, starting with a letter or digit.
Use an opaque identifier, never prose, a path, URL or credential. Known credential
prefixes are rejected. Public revisions normalize to strings.

D2 format version 1 is stricter than the general D1 library API. Descriptor
collections accept at most 32 items each and 64 descriptors in total, counted
before deduplication. Content and representation identifiers use
`[a-z][a-z0-9_]*` with at most 64 characters; capability tokens additionally allow
periods and hyphens and must exist in the server declaration before admission.
Palette entries are canonical `#RRGGBB` or `linear_rgb:[r,g,b]`, with three finite
channels between 0 and 16. Background also accepts a hex color. Versioned vocabularies:

| Field | Canonical values |
| --- | --- |
| material_family | clay, frosted, milky, dried_paint, directional_ink |
| background | solid, gradient, transparent, #RRGGBB |
| lighting | area, sun, point, spot |
| edges | smooth, mixed, rounded |
| composition | structure, focus_style, depth_of_field |
| projection | ORTHO, PERSP |
| ligand_treatment | sticks |

`directional_ink` expresses a known unsupported material observation, so it can
produce a low-fit result without accepting prose. Exact documented D1 aliases
such as `dried paint`, `rounded ribbons`, `soft highlights`, `native sticks`,
`black`, and `orthographic` normalize before persistence. Unknown prose, notes,
image metadata and source descriptions are rejected. Only normalized descriptors
needed for exact-context validation enter preparation storage. Descriptors are
bounded to 128 characters; the request is at most 64 KiB encoded JSON, overrides
at most 32 KiB, nesting depth 12 and aggregate JSON nodes 4096. Bounds apply before
selection and database writes; they do not depend on guessing secret patterns.

Unknown fields, malformed values, inactive controls and transport filenames in
edits fail validation. Structured observations/edits exclude reference paths,
URLs and arbitrary metadata fields. Capabilities, server policy, allowed presets,
authorization and admission limits cannot be supplied by a caller. Server
capabilities come from `engine.CAPABILITIES`, an installed declaration, not a live
probe. Optional constructor `selection_policy` can impose stricter restrictions;
it cannot relax public resolver limits or permit custom styles on a shared host.

Output controls that conflict with hard output requirements are rejected. Output
requirements follow normal resolver precedence over JSON edits. Rough mode keeps
its existing quality behavior, including halving dimensions, and is rejected
with explicit hard dimensions. Explicit color-follow controls are applied through
the existing style field; conflicting duplicate controls are rejected. After
conversion, enabled color following requires applicable exported hints. D2 reuses
D1's hint precedence/target matching on an inspection copy, including full-mix
requests, without changing the resolved style or generating colors.

## Selection and exact resolution

Phase A runs D1 without a bundle. It validates the structured request, retains
an explicit preset or selects a built-in, then freezes its canonical ID and
revision. Its decision has no resolved hash. A non-executable result downloads
no attachment and creates no job or reservation. Soft low fit returns
`authorize_approximation_or_new_preset`; a hard/capability/policy incompatibility
returns `revise_requirements_or_new_preset`. Invalid edits are validation errors.
Approximation permission relaxes visual fit only.

Compatible file requests enter the existing durable queue before conversion.
Phase B reruns D1 with the frozen explicit preset/revision and the real validated
bundle. It never reranks automatically. D1's content coverage remains coverage of
the rendered selections, not an assertion that a particular atom class exists
in the file. Molecular conditions within one selection intersect; separate style
entries union. Camera precedence and active material/color consumers retain D1's
reviewed semantics. Post-conversion refusal marks `selection_phase="refused"`,
fails the operation and records `backend_render_started=false`.
The installed catalog must still match the admitted decision before binding.
Typed `selection_error` values distinguish changed catalogs, invalid or refused
selection, unavailable color-follow hints and final resolution failure. None of
these failures authorizes another render.

Final render options pass once through the service's existing `prepare_options()`
and immutable execution snapshot binding, after D1 validation. D1 internally uses
the shared resolver for selection validation; that does not replace the final
service resolution. Only the snapshot supplies the top-level
`render_spec_sha256`, including quality, output, viewport and camera context.
The compact decision deliberately omits its own hash and overrides.

Initial submission may have `stage="conversion_pending"` and a null top-level
hash. Status exposes the exact hash once bound. A hash identifies RenderSpec JSON;
it does not prove admission, live capabilities, authorization or runtime readiness.

## Durable identity and recovery

`request_key` becomes the existing `operation_key`. The normalized structured
selection request and controls participate in its client-request hash. Aliases,
revision type and set ordering normalize deterministically. Changed fit-relevant
requirements conflict with an existing key instead of silently reusing a different
decision. The key binds the first molecular input: retries recover that operation
without comparing a fresh attachment URL, reopening a path or replacing its bytes.
To request a different molecular input, use a distinct logical key.

`recover_render(request=...)` and the adapter's retry path perform owner-scoped
lookup and verify normalized intent. Lookup never uploads, converts, reserves or
submits. It runs before attachment download or local file access, so an expired
attachment or missing original path does not prevent recovery. Connection failure
to an existing controller is an error, not permission to start another operation.
On a cold local/HPC submission, compatible local preselection may start the
existing admission controller; server preflight and recovery still precede file
access. Backend allocation remains behind durable queue admission.

An additive nullable `jobs.consumer_metadata` column stores sanitized decision
facts, source, validation phase and whether backend dispatch started. Old databases
open with null metadata for old jobs. The decision changes only from preselection
to exact-context binding/refusal; dispatch updates its separate boolean marker.
Terminal privacy cleanup removes preparation requests and molecular/style payloads
while retaining safe decisions and identity hashes. Status, result and recovery
read persisted facts. Decisions are never worker input.

`consumer_records` is the single catalog-independent durable validator and
canonical serializer. Metadata format version 1 has an 8192-byte encoded limit.
Its exact field allowlist includes immutable `style_identity`,
`request_fingerprint`, `preselection_decision`, current `preset_decision`,
`style_source`, `selection_phase`, `backend_render_started`, `selection_error`
and `error_category`. Built-in identity contains canonical preset ID, revision
and catalog digest; custom identity contains a SHA-256 of canonical style JSON.
Decision enums, fit/route/compatibility consistency, fixed reasons, bounded
feature tokens, digest formats and unexpected fields are validated on writes,
reads, presentation and before execution. Metadata bytes count toward storage
limits. Exact binding/refusal is atomic; immutable identity and preselection
survive terminal preparation cleanup, even when exact validation has no decision.

Malformed or oversized metadata returns the fixed `metadata_invalid` condition
and operator-inspection action. It is never exposed, reconstructed by reranking,
or allowed to dispatch conversion/compute. This is conservative validation of
durable state, not a claim that isolated database corruption is a remote write.
Custom-style recovery compares canonical JSON intent without applying the current
style schema again; new admissions still validate the complete style normally.

`recover_transfer(job_id)` delegates to existing transfer-only recovery and keeps
the same child operation. It cannot dispatch a replacement render. Status returns
the existing conservative operator actions for `restart_required`,
`compatibility_blocked`, `execution_unknown` and `recovery_required` and stops
bounded polling for those conditions. No automatic recovery was added for them.

## Compact responses and boundaries

Submission/status include acceptance, job ID, status/stage, profile, safe preset
decision, style source, validation phase, final hash, next action, poll interval
and reuse state. Internal history, owner/session IDs, execution payloads and logs
are omitted. Normal results keep artifact links or verified local outputs, preview,
required-artifact completeness, safe decision and final hash; full manifests stay
available as diagnostic artifacts. Missing PNG/scene remains partial recovery,
never permission for another render.
Verified download receipts retain the safe decision and specification hash. An
offline receipt can return these facts without a status request or importing the
current selector/engine; receipt validation checks transport types, not visual fit.

Compact status includes `job_id`, `status`, `stage`, `queue_position`,
`style_source`, `style_identity`, `preselection_decision`, `preset_decision`,
`selection_phase`, `backend_render_started`, `selection_error`,
`render_spec_sha256`, `result_available`, `condition`, `error_category`,
`error_message`, `next_action`, `poll_after_seconds`, `expires_at`, and `reused`,
plus `accepted` and `profile`. Unknown queue/expiry facts are null; they are never
invented. Safe error messages are fixed, including conversion/backend failure,
cancellation, transfer and compatibility blocks, expired/unavailable artifacts,
and delivered/deleted results.

Registered handlers sanitize failures before returning control to FastMCP's tool
manager, using fixed typed errors with no unsafe exception cause or context.
Outer supported FastMCP middleware separately protects argument validation before
handler entry. Server factories install standard logging filters on the FastMCP
server and MCP protocol loggers to suppress DEBUG argument dumps, which occur
before middleware. INFO and higher diagnostics remain enabled. D2 operation
failure logs contain only a safe category and job ID;
raw converter exceptions and submitted values are not logged. Typed converter
conditions survive generic conversion wrappers, including cancellation, compute
availability and capability failures. Missing converters report
`compute_unavailable`; malformed molecular conversion reports `invalid_structure`.
Errors
use deterministic JSON containing `code`, `error_category`, `error_message` and
`next_action`; recognized service codes survive wrapping. No submitted value or
raw exception text is included. Categories distinguish `attachment_unavailable`,
`format_unknown`, `invalid_structure`, `selection_invalid`,
`selection_incompatible`, `capability_unavailable`, `operation_conflict`,
`admission_refused`, `compute_unavailable`, `render_failed`, `cancelled`,
`artifact_expired`, `artifact_unavailable`, `presentation_failed`, and existing
blocked lifecycle conditions. For example `daily_quota` and `budget_exhausted`
retain their codes under `admission_refused`; `file_unavailable` maps to
`attachment_unavailable`. Normal selection refusal is structured non-admission.

Hosted delivery reads and validates inventory once, acquires the PNG stream,
encodes a bounded preview, and constructs the response. An acquisition failure
remains `artifact_unavailable`/`artifact_expired`: required delivery is incomplete,
the missing PNG is not advertised, and remaining artifacts are checked against
the same inventory. There is no second inventory lookup or PNG acquisition retry.
STDIO performs verified download/recovery once and retains paths and a delivery
receipt. If its verified PNG disappears before preview acquisition, delivery is
reported incomplete and other verified paths remain available. Neither case
repeats downloads or acknowledgement. Only decoder/encoder failure after stream
acquisition is presentation fallback. Preview success reports
`presentation_status="inline_preview"`; failure returns the same links/paths
with `presentation_status="fallback_links"` and
`presentation_error="presentation_failed"`. Preview failure cannot change render
success, consume the original PNG, repeat retrieval/acknowledgement, or rerender.
Bounds remain 1024 pixels and 384 KiB, with alpha preserved.

Determinism evidence records actual MCP tool/resource discovery for hosted and
trusted defaults, their compatibility tools, and explicit legacy-widget mode.
Canonical digests include returned ordering, names, descriptions, annotations,
input/output schemas, file metadata and resource URI/MIME metadata. Each guarded
probe verifies expected digests, package import origins and zero external actions
across source, staging, wheel extraction and installation with varied hash seeds.

`list_presets` returns D1's compact format version, schema digest, catalog digest
and descriptors. It is entirely local and does not contact a worker or start a
service. Static `eligible_under_current_policy` is not live availability, quota,
admission or editorial approval. Common renders need no catalog or quota preflight;
submission remains authoritative for admission.
When a server restricts the returned preset entries, `entries_filtered=true` and
`digest_scope="installed_catalog"` identify that the digest still describes D1's
complete installed catalog, rather than the filtered response.

D2 validation is package-level, guarded offline contract testing. Installed-host,
ChatGPT/Codex UI, Mac, Slurm, Modal, backend and deployment acceptance remain
unexecuted. PyMOL, installation-routing templates, runtime behavior, admission,
authentication, budget and privacy retention policy are unchanged. No D2 commit,
push, deployment or plugin publication is part of this implementation handoff.

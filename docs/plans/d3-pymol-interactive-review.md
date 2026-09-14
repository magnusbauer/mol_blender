# D3 explicit interactive PyMOL lifecycle: correction evidence

> **Historical candidate record.** This section records the first D3 candidate
> before its final independent review. That review found additional scope,
> reservation-authority, attach-only, identity, and deadline blockers. They are
> corrected in the final-review closure below; the earlier “ready” statement is
> not the Milestone D release conclusion.

Date: 2026-09-12 (America/Los_Angeles)

This is the D3-only correction and executed-evidence record. The D1/D2/MCP
implementation and shared progress/validation files remain byte-identical to
base commit `5be0e72d86a26386ed8faa68b788aae3dd90863a`.

## Scope and repository state

- Worktree: isolated D3 review checkout (machine-local path omitted)
- Branch: `codex/d3-pymol-interactive`
- Base and current HEAD: `5be0e72d86a26386ed8faa68b788aae3dd90863a`
- Index/commit/push: none; all D3 work remains uncommitted and unpushed

The complete D3-owned change set is:

| File | Purpose |
|---|---|
| `blender_render/pymol_bridge_plugin.py` | Thread-free blocking/nonblocking admission and explicit status/result/cancel/recover commands. |
| `blender_render/__init__.py` | Lazy adapter exports without eager PyMOL import for unrelated consumers. |
| `tests/test_pymol_adapter.py` | Historical adapter compatibility and receipt-descriptor test fixture. |
| `tests/test_d3_pymol_interactive.py` | Corrective lifecycle, integrity, privacy, timeout, reload and registration regressions. |
| `docs/pymol-interactive.md` | Public explicit-lifecycle contract. |
| `docs/plans/d3-pymol-interactive-review.md` | This evidence record. |

No D4/D5 work is included.

## Corrected architecture

```text
blocking render:    submit -> wait -> receipt-backed result -> presentation
nonblocking render: submit -> return durable handle
status:             explicit inspect-only service query
result:             explicit bounded wait (optional) -> receipt-backed result -> presentation
cancel:             explicit cancellation -> authoritative returned state
recover:            existing job/receipt/transfer recovery only
```

The automatic completion worker was deleted. Nonblocking admission creates no
thread and performs no wait, result inventory, download, acknowledgement,
receipt creation, `.blend` copy, preview or `load_png`. `_TASKS` contains only
bounded safe metadata and cannot retain clients, payloads, manifests,
exceptions or workers. Module reload therefore cannot leave old activity.

Omitted `blocking` and `blocking=1` preserve the historical synchronous path.
Both use the same option construction, `_admit_submission` and
`_result_for_task` primitives as the explicit workflow and return the absolute
PNG path only after verified delivery. `blocking=0` returns only after the
service provides a validated durable job ID.

## Review findings corrected

The result-integrity blocker is closed by requiring the exact scoped
`Client.download(job_id, destination)` receipt path. Job identity, immutable
inventory, artifact size and SHA-256 are checked by the shared delivery layer
and checked again immediately before return/presentation. Modified or foreign
bytes are repaired only through that contract or excluded from `outputs`; a
valid receipt can reconstruct the exact artifact without rerendering.

The lifecycle corrections also provide:

- authoritative inspect-only status, preserving all required status,
  condition, stage, next-action, result-availability and expiry fields while
  sanitizing unknown token-shaped fields;
- explicit result retrieval with strict `wait`, optional bounded waiting,
  verified structured outputs and artifact-specific partial delivery;
- synchronous, one-job cancellation with no result or presentation path;
- job-ID-authoritative recovery, explicit `recover_transfer`, no scene access,
  and no fabricated `recovered:<job_id>` request key;
- the exact historical positional tail `_self, overrides_json, preserve_view`,
  with D3 controls appended after it;
- strict lifecycle booleans (`blocking`, `auto_preview`, `rerender`, result
  `wait`/`present`, and `transfer_recovery`) before scene/client side effects;
- a whole-admission `admission_timeout` covering connection/start or attach,
  allocation negotiation, upload and submit response, with stable-key
  `execution_unknown` handling for an ambiguous committed submit;
- job-qualified nonblocking defaults and persistent collision reservations for
  PNG, implicit `.blend`, manifest, explicit blend and scoped receipt targets;
- bounded safe registries and transient per-action clients;
- direct caller-thread presentation only, with off-thread/headless fallback to
  the verified path and no GUI-dispatch wait;
- idempotent registration across repeat calls, import plus explicit register,
  and module reload, with exactly five commands and no preview/resume aliases.

The direct v1 client has no general request-key lookup. Request-key-only
recovery is rejected and documented. An ambiguous submit error retains the
unchanged key and local handle for inspection but never invents a job ID or
automatically resubmits.

A blocking intervention error locally abandons its convenience handle and
destination reservation because no handle is returned; its typed error retains
the durable job ID. Later explicit recovery reserves a destination anew and
still passes through receipt/checksum verification. Nonblocking reservations
persist across reload until delivery or cancellation.

## Failing-before evidence

The final corrective D3 file was run from `/tmp` against the exact staged
pre-correction package at
`/tmp/blender-d3-independent-review-20260912/source`, with the corrected tests
and an isolated `BLENDER_RENDER_HOME`:

```text
87 failed, 11 passed, 3 warnings in 1.61s
```

The failures cover the discarded watcher, eager wait/download/presentation,
local-path trust, foreign/mutated artifacts, receipt reconstruction, partial
delivery, status/cancel/recover authority, synthetic recovery identity,
positional compatibility, strict booleans, admission bounds, persistent
collisions, registry privacy, reload safety and duplicate registration.

During implementation the unchanged compatibility suites additionally caught
and drove corrections for a second intervention status GET and the historical
minimal `{job_id}` admission response. The final runs below include those
regressions.

## Final executed validation

All application suites used the prepared `/tmp/blender-d3-testenv`, isolated
state roots, `PYTHONDONTWRITEBYTECODE=1`, and `c3_boundary_guard`. No test was
excluded from the full run.

| Check | Final result |
|---|---|
| Corrected D3 focused suite | **98 passed, 2 dependency warnings**. |
| Existing PyMOL adapter/preview suite | **9 passed**. |
| Client/lifecycle/recovery/cancellation/delivery matrix | **164 passed, 4 warnings in 151.59s**; zero guarded external actions. |
| D1 focused contracts | **238 passed, 4 warnings in 6.03s**; zero guarded external actions. |
| D2 focused contracts including MCP | **269 passed, 4 warnings in 95.92s**; zero guarded external actions. |
| Complete guarded repository suite | **1,104 passed, 3 skipped, 4 warnings in 318.85s**; zero guarded external actions. |
| Installed shared contracts | **958 passed, 3 skipped, 4 warnings in 47.44s**; zero guarded external actions. |
| Installed D3 plus adapter/preview contracts | **107 passed, 2 warnings** from an asserted installed-package origin; zero guarded external actions. |
| Recursive package comparison | **92 package files equal** across checkout, staged source, wheel extraction and installed target, including manifest hashes and bytes. |
| D1/D2 determinism | **5 + 5 matching interpreters**, with zero guarded external actions. |
| D3 registration determinism | Source/staged/extracted/installed digest `c6921fd8e12d2313d0e4a3a4894dc6dff09e7e08cf9d86e87aaaf891edfd41ce`; exactly one help/shortcut entry for each of five commands. |
| Real headless PyMOL 3.1.8 | Source and installed import/register/reload passed; identical GUI/OpenGL hooks were classified headless; no render or `load_png` ran. |

The successful end-to-end evidence directory is
`/tmp/blender-d3-corrected-evidence-20260912-final2`. Its wheel SHA-256 is:

```text
4d0e8b9e3d1a0aaf03a6e0a08d9876a92f27c5a4902ea9a85459aa08896eb340
```

Its engine build is
`9d5744314e1f04f5cc82ec8f8c4ce18571eb6157f591171a20e1ff4e838e9c13`.
The old reviewed-candidate hash
`03ccfe1b11d326f747dca781578fc723fed83517403954be7a1055c1ffaab055`
is intentionally obsolete after correction.

Whole-wheel reproducibility is not claimed. A first same-source orchestration
built hash `7715cce455b4b308eb9fe15f226d9f0562840ab09b7e989811da1a9728d79e8f`
and then suffered a FastMCP/AnyIO interpreter-finalization `SIGABRT` in one
extracted D2 probe. That exact probe passed immediately unchanged. The clean
second end-to-end orchestration passed all probes. Both wheels carry the same
engine-build hash and 92 equal package members; their ZIP timestamps differ.

The three skips are inherited gates: two developer A/B child-process tests are
prohibited by the fake-only guard and Modal is not installed. The four warnings
are inherited FastAPI/Starlette/AnyIO/Authlib deprecations.

## Activity classification and remaining live gates

- Guarded application actions: fake/in-process services only; every final suite
  reported zero real process/network boundary attempts.
- Temporary preparation: the existing prepared interpreter was reused. The
  validator performed an offline `build --wheel --no-isolation` and
  `pip install --no-index --no-deps --target` outside the checkout. No network
  dependency installation occurred during this correction.
- Real PyMOL: `/opt/pymol/bin/python` performed import, registration and reload
  probes only.
- Live rendering: not performed. Qt GUI presentation, Blender rendering, Mac,
  SSH, Slurm, Apptainer, Modal, OAuth, deployment and paid execution remain live
  acceptance gates and are not claimed here.

D3 is ready for another independent read-only review. This record does not
authorize committing, pushing, or beginning D4/D5 integration.

## Final-review closure — 2026-09-13

The integration correction makes saved reservations collision bookkeeping
only. They retain a hashed scope and bounded target set but cannot select a
profile, direct endpoint, output destination, or required artifact. After a
module reload, an explicit job operation must restate its original profile,
legacy mode, or direct server host; omission never falls back to `local`.

The shared client now distinguishes `submit_existing` from result-only
attachment. PyMOL `attach_only=1` can therefore submit through an already
running controller without starting or allocating one. Safe D2 preset/style
identity and the resolved-specification digest survive status sanitization.
Blocking partial delivery raises `IncompleteResultError` with its structured
verified result instead of claiming a path-only success.

One absolute monotonic deadline now covers action-lock contention, status/wait
requests, the receipt lock, artifact transfer, and acknowledgement. Timed file
locks retain unbounded behavior for existing callers unless a deadline is
explicitly supplied.

The focused D3/PyMOL/connection suite passed **115 tests** with two inherited
dependency warnings. New regressions directly exercise forged route/path
reservations, explicit reload scope, deterministic job-qualified recovery,
`submit_existing`, immutable consumer identity, typed partial delivery, and
bounded action/receipt locks. Full guarded and installed-wheel evidence is
recorded at the Milestone D integration checkpoint.

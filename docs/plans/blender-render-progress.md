# Blender Render implementation progress

## Lab PyMOL defaults: neon preset and direct server — 2026-09-11

The user requested `neon_duplex_hero` as the default after finding that bare
`blender_render` still selected a local service. The lab client now reads an
installation-specific `pymol-defaults.json` beside its package. The deployed
shared plugin and downloadable client bundle select the existing lab endpoint
and neon preset with no render arguments. The checkout's local deployment
configuration supplies the same defaults. These files contain no credentials and
are ignored by Git; committed examples contain only placeholder endpoints.

Explicit preset, host and port arguments override their defaults. Selecting a
different mode or a named profile uses that connection's settings. An installation
without this configuration still selects local rendering and preset 1. The
renderer implementation, immutable engine assets, controller and queue are
unchanged. Updated lab and DokuWiki instructions explain the bare command,
explicit overrides and outside SSH forwarding.

Executed verification:

- **36 passed** with `python -m pytest` for `test_pymol_adapter.py` and
  `test_lab_network.py`, covering default selection and explicit overrides.
- **36 installed-wheel checks passed** outside the checkout. All **87** packaged
  source files matched checkout, wheel and installation byte-for-byte. Wheel
  SHA256: `73ffb39b6a152ba790372d9f5d3618b8b1e30d630c3d2adf8dd8dc7aa5e2ab2f`.
- An actual Linux PyMOL singleton loaded public 2TRX, ran the deployed plugin,
  and executed literal `blender_render` with no arguments. Previously imported
  checkout client/export modules remained loaded, reproducing the user's mixed
  installation context. Job `84001ab6fce97a71932dd38d5458df75` completed through
  the lab proxy and returned a PNG to PyMOL. The manifest confirmed
  `neon_duplex_hero`, full quality and **128 samples**; visual inspection confirmed
  cartoon helices/sheets and ligand sticks. The initial embedded multi-instance
  harness did not register the command on its separate command object and
  submitted no job; using PyMOL's actual singleton resolved that harness issue.

This client-only deployment did not restart or replace the controller. No new
Mac GUI, Slurm, hosted service or outside-network release gate is claimed.
The deployment-specific wiki replacement and render evidence remain untracked.

## Authorized direct lab-network access — 2026-09-11

The user explicitly requested token-free, tunnel-free access inside the lab and
SSH forwarding for outside access. This bounded follow-up implements that network
trust policy without changing rendering behavior or the controller's token checks.
It is not unrestricted publication or a release-gate completion.

The standalone lab proxy requires explicit private-network CIDRs, derives ownership
from the actual TCP source address, and supplies a separate operator-held controller
credential to the existing loopback API. Forwarded identity headers are discarded.
Users on the same workstation/NAT/Digs host share a job identity; this limitation is
explicit in the documentation. Administrator, internal execution and unsandboxed
file-conversion routes are unavailable through this adapter. The normal service
retains its one FIFO queue, database, idempotency records and lifetime accounting.

PyMOL `mode=lab` and `lab_http` endpoint profiles use the existing shared client and
renderer. The opt-in transport sends no inherited token, disables environment HTTP
proxies, rejects redirects/public IPs, and connects to the exact validated private
IPv4 address. No camera, preset, material, export or Blender implementation changed.

Executed verification:

- **59 passed, 4 inherited deprecation warnings** for `test_lab_network.py`,
  `test_pymol_adapter.py`, and `test_jobs_api.py`, using `python -m pytest`.
  A sandbox-local TestClient event-loop stall was resolved by running this same
  suite outside the sandbox; no renderer/API workaround was added.
- An offline-built wheel was installed outside the checkout with `--no-index
  --no-deps --target`. **30 installed transport/PyMOL checks passed**. All **87**
  packaged source files matched checkout, wheel and installation byte-for-byte.
  Wheel SHA256: `c83a0297ef661f1ecee908a61b1b394a198899c2fde404d00267fb9bc669624c`.
- A real public 1SPS scene was exported in PyMOL, submitted directly from Digs
  without a token/tunnel, rendered and downloaded. Job
  `e0cefbb42f979fd8d7a2fb76046393fd` produced a 320 x 240 rough PNG; its manifest
  verified CIF, DSS, cartoon secondary structure, native ligand sticks and the
  pinned runtime. A second actual PyMOL adapter render through SSH completed as
  `7cc518e93d3513420a3e4ae49b1fa843` with another valid 320 x 240 PNG.
- Live admin/internal/PSE-route attempts returned 403. An unapproved TCP source
  returned 403 despite a spoofed forwarding header. Graceful shutdown/restart
  preserved the existing queue: health did not restart it; one new upload did,
  and its unused structure was deleted without submitting another render.

The operator explicitly deployed only the observed lab subnets plus loopback.
Executable service code and credentials live in an owner-only configuration
directory; the shared client archive contains no credentials. The prior controller
was stopped before replacement, and its existing database was retained. No Slurm,
Modal, OAuth, Mac GUI or real outside-network release gate is claimed here.
The ready-to-adapt DokuWiki replacement is `docs/wiki/good_viz_blender.txt`; a
deployment-specific filled copy and public render evidence remain untracked.

## C3 exact-commit local and Slurm acceptance passed — 2026-09-10

Rendered candidate: `8741242d585dc4cf0a0ef76ca96c78e892a7cea4`.
The unchanged runner ran once from fresh clean detached local/shared-remote
worktrees after passive, engine, guard and prerequisite gates passed. The
established external one-submission guard permitted exactly one allocation.
No source, runtime, SIF or qlogin argument was changed. This documentation-only
evidence commit is a direct child of the rendered candidate; it was not itself
rendered. See [the full sanitized acceptance record](../c3-acceptance-8741242.md)
for commands, immutable identities, checksums, timings and retained evidence.

Local job `2985a7b2984355c57a6c3c18916e2b07` rendered on CUDA/NVIDIA GeForce
RTX 4080 SUPER. Slurm job `da01e86e31d4aacf8d7b1d2ec56f19cc` rendered on
CUDA/NVIDIA RTX A4000 in allocation **23432476**. Both had one logical operation
and one leaf render, matching bundle/spec/engine identity and 1,106 atoms / 110
residues across chains A/D. Export verified peptide/PTR, DSS and saved camera.
Both PNGs are valid 512 × 384 rough outputs with protein cartoon and native
ligand sticks, no spheres or `.blend`. Both workers verified the pinned runtime
and captured CCD. Paired visual inspection passed for orientation, framing,
representation and absence of obvious clipping or missing counterpart.

The same long NFS base produced an 88-byte durable registry path, which would
have produced the historical 115-byte socket. The actual Slurm socket was only
**43 bytes**, independent of durable state. Outer base provisioning, same-path
Apptainer binding, container verification and both inner/outer cleanup passed.
The local 33-byte socket was also removed after worker exit. Durable logs stayed
under the configured roots. The validation session stopped, allocation 23432476
was cancelled after successful delivery and disappeared from the queue, and all
owned processes exited. No retry, recovery, replacement allocation, developer
preview or Modal action occurred. Historical database and `1qys.cif` hashes
remain unchanged. Private evidence identifier: `acceptance-8741242-y2pny9ue`.

This supersedes earlier candidate-specific statements that normal local/Slurm
parity and IPC acceptance were still pending. Real recovery/stress scenarios,
macOS Metal and hosted/UI gates remain unverified as detailed in the linked
record. D has not begun. Modal implemented and contract-tested; real backend
execution unverified. Repository consolidation follows the separately authorized
archive-first procedure only after this successful acceptance and visual gate.

## Outer Slurm service-container IPC correction — 2026-09-10

Starting candidate is `1dd0cf96d1091510b3ae859b11fb1357a41a79ba` on
`codex/c3-execution-review`. Its one authorized acceptance run passed locally,
but Slurm operation `b38089ce229125e37e9325add58be12d` was blocked before
execution with `worker_ipc_unavailable`. This correction addresses the missing
IPC base at the outer Slurm service-container boundary. The preserved operation
and all historical databases are untouched; no recovery or replay is authorized.

Initial regressions produced **23 failures and one pass** before implementation;
two additional acceptance-evidence regressions and the container bind-failure
regression also failed first. The correction adds a mandatory explicit base,
validated short node-local allocation storage, a same-path rw bind through
`--cleanenv`, a contained pre-service Unix bind probe, and a bounded supervising
child lifecycle. Probe and actual worker evidence are distinct. Durable state,
launcher resource arguments, session publication/fencing, ownership, and
reservations remain unchanged. An unconfirmed child exit or remaining inner
worker directory retains storage and records incomplete cleanup rather than
removing potentially live IPC. An uncatchable supervisor kill/node failure cannot
guarantee finally cleanup and is not claimed as verified.

Independent read-only review identified four blockers: stale same-session
handshakes, a success marker preceding probe cleanup, finalization exceptions
skipping signal restoration/evidence, and evidence-write failure skipping owned
validation cleanup. Failing regressions reproduced all four before fixes. The
handshake now uses a fresh invocation nonce and follows complete probe cleanup;
signal restoration and client/tunnel cleanup have unconditional finally scopes.
Cancellation coverage retains a live inner worker until contained shutdown.
Probe directories have a separate prefix so a terminated pre-service probe is
not mistaken for a possibly live Blender worker. The incomplete full-v1 run was
stopped for these review fixes and is explicitly superseded, not passing evidence.
Post-fix focused contracts passed **120 tests** with zero audited external
actions. The complete `full-v2` runner passed **595 guarded repository tests**
(two inherited A/B child-process skips, four warnings, 221.21 seconds) and
**401 installed-wheel contracts** (four warnings, 24.00 seconds). Both logs report
zero audited process/network attempts. The installed suite now includes outer
service-container lifecycle and acceptance-evidence contracts. The runner built
a wheel and installed it offline with `--no-index --no-deps --target` outside the
checkout, using the prepared test dependencies. It verified import origins and
recursive equality of **86 files** across source, wheel and installation. This is
not a clean dependency install or real backend validation.

Verification command:
`<prepared-python> scripts/validate_c3_contracts.py --output <outside-checkout>/full-v2`.
Private evidence identifier: `c3-outer-ipc-im4cfb_p/full-v2`.
Wheel SHA-256:
`ba980078e2bed27b73c3447a341a3d5614ca6ed41a75cfe3583db75f3e6e97f7`.
Engine build:
`37c731a7ceebeade49213b9399837dfe6fabc6eab2f3dd728c408ddca84cbd58`.
`git diff --check` passed. Final independent read-only review approved the
post-fix source, tests, documentation, completed verification evidence, and the
eleven-file staged scope with no remaining blockers.
All three historical operation databases and unrelated `1qys.cif` remain
byte-identical to their starting hashes. No Blender, Apptainer, backend SSH/Slurm,
Modal, acceptance, SIF rebuild, hosted-policy change, branch consolidation or D
action ran during this correction. Modal implemented and contract-tested;
real backend execution unverified. A new exact-commit Slurm acceptance remains
required after this correction; fake contracts do not establish that gate.

## Structured compatibility diagnostics correction — 2026-09-10

Starting HEAD is `17fce7350242c3467501db40ff2810ba558b64b6` on the review branch.
The real acceptance at that candidate passed locally but retained a Slurm job as
compatibility_blocked without an underlying exception. Subsequent authorized
non-rendering SIF probes matched the engine inventory, locked runtime versions,
and CCD capture/activation/checksum/cleanup. They did not establish the original
GPU-node failure cause. This correction adds diagnosis, not an inferred runtime fix.

Eighteen initial diagnostic regressions and two acceptance-reporter regressions
failed before implementation. The version-1 bounded allowlist travels from typed
sources through daemon IPC and worker management into an additive nullable jobs
column and authenticated owner status. Safe catalog messages replace null errors
for diagnosed blocks. Existing legacy blocks remain valid. Informational evidence
does not alter lifecycle classification, execution certainty, retries, reservations,
charging, or operation identity. The runner records owner diagnostics before cleanup
and hashes accessible validation-owned logs separately without copying their text.

Focused integration covered all thirteen reasons, real in-process daemon CCD/import
boundaries, owner isolation/public health, atomic persistence, old database opening,
and preserved job/accounting state. A further regression caught a potential change
to existing non-compatibility CIF fallback error text; that text remains unchanged.
Only compatibility error messages use the fixed safe catalog.

Independent read-only review found two error-path blockers:
relative imports in standalone verifier exceptions and a private stderr error
suppressing a typed refusal. Both were reproduced by failing tests and fixed.
A further failing regression required the daemon diagnostic formatter to load before
the first seal. Exception construction now imports nothing; unavailable formatting
uses a fixed unknown record. Private traceback output is best-effort. Migration
coverage now includes a populated receipt. The partial full-v1 run was stopped and
marked superseded before these fixes; it is not passing evidence. Post-review
focused verification passed **77 tests**, with zero audited external actions.

Complete post-fix verification (`c3-diagnostics-l4ly8neb/full-v2`) passed:
**546 guarded repository tests**, two inherited A/B child-process skips, and
**346 installed-wheel contracts**. Repository duration was 222.38 seconds;
installed duration was 23.80 seconds. Both logs report zero audited external
process/network attempts. Four inherited dependency warnings remain. The runner
performed an outside-checkout wheel build and offline target install, verified
installed import origins, and compared **86 package files** recursively across
source, wheel and installation. This uses prepared runner dependencies, not a
clean dependency-install or real backend gate. Command:
`<prepared-python> scripts/validate_c3_contracts.py --output <outside-checkout>/full-v2`.
Wheel SHA-256:
`a345c14838ebb2d6b3fafa256ae0ac392ff877affd6149877b30a11fca8c1233`.
Engine build:
`0d0fe1a673bb9b4f5782e3da1ac29150a6e05225525993c085bf46934a0ca793`.
`git diff --check` passed. The preserved Slurm operation database remains
byte-identical to its pre-edit checksum. HEAD remains the starting candidate
until the scoped correction commit; unrelated `1qys.cif` remains untracked.
Final independent read-only review approved the post-fix staged implementation,
documentation and completed verification evidence with no remaining blockers.

No acceptance, real Blender, SSH/Slurm compute, Modal, SIF
rebuild, branch consolidation or D work is authorized in this correction. Both
historical blocked operations and their databases remain untouched; no recovery or
replay occurred. Modal implemented and contract-tested; real backend execution
unverified. Real GPU-node failure diagnosis and recovery/stress scenarios remain
unverified; passing fake contracts do not establish real acceptance.

## Worker IPC lifecycle correction — 2026-09-10

Starting HEAD verified as `880c3c37ec38b7c404a105f48070bdcec1c9d358` on
`codex/c3-execution-review`, with unrelated `1qys.cif` preserved. The review remote
matched; public-release remained `70bcbfa37cf1148ff90ae6d6849f46b6532ff96a`.
The preceding real C3 attempt passed developer preview and local acceptance after
isolated pinned runtime preparation. Slurm worker startup then failed with
`AF_UNIX path too long` at a 115-byte socket pathname beneath the durable registry.
That execution_unknown operation is retained and is not retried or recovered here.

Twenty new guarded IPC regressions failed before implementation. Persistent worker
logs and captures remain under the configured durable root; sockets now use fresh
short private directories selected from a suitable XDG runtime directory or /tmp.
The directory lives through confirmed process exit, including forced termination,
and is bound explicitly for outer Apptainer launches. Creation failure is typed
before process launch. Private status clears the active socket after shutdown.
Acceptance prerequisites report and verify the effective IPC storage strategy
without restricting durable registry length or launching a worker.

The first focused run passed 100 worker, command, integrity and operation contracts;
the extended IPC/preflight run passed 23, including preservation of the admitted
job and reservation on IPC failure. Independent read-only review identified one
bind-encoding edge case: private XDG directory names containing `:` or `,`. Two
failing command regressions reproduced it; those bases now fall back to /tmp.
The partial v1 full run was stopped as superseded. Post-fix focused verification
passed 25 tests, and complete verification was repeated after the fix.

Final `c3-worker-ipc-full-v2` evidence: **514 guarded repository tests passed**
(two inherited A/B child-process skips), **318 installed-wheel contracts passed**,
and all **85 package files** matched recursively between source, wheel and
installation. Both complete suites and all focused passes recorded zero audited
external attempts. Four inherited dependency deprecation warnings remain. Wheel
build and offline install ran outside the checkout with the prepared test
interpreter; this is not a clean dependency-install or real-backend acceptance.
`git diff --check` passed. Wheel SHA-256:
`9a9152ee109b87be3bdb1163869c30ca166e452d9aa83c62bcc1808b568cac27`.
Engine build:
`c62a6a57202424d0911797c7576a9123e9c70dfd4da8570856b23f86a12cf098`.

No real Blender, SSH/Slurm compute, Modal, acceptance invocation, D work,
existing-operation recovery, or public-release change is part of this correction.
Real IPC/Slurm acceptance of this new build remains unexecuted. Modal implemented
and contract-tested; real backend execution unverified. Existing recovery/stress
gates remain open. The authorized Git boundary is one direct child of `880c3c3`
and a non-force push of only `codex/c3-execution-review`.

## Developer preview CCD integration — 2026-09-10

Starting HEAD verified as `1edf33060a83a3b34fb35a067f793a8434c25c15` on
`codex/c3-execution-review`, with unrelated `1qys.cif` preserved. The remote review
branch matched; `codex/public-release` remained at
`70bcbfa37cf1148ff90ae6d6849f46b6532ff96a`. This narrow correction authorizes one
direct-child commit and non-force push of only the review branch. No real Blender,
Slurm, Modal, acceptance runner, D, deployment or hosted-policy change.

Ten actual-entrypoint regressions failed before the fix: developer_blender
invoked the shared renderer without initializing worker_ccd. The entrypoint now
checks cfg's CCD checksum against the runtime lock, selects the preview's already
private CCD, and initializes a worker-owned capture inside the preview root.
Rendering and the existing source/provenance evidence generation share its
lifetime; finally closes the capture on success or failure. The renderer's strict
requirement and worker capture implementation are unchanged.

The tests execute the actual entry script and package code from a temporary source
snapshot, injecting only Blender/Biotite/render boundaries. They verify private
capture use, an inherited caller-CCD path never being read, wrong-pin rejection,
provenance preservation, source-mutation rejection, and cleanup after render and
manifest failures. Focused developer/CCD verification passed 46 tests with two
intentional inherited child-process skips and zero audited external attempts.
Complete guarded and installed-wheel verification and independent review follow.

Complete verification passed 489 guarded repository tests (two intentional
inherited child-process skips) and 295 installed-wheel contracts. Both audit logs
record zero external attempts; four dependency deprecation warnings remain.
Offline wheel build/install ran outside the checkout, verified installed import
locations, and matched all 84 source/wheel/installed package files. Whitespace
checks passed. Evidence: `c3-developer-ccd-full-v1` runner output.
Wheel SHA-256: `7f9c04cdfb649f080081f091b13873864d1a8e324ab39fd53291976c0d24f007`.
Engine build: `76aa0cb47c4e1c9f4d43b896ddeef0db07a313779ce4960f5566292099e96f06`.
Independent read-only task `developer_ccd_review` found no blocking defects in
the six-file delta. Its focused guarded rerun passed 46 tests with two intentional
skips and zero external attempts. It confirmed capture lifetime, locked chemistry,
source/provenance preservation, full-run evidence and the staged Git boundary.
The correction is one direct child of `1edf330`, pushed without force only to
`codex/c3-execution-review`; public-release and real backend gates remain unchanged.

## Three final C3 blockers — 2026-09-10

Starting HEAD verified as `f86c87d5df79f8dcf3ac0180b3d05d17a1087da9` on
`codex/c3-execution-review`, with only unrelated `1qys.cif` untracked. The remote
review branch matched that commit; `codex/public-release` remained at
`70bcbfa37cf1148ff90ae6d6849f46b6532ff96a`. One new direct-child commit and an
explicit non-force push of only the review branch are authorized. No real
Blender, Slurm, provider execution, acceptance runner, D, deployment or hosted
policy change is authorized. The unrelated fixture and external SIF are retained.

Twelve client regressions failed before the waiting fix. Shared Client.wait now
surfaces all four durable blocked conditions with condition/job/next-action
context through direct clients, CLI and PyMOL. Explicit later resolution resumes
the same operation without client cancellation or resubmission. The focused
client/adapter run passed 42 tests with zero audited external attempts.

Eleven retrieval regressions failed before separating execution validation from
stable version-1 stored-envelope integrity. Recovery checks original immutable
identity without current catalog, schema consumers or capability derivation.
Invalid saved evidence retains recovery_required; conflicting child identity,
protocol and artifact inventory preserve verified receipts without new transfer
or acknowledgement. The focused recovery suite passed 82 tests with zero audited
external attempts, including upgraded-controller recovery with no new compute.

Eleven CCD contracts were written before the new worker capture boundary existed.
The actual daemon initializes a private locked-checksum capture before readiness,
verifies Biotite activation, and checks only captured bytes thereafter. Tests
cover source swaps, symlinks, bad sources, failed/ineffective activation, private
copy corruption, renderer use, real daemon readiness over fake IPC and cleanup.
The first integrated run exposed an older test's source-path assumption; it now
checks private-capture corruption, and the IPC retention test exercises that
same boundary. All 71 focused worker/runtime/review tests passed with zero
audited external attempts. Complete verification and independent review follow.

Complete guarded verification passed 479 repository tests (two intentional
inherited child-process skips) and 285 installed-wheel contracts. Both runs
recorded zero audited external attempts and four dependency deprecation warnings.
The offline build/install ran outside the checkout, checked installed import
locations, and matched all 84 source/wheel/installed package files with the
recursive inventory. `git diff --check` passed. Evidence is retained in the
task's `c3-three-blockers-full-v1` runner output.
Wheel SHA-256: `9d7de0486ebd41551dd70b506813158de4768657dc7f103269c31373ee561aa7`.
Engine build: `6e5d0526e41c2c5d366ef3d5ff5930349d6f95cbfcd93c8519051d47e9bbaf0c`.
Independent read-only task `c3_three_blocker_review` found no blocking findings
in the final 18-path delta. Its guarded selection passed 82 tests with zero
audited external actions. It independently checked the worker lifetime and
activation flow, stable retrieval and receipt compatibility, all 84 files across
source/staging/wheel/install, hashes and whitespace checks. The correction's Git
boundary is one direct child of `f86c87d`, followed by an explicit non-force push
of only `codex/c3-execution-review`; `codex/public-release` must remain unchanged.

The installed/source manifest distinction, passive public-handler sealing and
minor status consistency remain deferred release-hardening requirements. Modal
implemented and contract-tested; real backend execution unverified. Real local/
Slurm acceptance and recovery stress remain unexecuted.

## C3 review corrections — 2026-09-10

Starting HEAD verified as `1969365e069504b21bc77b33d0811c31bea2debc` on
`codex/c3-execution-review`. Unrelated `1qys.cif` and external SIF are preserved.
One direct-child correction commit is authorized; no push, D, real acceptance,
Blender, SSH, Slurm, provider action, Modal deployment or paid compute.

Phase 1 introduced recursive package identity, actual-byte seals, process-local
integrity latch, orthogonal durable operation blocks, migration compatibility,
worker CCD checks and removal of renderer CCD restoration. Seven inventory/CCD
regressions and four operation regressions failed before implementation. Initial
focused verification passed 46 tests with zero audited external actions.

Phase 2 introduced explicit connection intentions, attachment-only older-child
recovery, and transactional child timing import identity. Three initial regressions
failed. Integrated dead-tunnel/restart contracts use fake connection boundaries
and in-process child services; no allocation or new leaf render during recovery.

Phase 3 addresses aggregate-only public health, authenticated negotiation, worker
resource limits, explicit launcher/transport validation, sanitized passive cache
observations and shared reviewed-checkout cleanliness. Eight initial regressions
failed. Integrated focused verification passed 72 tests (two inherited dependency
warnings), with zero audited process/network actions.

The integrated pre-review run passed 407 repository tests (two deliberately
excluded child-process tests) and 213 installed-wheel contracts. The outside-
checkout build/install and recursive comparison verified 83 package files.
Independent read-only task `c3_review_one` found seven blockers: mutable subprocess
entrypoints, entrypoint drain reset, retained allocation permission after child
submission, cancellation/block races, insufficient legacy-child evidence,
transfer continuation after a latch, and resource symlink substitution. It also
noted reused transfer attempt numbers on restart.

Twelve new regressions failed before fixing those paths. Additional checks cover
captured worker/helper launch, changed-code preloading, timing-window retention,
and the final remote start identity check. A full rerun found a recovery-condition
precedence regression; the explicit dispatch fence now takes precedence for leaf
operations while legacy child metadata remains conservative. Six additional HTTP
worker regressions failed before preserving nonterminal conditions through that
transport. The latest focused run passed 26 tests with zero audited external
actions. Complete post-fix evidence and final independent review follow below.

Post-fix verification: 430 repository tests passed; the two inherited A/B
child-process tests remain excluded by the audit guard and have the documented
in-process contract replacements. All 236 installed-wheel contracts passed.
Both runs recorded zero audited process/network attempts and four dependency
deprecation warnings. Offline wheel build/install ran outside the checkout;
the shared recursive inventory matched all 83 source, wheel and installed files.
Import-location assertions and `git diff --check` passed. Evidence is retained in
the correction task's `c3-corrections-final-evidence` runner output (repository,
installed, build/install logs and evidence.json). Wheel SHA-256:
`030a1b0c441fb33aa53d51807d0855f0138d6c5cc64654c18c5d9b87389da026`.
Engine build: `95c86b12b5a1552fefe5ad377d4dbfd26d83472ebbaded3138fe8509dd56f09e`.
Independent task `c3_review_final` reviewed that staged implementation and evidence
without edits. Its 57 guarded checks passed, but it found three blockers: the
partial Modal runtime image omitted the new engine dependency, the Modal helper
was missing from the conversion closure, and downstream recover-transfer lacked
its own integrity guard. Four regressions failed before fixes. The setup layer
now includes its dependency; the provider-free helper preloads and guards sandbox
creation; downstream recovery checks integrity before requesting new work.
All 27 focused review regressions passed. The next repository run passed 434 tests;
a new package-root alias regression, added during that run, failed both in its
focused check and the installed-wheel run. Resource lookup now captures invocation
and physical root identities before following links and latches if an alias moves.
Final renewed verification passed 435 repository tests (two intentional inherited
child-process skips) and 241 installed-wheel contracts, with zero audited external
actions and four dependency deprecation warnings. Offline build/install, import
locations, recursive comparison of all 83 source/wheel/install files and whitespace
checks passed. The authoritative final runner output is
`c3-corrections-sealed-evidence`, superseding earlier correction evidence.
Wheel SHA-256: `7f2c1a0ae259578ce387ef5796a6be8dbd55a511f7d28727d4a1dec2abd2b021`.
Engine build: `6c862ce114c48fe8effc2ca17382ed6f820b9510f872988875e0af50e1648a14`.
Independent read-only task `c3_review_sealed_final` checked the integrated diff,
evidence and 62 guarded correction tests (all passed). It found five remaining
boundary gaps: latch races at durable admission, conversion exception masking,
CCD refusals losing operation state over worker IPC, remote installation damage
preventing administrative controls, and Modal staging bypassing the inventory.
Seven regressions failed before implementation. The fixes synchronize admission
with detection, preserve typed conditions, retain cold/warm worker refusals,
use captured administrative helpers, and use the same private inventory capture
for Modal CLI and image staging. The first affected run passed 50 tests with zero
external attempts; the admission/lifecycle follow-up passed 42 tests. Renewed
complete verification passed 442 repository tests (two intentional inherited
child-process skips) and 248 installed-wheel contracts. Both audit logs contain
zero external attempts; four dependency deprecation warnings remain. Offline
wheel build/install, installed import checks, the shared 83-file recursive
source/wheel/install comparison, and whitespace checks passed.
That runner output is `c3-corrections-complete-evidence`.
Wheel SHA-256: `17f4314e3ad8678eda686a88fd38c7bb06578b672f130fee375abe51e83641c4`.
Engine build: `896ce82ec04b55912d9df01efbb1fbefbd08c24b3411f82388f3fc6362a4416a`.
Post-fix independent inspection passed 58 guarded checks but found two residual
edges: the renderer-entry CCD guard still returned an untyped error, and image
staging could reseal a retargeted package alias. Three regressions reproduced the
failures. The CCD test replaces only the Blender preload boundary and compiles
the actual renderer entry and guard from sealed bytes; it proves retention through
daemon IPC, worker manager and controller. The entry guard now preserves the
typed pre-execution refusal, while staging checks the existing process seal and
lexical root identity before capture. All 57 affected focused checks passed.
Renewed complete verification passed 445 repository tests (two intentional
inherited child-process skips) and 251 installed-wheel contracts, with zero
audited external attempts and four dependency deprecation warnings. Offline
build/install, installed import checks, all 83 recursive source/wheel/install
file comparisons, and whitespace checks passed. The authoritative output is
`c3-corrections-final-seal-20260910-v2`, superseding earlier correction evidence.
Wheel SHA-256: `1cc6b8e4fa0a476e93c457636a768775f65c15a61f39e86e9885f8754c6cd442`.
Engine build: `dca932f6ff6c520d69583e5facdc4a0502a12018bdd67dc38df709ebb6ae00b9`.
Final independent read-only inspection by `c3_review_sealed_final` found no
remaining blocking findings. Its independent guarded run passed 72 tests with
zero external attempts and four dependency warnings. It checked the final 44
staged paths, migration/recovery preservation, tests, documentation and complete
evidence, and independently compared all 83 files across source, staging, wheel
and installation. The final Git boundary requires the single correction commit's
direct parent to remain `1969365e069504b21bc77b33d0811c31bea2debc`; no reviewed
commit is rewritten, unrelated files are preserved, and nothing is pushed.

Modal implemented and contract-tested; real backend execution unverified.
Real local/Slurm parity and recovery stress remain unexecuted.

## C3 execution record — 2026-09-09 (in progress)

Authorized starting point: `codex/public-release`, C2 `70bcbfa`. The existing
untracked public structure is untouched. No checkout/reset or hosted policy
change. C3 is limited to immutable execution, backend negotiation and recovery;
D and publication remain outside scope.

C3.1: added immutable bundle/spec admission snapshots separately from C1's
fingerprint encoding. Raw conversion captures catalog/options first, then binds
both snapshots atomically. Status/result preserve digests after private snapshot
content is deleted. Internal submissions require distinct controller credentials,
scoped owners and independent validation; workers check exact build/capabilities
and restart a stale process once before dispatch. Packaging generates the manifest;
source identities and loaded runtime observations are cached per process. Added
selection propagation to Apptainer and an injected Modal sandbox boundary; removed
render-time CCD download setup. GPU policy resolves to backend-neutral AUTO.

Tests were written first: six expected failures before implementation, with zero
audited process/network attempts. Final focused run: **83 passed, 4 dependency
warnings**, using `python -m pytest -p c1_boundary_guard` over execution-envelope,
spec, export, jobs/API and render-server contracts. Log: temporary test-runner
storage `blender-c3-focused1.log`. A privacy regression (snapshot bytes surviving
terminal cleanup) was found by existing tests and fixed; only immutable digest
and provenance metadata survive terminal cleanup. No real backend execution yet.

Test setup uses a temporary Python 3.11 environment outside the checkout. Test
dependencies were explicitly installed as runner prerequisites; application
imports/tests run under the audit guard. The system Python lacked required
dependencies and its venv bootstrap, so it was not used for application tests.

C3.2: version-2 profiles now require structured `launcher.kind` and `arguments`.
The generic template selects `slurm_sbatch`; IPD explicitly selects `ipd_qlogin`.
Sbatch receives a private worker script as argv, validates job/cluster replies,
and persists an ambiguous-submission fence. Worker publication holds the registry
lock and verifies the allocation session. Cluster identity accompanies scheduler
operations. Attach never allocates; start is explicit. Doctor supports profiles
without network/runtime probes. Authenticated health replaces quota probes and
pre-allocation identity negotiation rejects unsupported remote Python/builds.

Initial regression run had 11 expected failures (the audit blocked the old
attach path's attempted process; no process executed). Final focused HPC,
runtime and CLI contracts: **45 passed, 4 dependency warnings**, zero audited
boundary attempts. Existing replacement tests now explicitly exercise `start`;
new tests exercise the stricter non-allocating `attach` contract. Real scheduler
and runtime behavior remains unverified until exact-commit acceptance.

C3.3: gateway child identity and observed completion persist before transfer.
Lost submission responses use an owner-scoped operation lookup, never another
submission. Downloads use C2 non-consuming reads, verify bytes, atomically save
files/receipts and commit state before acknowledging. Transfer or parent storage
failure keeps the child completion and a `transfer_pending` parent; an explicit
`recover-transfer` or controller restart resumes that operation. Cancellation
after child completion and expiry clean parent staging without cancelling or
rerendering the child. Reservations, input retention, active deletion protection,
single-writer restart and original parent expiry cover the pending state.

Structured timing records use bounded allowlists and process-local monotonic
durations. Queue history across restart is marked unavailable rather than derived
from cross-process timestamps. Timings are operation/result metadata and receipt
data; the renderer writes a separate temporary journal rather than checksummed
manifest telemetry. Fake Modal sandbox and worker contracts execute without any
provider actions. C3.3 began with five failing regressions; focused recovery,
worker and lifecycle contracts now pass **67 tests**, with zero audited boundary
attempts. A broader intermediate run passed **351 tests with two intentional
A/B child-process skips**. In-process replacements cover those two contracts;
they do not establish fresh-interpreter acceptance.

The separate read-only review followed the first successful full/wheel run
(356 repository tests, 162 installed contracts, 82 matching package files).
It found three reproduced blockers: snapshot storage was outside the resource
limit, a child could return different identity metadata before acknowledgement,
and optional diagnostic loss blocked required outputs. Three regressions failed
before correction. Storage now includes snapshots/options; delivery checks the
child identity before any ack and preserves diagnostic optionality. Additional
review corrections require explicitly private gateway endpoints, keep controller
credentials separate from OAuth, reject bad launcher profiles before scheduler
actions, preserve ambiguous-stop fences, and clean validation-owned processes on
failed startup. Raw coordinate converters use the same stable object name.

The first contract-runner attempt was interrupted because a broad fixture copy
included an ignored local SIF image. The runner and source-identity regression now
copy only declared package source/resources. The SIF was untouched; no container
was started. Subsequent full/offline wheel runs passed with zero audited application
boundary attempts. Final exact counts and wheel identity follow after the last
review corrections are rechecked. No real render, SSH, allocation or Modal action
has occurred during implementation/review.

Final reviewed implementation verification:

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/blender-c3-test-env311/bin/python scripts/validate_c3_contracts.py --output /tmp/blender-c3-final
git diff --check
```

Repository: **373 passed, 2 intentional A/B process skips, 4 dependency warnings,
34.20s**. Installed wheel: **179 passed, 4 dependency warnings, 7.17s**. Both audit
logs report zero real process/network attempts. All **82 package files** match
source, wheel and installed bytes, and installed import paths are asserted.
Wheel SHA-256: `a59ca1f2a6054a7395091ded501feaf47465b8c3bf4833e9dacb4f242e6d33c4`.
Engine build: `ef64aec3b7ec0ec0363adb5adf7984998afc6420a7389edfc0cab4e8e2596ce7`.
Build and wheel installation ran offline with preinstalled test/build dependencies.

Final reservation review reproduced two more regressions: cancelling or expiring
a pending gateway transfer could release its prior dispatch reservation. Both
regressions failed before correction and now pass. Terminal gateway operations
conservatively charge the original reservation, independent of restart policy;
a short transfer retry cannot stand in for the earlier child GPU duration. No
new execution is authorized by recovery. Read-only follow-up reviewed these
changes, snapshot limits/cleanup, pre-ack identity checks, script/cluster argv,
token ownership and the validation runner's isolation/cleanup. Whitespace check
passed. C3 implementation and validation runner are ready for the requested
commit; exact-commit real acceptance is the next step, subject to prerequisites.

Modal implemented and contract-tested; real backend execution unverified.
No D work, deployment, hosted-policy modification, push or publication occurred.

### C3 exact-commit acceptance prerequisite result

Implementation and runner committed as
`1eb9ee41ffe44845b3be3b464aee8d1dcadf1562` on `codex/public-release`; no push.
After that commit, the acceptance runner executed:

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/blender-c3-test-env311/bin/python scripts/validate_c3_acceptance.py --commit 1eb9ee41ffe44845b3be3b464aee8d1dcadf1562 --output /tmp/blender-c3-acceptance-1eb9ee4
```

Exit **2**, `status=prerequisites`. It verified the exact clean tracked commit
and matching engine identity, then stopped because this workspace has no detected
local host Blender executable, configured PyMOL Python interpreter, or configured
private HPC profile. The required PSE fixture exists. Chain A/D and PTR/peptide
assertions, DSS export and both GPU renders remain **unexecuted**. The existing
container image was not substituted for host acceptance. No service, Blender,
SSH, scheduler, allocation, provider action or paid compute was started.

C3 implementation/contract coverage is committed. Real local/Slurm parity remains
blocked on those runtime/configuration prerequisites. Real filesystem/tunnel
recovery, controller/storage replacement under failure, physical power loss,
concurrent stress, and Modal worker/Volume recovery remain separately unverified.
The proposed personal Modal checkpoint is documented in
`docs/plans/c3-modal-validation.md`; it still requires explicit deployment/paid
execution authorization. This evidence update changes documentation only; the
reviewed package, engine identity, wheel and runner are unchanged.

## Starting point and scope

- Started 2026-09-07 on `codex/public-release`, HEAD `b1bb7d1c5d0d832b75c096eeea855b306815d79f`, matching the live private draft PR #1 head. No checkout, reset, commit, push or deployment.
- Starting `git status --short`: only `?? docs/plan/`. No tracked edits. This linked worktree has no configured Git remote; the GitHub connector verified `magnusbauer/blender_render` and read all 19 review threads and four review submissions.
- The supplied plan existed at `docs/plan/blender_render_implementation_plan_v3.md`, not the requested path. It is preserved unchanged; `docs/plans/blender-render-v3.md` is a byte-identical copy. Read all 605 lines. Read root AGENTS.md in full; no applicable nested AGENTS.md found.
- Scope: Milestone A only, local isolated tests. No Slurm allocation, Modal submission, production service changes, credential/privacy/budget changes, publication, or release acceptance claims.
- Reuse `export.py` bundles, `style.schema.json`, all existing preset JSON, `core.py`, `rendering.py`, `blender_custom_render.py`, queue/worker/backend implementations. No replacement framework or file-tree migration.

## Milestones

- [x] A: local checkpoint criteria proven by executed contracts and camera/scene evidence; see the final A/B closure table below. Compatibility tightening is documented; this is not remote release acceptance.
- [x] B: local developer-loop criteria proven by executed causal previews, scene inspection, preset restart/reuse and reference decisions; see the final A/B closure table below. No hosted validation is required for this milestone.
- [ ] C: lifecycle, operation identity, generic Slurm launch, side-effect-free reads, artifact recovery and runtime negotiation.
- [ ] D: thin consumer adapters, best-fit selection, compact results and explicit install routing.
- [ ] E: rebuilt wheel/runtime artifacts and executed backend/installed-host acceptance.

## Review findings mapped to milestones

All links are PR #1 inline comments. Current-code checks are static unless listed in test evidence; an open thread is not by itself proof of a current defect.

| Finding | Milestone | Starting-code verification |
|---|---|---|
| [Custom pastel identity](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528245) | A | CLI/PyMOL send implicit preset with style_doc; core gate accepts name/filename regardless of representation. Fixed in A; adapter-to-worker surface regression. |
| [Camera overwritten](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528248) | A | prepare_options replaced custom camera when preserve_view defaulted true. Fixed in A; full-JSON and override regressions. |
| [Duplicate validation](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618504) | A | Shadowed lists, duplicate structural validation and weak Blender import fallback confirmed. Consolidated in A; semantic checks retained. |
| [Local authoring/catalog](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528249) | A/B/D | Built-in filenames exist; no user save/reuse or editing surface. A provides shared catalog/resolution; B owns persistence/workbench, D owns MCP expansion. |
| [Temporary export identity](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528254) | C | export.py uses UUID combined selection; semantic fingerprint recovery needs dedicated regression. |
| [qlogin coupling](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528256) | C | hpc_remote.py hardcodes qlogin; confirmed statically. |
| [Partial result recovery](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528258) | C | Client.download rebuilds paths from current artifacts; no receipt. Confirmed statically. |
| [Unexpired results on replacement](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528264) | C | deploy waits only for active queue/running jobs before stop. Artifact-loss recovery remains untested. |
| [Outer/child identity](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528268) | C | Gateway submits same content with new key without explicit rerender. Needs operation-lifecycle regression. |
| [Late worker admission release](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528269) | C | worker_http releases only when task done within grace, no late completion release. Confirmed statically. |
| [Raw-upload selection](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528272) | C | CLI raw upload does not pass selection. Confirmed statically. |
| [Read-only allocation](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618485) | C | stdio read helper uses configured_client without attach_only. Lifecycle consequences require regression. |
| [Unnamed attachment format](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618491) | D | Optional metadata exists; historical suffix-default finding deferred to input-adapter tests. |
| [Recent-job recovery](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618494) | C/D | Existing tool surface lists temporary structures; owner-scoped operation recovery remains deferred. |
| [Local-only install routing](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618500) | D/E | Installed routing not reverified; do not claim data leakage or fixed routing. |
| [One submission surface](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618507) | D | Compatibility import/submit and preferred render coexist; deliberate compatibility decision deferred. |
| [Legacy widget](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618514) | D | FastMCP private-hook subclass remains; retirement deferred. |
| [Compact result](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618517) | D | Full manifest/result presentation remains; PNG/scene option distinction covered in A only. |
| [Installed attachment-to-PNG gate](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618521) | E | No fresh installed-host execution in A; historical reports remain qualified. |

## Milestone A implementation

- `spec.resolve_options()` is the shared library entry point. `service.prepare_options()` delegates; direct legacy worker requests translate through it. CLI/PyMOL/MCP requests still converge through the existing service/renderer, with no new service or engine.
- `presets.py` catalogs the six existing selectable IDs, descriptions, aliases, built-in namespace, source JSON, revision, defaults, representation/material requirements and schema. All seven pre-existing JSON assets, including the reference-only asset, remain byte-identical to HEAD. `blender-render presets` retains its ID list and adds catalog metadata. No user-preset persistence or hosted tool redesign in A.
- One cached Draft202012Validator supplies structural validation. Nonblank chain/material/blend-name rules moved into the schema. Material references, residue-range ordering and required cutout frame axes remain semantic checks. Missing Blender-side validation dependencies now fail explicitly.
- Full JSON overrides the adapter's implicit preset identity. The strict reference gate follows its representation/selection layout rather than preset names or output filenames. Changing materials/colors/camera preserves the reference guard; a surface-only style does not inherit cartoon/sticks requirements.
- A queued request contains the resolved JSON, schema version, preset source/revision, required capabilities and SHA-256. The worker verifies the hash, writes that exact JSON to Blender, and records it in the diagnostic manifest. Rough/color/output options resolve before hashing; the gateway forwarding shape does not apply rough twice. Capability metadata is declarative, not runtime negotiation.
- CLI adds `--overrides-json` and `--preserve-view` / `--no-preserve-view`; PyMOL adds `overrides_json` and `preserve_view`. Objects merge recursively and arrays replace. Complete JSON is authoritative. On preset execution, saved pose is the default; any explicit camera override or `preserve_view=false` retains the preset camera plus edits. For reorientation, use the existing `camera.mode="override"` controls; intrinsics in `pymol` mode use `override_intrinsics=true`.
- Explicit dimensions beat viewport-derived defaults; explicit convenience flags beat JSON fields; resource policy runs last. PNG remains the transport output. `save_blend` in JSON/overrides or an explicit adapter option requests a scene. The implicit CLI/PyMOL false default no longer cancels a scene explicitly requested in full JSON. An explicit PyMOL `blend_output` also requests scene creation.

Example shared-library use (no render or service startup):

```python
from blender_render.spec import resolve_options
request = resolve_options({
    "preset": "green_paint",
    "overrides": {"camera": {"mode": "override", "yaw_deg": 30, "lens_mm": 70}},
    "save_blend": False,
}, bundle)
# request["style_doc"] is the existing version-1 style JSON sent to Blender.
# request["render_spec"] records the digest and provenance.
```

## Acceptance and evidence

Test environment: existing `.venv/bin/python` (Python 3.11.15), with `BLENDER_RENDER_HOME=/private/tmp/blender-render-milestone-a/config`, `PYMOL_BRIDGE_SESSION_ROOT=/private/tmp/blender-render-milestone-a/sessions`, `PYMOL_BRIDGE_TMP_ROOT=/private/tmp/blender-render-milestone-a/runs`, and `PYTHONDONTWRITEBYTECODE=1`. Tests use isolated stores and fake workers; no live service connection or compute submission.

| Executed check | Result |
|---|---|
| Baseline: `.venv/bin/python -m pytest -q -p no:cacheprovider` | **111 passed, 4 warnings**, 3.96s. No pre-existing test failures. |
| Existing suite after initial refactor, same command | **111 passed, 4 warnings**, 3.77s. |
| First focused run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_spec.py tests/test_style_schema.py tests/test_pymol_adapter.py tests/test_render_server.py` | **1 failed, 53 passed**, 0.25s. New regression exposed a legacy adapter dropping an unknown field. Fixed by rejecting unknown request keys before staging/dispatch. |
| Focused run after fix and MCP coverage, same command | **59 passed, 2 warnings**, 0.90s. |
| Expanded suite: `.venv/bin/python -m pytest -q -p no:cacheprovider` | **145 passed, 4 warnings**, 4.08s. |
| Final suite after diff-review corrections, same command | **145 passed, 4 warnings**, 3.92s. **34 new tests**, no unresolved new test failures. |
| `.venv/bin/python -m build --wheel --no-isolation --outdir /private/tmp/blender-render-milestone-a/wheels /private/tmp/blender-render-milestone-a/wheel-source` | Could not import `setuptools.build_meta`; existing dev environment lacks setuptools and wheel (and pip). This was an environment limitation, not a passing build. |
| `.venv/bin/python -m build --wheel --outdir /private/tmp/blender-render-milestone-a/wheels /private/tmp/blender-render-milestone-a/wheel-source` | Isolated build succeeded; repeated successfully after final source corrections. Source/package assets copied to temporary staging, declared build dependencies installed only in temporary build environments. Existing `.venv` unchanged. |
| `/private/tmp/blender-render-milestone-a/installed-env/bin/python -m pip install --no-deps --no-index /private/tmp/blender-render-milestone-a/wheels/pymol_plugin_bridge-0.2.0b1-py3-none-any.whl` | Installed successfully into a new temporary venv. Verified core/spec imports resolve inside that venv, outside the checkout. |
| `/private/tmp/blender-render-milestone-a/installed-env/bin/python -m pytest -q -p no:cacheprovider /private/tmp/blender-render-milestone-a/wheel-check` | **59 passed, 2 warnings**, 0.78s. CWD was installed `lib/python3.11/site-packages`; tests copied outside checkout. PYTHONPATH put installed site first and existing dev site second for test dependencies. This proves wheel module/assets/contract coverage, not a clean dependency install or live renderer validation. |
| Blender runtime probe: `/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-use-system-env --python-exit-code 1 --python blender_render/runtime_probe.py` | Sandbox attempt exited 139 during Blender startup. Authorized unsandboxed retry started Blender **5.0.1**, then exited 1: `ModuleNotFoundError: No module named 'molecularnodes'`. No render executed. The dev interpreter also has no `pymol2`. Runtime not installed or changed. |
| `git diff --check` | Passed; final review repeated after documentation. |
| Plan/preset/public fixture integrity checks via Python SHA-256 and `git show HEAD:<asset>` | Canonical plan byte-identical to original; all seven preset assets byte-identical to HEAD; all five public structures match `examples/structures.json`. |
| Final source audit using `ast.parse` and `zipfile.ZipFile.read` | All eight changed/new package Python modules and the new test module parse; all eight package modules plus schema are byte-identical between the final wheel and reviewed checkout. Final wheel SHA-256: `5a25b79175c42ae80add0e50c5c05d26c20766c2aefb3ba2d4238bd847f6c05e`. |

Existing warnings are Starlette/httpx, anyio BlockingPortal, Authlib jose and Authlib httpx deprecations. The focused/wheel suites encounter only the latter two. No dependency warning was introduced by A.

Focused regressions in `tests/test_spec.py` prove full JSON / preset override equivalence for pastel and green paint, array replacement, caller input isolation, explicit camera and dimension precedence, public limits, unsupported fields rejected before staging/dispatch, retained semantic checks, stable preset identifiers/aliases, exact worker snapshot/hash, tamper rejection, rough forwarding without repeated reduction, PNG-only versus explicit scene, reference-gate preservation, and full CLI/PyMOL surface-only transactions. In-process HTTP/stdio MCP tests prove shared option and scene semantics; they do not prove installed-host display or backend execution.

Retained baseline evidence (historical, no fresh pixel comparison):

- Plan SHA-256: `75f3ccfce77a62dbfde24773d67879bd6589597a64ba3846d6aeede6a81c876e`.
- Pastel DNA image `.validation/1bna-mac.png`: `4f5c386fcf96259ac9568e4249256ed2c23337626099443cf692815c5cfc2859`; manifest `.validation/1bna-mac.manifest.json`: `983f3822b7a68e47f956199d0da4ff81d5a3f5896d454f62375aeb940dc97c84`.
- Green-paint image `examples/rendered/1sps_dried_paint.png` (also `.validation/1sps-mac-pse.png`): `838c8025fe7516b12f92307a3a44f7e07279d6a1f7e2b5711c64c0bed41296a8`; manifest `.validation/1sps-mac-pse.manifest.json`: `99b9b1050cfcfeb9da63c92bffea330e439d1e7197a0e52a0de781e1cdd9122d`.
- Both historical manifests record Blender 5.0.1, Molecular Nodes 4.5.10, Python 3.11.13, NumPy 1.26.4 and Biotite 1.6.0. They are preserved evidence, not proof that the current runtime is complete.

**At the initial milestone handoff, not executed / unverified:** fresh local PNG/scene or camera visual comparison, fresh PSE export, Slurm, Modal, production restart/deployment, regenerated SIF, clean full dependency install, installed ChatGPT/Codex attachment/display, and all B–E acceptance scenarios. The initial runtime probe failure was not counted as a pass. The user-requested local follow-up below supersedes the local runtime/export/render limitation; remote and installed-host checks remain unverified.

## Follow-up: reuse the existing PyMOL and Blender runtimes

The user's request to verify results prompted a more complete runtime lookup. The earlier environment limitation was too broad: the development `.venv` lacks PyMOL, but the existing validation configuration already identifies a working Anaconda Python 3.9 with **PyMOL 2.5.4** and its `pymol2` API. The `pymol2` import is unchanged from the starting commit. It creates an isolated PyMOL instance for CLI file export; the live PyMOL adapter uses the application's `cmd` directly. No new PyMOL installation is required.

The existing Blender **5.0** extension directory contains Molecular Nodes. Adding that directory and the previous `.validation/config/runtime/site` to the subprocess import path makes the probe succeed with **Blender 5.0.1, Molecular Nodes 4.5.10, Python 3.11, NumPy 1.26.4 and Biotite 1.6.0**. The earlier bare/factory-startup probe omitted the extension import path. No package, credential, application configuration or running service was changed.

For reproducibility, `PYMOL_PYTHON` below is the `pymol_python` value read from the existing `.validation/config/config.json`; `MN_PARENT` is the existing Blender 5.0 `extensions/blender_org` directory. These are machine-local execution inputs, not paths added to shareable presets. Outputs and the validation driver are under `/private/tmp/blender-render-milestone-a-live`.

Executed follow-up checks:

1. `"$PYMOL_PYTHON" blender_render/export.py examples/1sps_pose.pse /private/tmp/blender-render-milestone-a-live/1sps-payload.json`: **passed**. Exported 1,106 atoms / 110 residues / chains A,D; DSS ran. All 18 camera values exactly match `examples/1sps_pose.json` (maximum difference 0).
2. `"$PYMOL_PYTHON" scripts/validate_export.py /private/tmp/blender-render-milestone-a-live/export-validation.json`: **passed**. Two transformed enabled objects, 3,772 atoms, disabled-object exclusion, camera, atom colors and insertion codes preserved. Maximum coordinate error **0.000008529923 angstrom**.
3. Existing Blender `--background --factory-startup --python-use-system-env --python-exit-code 1 --python blender_render/runtime_probe.py`, with `PYTHONPATH` including the two existing runtime locations above: **passed**, matching pinned versions.
4. `.venv/bin/python /private/tmp/blender-render-milestone-a-live/verify_renders.py`: four real renders through the current `prepare_options -> HostBackend -> shared rendering transaction -> Blender` on an isolated local Metal worker. `BLENDER_RENDER_HOME` points to a new temporary configuration root; `BLENDER_RENDER_SITE` points read-only at the existing validation dependency site; `PYTHONPATH` includes the existing Molecular Nodes extension parent. All four outputs are **640 x 480 RGBA** with transparent and opaque pixels, at 32 samples. The driver retains only the explicit output copies; per-render directories were cleaned and the dedicated worker was stopped (socket removed).

| Case | Result | Scene requested | Resolved JSON SHA-256 |
|---|---|---|---|
| Pastel saved PSE pose | **passed**, 3.375s; `mn_cartoon`, `mn_native_sticks`, ligand spheres off | No; PNG only | `6c4ded8ca823f8cfe0da533ff4adda14c4899d42e03ec4a09a83d32c49df7cd6` |
| Green paint saved PSE pose | **passed**, 2.459s; `mn_cartoon`, `mn_native_sticks`, ligand spheres off | No; PNG only | `7d6a2eab3a98b5a8f372d62a169e3fe104179f2e3f135a09f4347c5397ef6a07` |
| Custom surface via full JSON, with implicit pastel ID deliberately retained at input | **passed**, 1.508s; no cartoon/sticks, no incorrect pastel gate | Yes; `.blend` produced | `0554e28d32890410a14f61004402fdf469e4b20eceda428e57f6a3f55cd15ff6` |
| Equivalent custom surface via preset plus overrides | **passed**, 1.354s | Yes; `.blend` produced | `0554e28d32890410a14f61004402fdf469e4b20eceda428e57f6a3f55cd15ff6` |

5. Reopened `surface_full.blend` in background Blender with `--disable-autoexec` and `/private/tmp/blender-render-milestone-a-live/check_scene.py`: **passed**. Camera transform differs from the saved-pose baseline, lens property is 80 mm, resolution is 640 x 480, film transparency is enabled, engine is Cycles. Projection remains orthographic as supplied by the PSE; the lens property is retained but does not control orthographic magnification.
6. Numeric image comparison of the two surface renders: resolved JSON is identical; images differ in **two channel values by 1/255**, mean absolute channel difference **0.000001627604** on the 0–255 scale. Passed maximum-one-level / mean-below-0.00001 tolerance. They are not claimed to be bit-identical. PNGs were visually inspected: expected cartoon versus surface representations, same saved composition for pastel/green, no edge clipping.

The current green-paint manifest also matches the earlier `.validation/1sps-mac-pse.manifest.json` exactly for materials, styles, camera specification, world, lights, geometry and the actual camera matrix (maximum matrix difference 0).

Fresh local evidence files: `export-validation.json`, `render-validation.json`, `scene-validation.json`, four PNGs, four manifests, and the two explicitly requested scenes in the temporary directory above. These are low-sample acceptance images, not final publication renders. No Slurm allocations, Modal submissions, uploads, deployments, or production-service changes occurred. Remote/backend-wide and installed-host acceptance remain unverified.

## Decisions, conflicts and next task

- Schema remains version 1.0; full style documents remain supported. Objects merge recursively; arrays replace wholesale. Preset identity is provenance, never sufficient to impose representation gates.
- Saved-pose preservation is a default for preset execution. Explicit full-JSON camera and explicit camera overrides win; preserve_view=false selects the preset camera. Explicit dimensions win over viewport-derived defaults. Resource policy is applied after resolution.
- Keep PNG output default and scene creation explicit, including save_blend inside a complete JSON/override document. Transport filenames remain controlled by the service.
- Root AGENTS requires preset 1 cartoon/native sticks; preserve these checks for the actual cartoon/sticks specification, without applying them to a surface-only custom representation. No security requirements weakened.
- Request snapshot metadata changes normalized job records. Existing legacy requests are translated, but historic stored fingerprints and already-running older workers were not migrated or validated; any future upgrade must follow the existing drain/stop rule. No running service was modified.
- The plan's broader hosted allowlist/capability negotiation, fit-without-saved-pose and user/project catalog persistence need later work; A must not pretend static metadata is a live runtime probe.
- No genuine instruction conflict required weakening security or release gates. The requested canonical plan path was supplied without moving the user-owned original.
- Next bounded task after A: Milestone B's local inspect/validate/save/reuse and fresh-process preview foundation, with source identity and isolated public fixtures. Stop for review before starting it.

Stopped after Milestone A. At the user's follow-up request, this milestone is committed on `codex/milestone-a-json-contracts`, branched from the starting HEAD. At the user's further request, the original `docs/plan/` is also tracked on this branch, with its contents unchanged and byte-identical to the canonical plan. PR threads were read but not modified; no remote push or PR update is included.


## Follow-up: eight reference presets and complete gallery (2026-09-07)

This is the user's subsequent, explicit request for eight image-inspired presets, public structure matching, a refined cutaway with colored pocket residues, and a README gallery. It does not start Milestones B–E. Starting commit: `24629ce781f4f1e15d271767e2a802fc5022f10b`, branch `codex/milestone-a-json-contracts`. The starting checkout was clean; current changes are this follow-up. No reset, checkout from main, broad migration, remote push or PR publication occurred. The original plan, `docs/plan/`, AGENTS security requirements and seven legacy preset JSON files remain byte-identical to the starting commit.

- [x] Reuse the shared engine and resolver; add eight named presets with `reference_01`–`reference_08` aliases.
- [x] Iterate actual local Blender renders, including the supplied sessions and downloaded public structure candidates.
- [x] Fix camera-facing surface cuts and colored flat walls; retain the lower shell and complete interior geometry.
- [x] Add complete nearby protein residues with element-colored native sticks.
- [x] Render and visually inspect all 14 catalog entries for the README; link each identifier to its JSON.
- [x] Test JSON/override equivalence, scene opt-in, shader/camera/geometry behavior and the installed wheel.
- [x] Review the diff and retain exact evidence and remaining limitations.
- [ ] Identify the original structures behind references 1, 5 and 6; exact image reproduction remains unverified.
- [ ] Slurm, Modal, deployment, clean full dependency installation and installed-host attachment/display checks remain unexecuted.

Implementation: optional fields expose native surface smoothing, nucleic-base thickness, camera roll/fit/projection/focus, element colors, gradient pigments and a frosted grid. These extend the existing schema and scene builder; they do not introduce a second renderer. Explicit camera precedence and PNG-only defaults remain intact. The eight preset contracts also prove full JSON and preset-plus-overrides equivalence. The broader review-to-milestone mapping above remains the historical PR context; this follow-up does not claim to resolve deployment or workbench findings.

The final cyan/mint preset cuts only isolated surfaces. A 12-angstrom pocket radius uses left/right 0.85, top 2.0, bottom 1.0 and back 1.2. Earlier full-height cuts exposed too much of the bottom; three localized candidates were compared, and the final version combines the retained lower shell with an open upper edge. A 5-angstrom proximity selection expands to **19 complete residues / 165 atoms**. Nitrogen/oxygen colors were checked in the stored molecular attributes and inspected in the PNG. The ligand remains pink.

Real 8EA6 geometry audit: both cuts preserve the same **187,960 interior vertices / 308,806 polygons**, SHA-256 `3b90a56d64c5a0da6563f42a511e1ca54c25090687b04088387771f84e87adf8` before and after each Boolean. Both resulting surfaces have **zero boundary edges and zero nonmanifold edges**; each has three flat, pigment-colored cut faces. Closed surfaces and visible interior were verified together, rather than substituting caps that obscure the pocket. Sanitized evidence is committed in `examples/presets/cutaway_checks.json`.

Public examples: 8EA6, complete 7PZB, 8AA5, 8AW3 and 1AZM. The six legacy previews use the existing public 1SPS pose fixture. The complete 7PZB entry restores DNA E/F absent from the supplied session. Public 7R6R was tested but rejected as a less suitable silhouette. Reference 1 uses 8AW3 as an analogue; reference 5 uses acetazolamide in 1AZM for a real sulfonamide; reference 6 uses 8AW3 without inventing the absent organic ligand. Source URLs, coordinate hashes, views, preset hashes and thumbnail hashes are in `examples/presets/gallery.json`. No supplied PSE, private coordinates, reference screenshot, credentials or machine-specific paths are added to the gallery. See `docs/reference-presets.md` for selection assumptions and match limitations.

### Executed checks

Working directory for repository commands is the edited checkout. `$TASK` below is `/private/tmp/blender-reference-presets`. The existing development Python 3.11.15, PyMOL 2.5.4 and Blender 5.0.1 / Molecular Nodes 4.5.10 were reused. Tests set `PYTHONDONTWRITEBYTECODE=1`, `BLENDER_RENDER_HOME`, `PYMOL_BRIDGE_SESSION_ROOT` and `PYMOL_BRIDGE_TMP_ROOT` to task-specific temporary locations. Render checks use `PRESS_BIOTITE_CCD_PATH=$TASK/ccd/components.bcif`, the existing `BLENDER_RENDER_SITE` and configured Molecular Nodes extension environment. Every local worker was dedicated to this task and explicitly stopped.

| Exact command / check | Result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider` during development | 155 passed / 4 warnings, then 172 passed / 4 warnings. These intermediate runs preceded the final pocket-residue test. |
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_reference_presets.py tests/test_spec.py` | 53 passed, 2 warnings, 1.76s before the final additional test. |
| Final `.venv/bin/python -m pytest -q -p no:cacheprovider` | **173 passed, 4 warnings, 3.94s**. Log: `$TASK/pytest-final.log`. |
| `"$BLENDER" --background --python-use-system-env --python-exit-code 1 --python scripts/validate_reference_render.py -- "$TASK/blender-checks-final"` | Passed: native nucleic thickness changes geometry without mutating the shared default; ordinary/procedural cut surfaces stay closed with unchanged interiors; four perspective/orthographic landscape/portrait fits retain all vertices; explicit focus/scale; actual gradient and frosted-opacity renders. JSON: `$TASK/blender-checks-final/checks.json`. |
| `.venv/bin/python "$TASK/render_final.py"` | Eight actual **1600-pixel-wide / 128-sample** Cycles PNGs. The first two driver setup assertions failed before rendering because they omitted resolver-added transport defaults; correcting those expectations allowed all eight to render. |
| `.venv/bin/python "$TASK/render_pockets.py"` | Three actual public-8EA6 candidate renders at 1100 × 737 / 48 samples. All geometry audits passed; images compared before choosing the final opening. |
| `.venv/bin/python "$TASK/render_final.py" 2 7` | Final revised cutaway and public 8AA5 gallery replacement passed at 1600 pixels / 128 samples. Actual atom-color, complete-residue and interior-preservation assertions passed. |
| `.venv/bin/python "$TASK/render_legacy_gallery.py"` | Six actual legacy-preset PNGs at **960 × 720 / 64 samples**. Pastel and green retain cartoon/native sticks and no atom spheres. All 14 gallery outputs requested PNG only, and no scene was returned. |
| `.venv/bin/python "$TASK/package_gallery.py"` | All 14 README thumbnail links and JSON links exist; every catalog identifier appears; public source and SHA-256 records written. Contact sheet visually inspected. |
| `.venv/bin/python "$TASK/build_wheel.py"` then `.venv/bin/python -m build --wheel --outdir "$TASK/wheels" "$TASK/wheel-source"` | Final isolated build passed. No build dependencies installed into the existing dev environment. |
| `"$TASK/installed-env/bin/python" -m pip install --no-deps --no-index "$TASK/wheels/pymol_plugin_bridge-0.2.0b1-py3-none-any.whl"` | Passed in a fresh temporary venv. |
| `"$TASK/installed-env/bin/python" -m pytest -q -p no:cacheprovider "$TASK/wheel-check"` | **83 passed, 2 warnings, 0.73s**, outside the checkout. CWD is the installed site; PYTHONPATH puts that site before the existing dev dependencies. Imported core path was verified inside the temporary venv. This is not a clean dependency-install test. |
| Python `zipfile`, SHA-256 and AST comparison | All **65 package Python/JSON files** match the wheel exactly; Python parses; legacy presets, plan and instructions remain unchanged. Wheel SHA-256 `e9933992ca3d80d5dc40c397368bd5adc46222be654604f84dfe65307b835bac`. |
| `git diff --check` and source/gallery review | Passed. Final review checks links, source provenance, isolated cuts, camera semantics and explicit scene behavior. |

Failures and limitations remain distinct. The inherited four dependency deprecation warnings are unchanged. Factory-startup validator attempts could not import `databpy`; using the configured extension environment then exposed a missing CCD database. Those failed attempts are not passes. A trial exercised the existing CCD recovery fallback and populated runtime/shared CCD caches; their prior contents were not fully recorded, so this task does **not** claim those caches stayed unchanged. The explicit CCD override now targets only task-local storage, and final runs used it. No running production service, credentials, privacy settings, budgets or application configuration were changed. PSE conversion ran under a short-lived OS sandbox with no network/credentials and writes limited to task temporary state; the existing PyMOL printed a missing-license message in that empty home, but export completed successfully. No new PyMOL was installed.

No unresolved new test failures remain. There is no fresh Slurm allocation, Modal render, deployment, publication, remote push or installed Codex/ChatGPT result-flow acceptance. Presets 1, 5 and 6 are intentionally labeled approximations rather than perfect matches. The proposed next bounded task is to establish their exact source structures and refine only those three reference matches; Milestone B remains a separate, unstarted task. Stop here for review.


Final appearance comparison: `.venv/bin/python "$TASK/compare_legacy.py"` rendered `preset_5_black_interface` using a temporary copy of the starting commit's renderer, without checking out or changing the repository. The first attempt lacked the Python package directory layout and failed to import `blender_render`; correcting the temporary layout allowed the comparison to pass. Current versus starting image differs in one channel value by 1/255 (mean absolute difference **0.000000361690** on the 0–255 scale). Its dark appearance is inherited. `legacy-baseline-check.json` records the result. A separate attempt to compare supplied PSE file hashes could not proceed because the initial exported payloads did not store source-file hashes; that retrospective hash check remains unverified. Conversion only had read access to those files, and no save/overwrite operation was performed on them.

## Follow-up: glow, thicker pocket sticks and glass refinement (2026-09-07)

The requested visual refinement stays within the existing shared renderer. The cutaway ligand now declares an emission-backed fog-glow pass in the shared schema and compositor, while keeping its pink material below clipping. The pocket sidechain sticks remain element-colored and use radius `0.3` so the complete nearby residues read through the opening. The ivory/lime shell and cyan nucleic acids use higher transmission, lower frosted opacity and a finer, very light grid; the grid and glass controls remain explicit preset values rather than hidden renderer defaults.

The two affected presets were rendered again through the local Blender 5.0.1 / Molecular Nodes 4.5.10 worker with the task-local CCD path. The verified outputs are `02_cyan_mint_cutaway.png` and `06_ivory_lime_frosted.png` in the task's temporary final directory; their checked-in thumbnails and gallery SHA-256 records were regenerated from those outputs. The actual renders preserve the cutaway interior and show colored sidechains, a controlled pink ligand halo, translucent cyan DNA and the pale frosted shell.

| Exact command / check | Result |
|---|---|
| `BLENDER_RENDER_HOME=/private/tmp/blender-reference-presets/config BLENDER_RENDER_SITE=/Users/mb/Documents/blender_bridge-release/.validation/config/runtime/site .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_reference_presets.py tests/test_spec.py tests/test_style_schema.py` | **81 passed, 2 warnings**, 0.88s. |
| `PRESS_BIOTITE_CCD_PATH=/private/tmp/blender-reference-presets/ccd/components.bcif BLENDER_RENDER_HOME=/private/tmp/blender-reference-presets/config BLENDER_RENDER_SITE=/Users/mb/Documents/blender_bridge-release/.validation/config/runtime/site PYTHONPATH=/Users/mb/Library/Application\ Support/Blender/5.0/extensions/blender_org .venv/bin/python /private/tmp/blender-reference-presets/render_final.py 2 6` | **Both renders passed**. Cyan/mint: 1600 x 1072, 128 samples, style SHA-256 `eb752c5112c634212686b736788e9e17370a98f227e707dd9e31af25c8d504b2`, PNG SHA-256 `b3dc3df812f9a411c8e9f7ed64af1519c88fb1d51b7af2f85ef1654e5b5d03fb`. Ivory/lime: 1600 x 900, 128 samples, style SHA-256 `7adf4d94ae473ec79d8c69823306cb803b6394a814bbebf724dfe4c5e6562bf7`, PNG SHA-256 `a0217c843fc820c6bb1a9e5cd856d8c79b3af5f6e4feeac8e36d250bcd3122b7`. |
| `.venv/bin/python /private/tmp/blender-reference-presets/package_gallery.py` | **Passed**. Regenerated the two thumbnails, `examples/presets/gallery.json`, `examples/presets/cutaway_checks.json` and the isolated delivery bundle. |
| `.venv/bin/python /private/tmp/blender-reference-presets/build_wheel.py && .venv/bin/python -m build --wheel --outdir /private/tmp/blender-reference-presets/wheels /private/tmp/blender-reference-presets/wheel-source` | **Passed**. Fresh isolated wheel `pymol_plugin_bridge-0.2.0b1-py3-none-any.whl` built without changing the development environment. |
| `PYTHONPATH=/private/tmp/blender-reference-presets/installed-env/lib/python3.11/site-packages /Users/mb/Documents/blender_bridge-release/.venv/bin/python -m pytest -q -p no:cacheprovider /private/tmp/blender-reference-presets/wheel-check` from the installed site directory | **83 passed, 2 warnings**, 0.79s. The temporary installed environment itself has no pytest package, so the existing development pytest runner was used with the installed wheel first on `PYTHONPATH`; this is an installed-wheel check, not a clean dependency-install test. |
| `.venv/bin/python -m pytest -q -p no:cacheprovider` | **174 passed, 4 warnings**, 4.50s. Warnings are inherited dependency deprecations; no new test failures. |
| `git diff --check` | **Passed** before the final commit review. |

No new remote, Slurm, Modal, production-service, credential or deployment checks were performed. The supplied references remain visual targets rather than exact pixel-match claims; the original structure identity for references 1, 5 and 6 remains unresolved. The next bounded task is a final full-suite/build verification of this refinement, then stop for review.

## Follow-up: viewer-facing cut, continuous ligand rims and clean vivid grids (2026-09-07)

Starting HEAD: `9cabb91225d4baff53c088a3dd35ff77fce7117b`, branch `codex/milestone-a-json-contracts`, in the existing release worktree. This continues the user's visual corrections and subsequent request for stronger colors. The separate dirty `master` checkout was not edited. No branch reset, migration, deployment or publication occurred. AGENTS, both plan paths and legacy preset JSON files remain byte-identical to HEAD. This does not start Milestones B–E.

- [x] Resolve camera orientation once for camera-relative cuts and final framing, including explicit yaw, pitch and roll. Keep the open side toward the viewer and use screen up for the vertical walls.
- [x] Replace the rejected cellular ligand treatment with continuous grazing-angle white/pink rims and a small fog glow. Retain pink centers, native sticks and element-colored complete pocket residues.
- [x] Replace the rejected grain/irregular-net appearance with isolated, uniformly spaced quad-cell overlays on both the glass shell and cyan DNA. Cull rear-facing cells so the glass does not show overlapping nets. Original molecular geometry stays intact.
- [x] Match the supplied mesh close-up, then deepen cyan and lime in response to the user's approval of the grid and request for more vivid colors. Keep ivory areas and thin white lines.
- [x] Refresh the two README previews, full-resolution glass image, grid-detail link, public provenance and geometry evidence.
- [x] Execute local molecular previews, Blender behavior checks, repository tests, installed-wheel tests and final diff/hash review.
- [ ] Exact source identity and complete composition match for reference 6 remain unverified. The revised example uses public 7PZB with explicit A/B colors and DNA C/D residues 5–12. Its ligand visibility and silhouette still differ from the reference.

The cut retains the lower shell and clean pigment-colored walls. Its 12-angstrom pocket uses left/right 0.95, top 2.7, bottom 1.0, back 0.9 and an in-frame roll of -10 degrees. Both actual 8EA6 cuts retain **187,960 interior vertices / 308,806 polygons**, with identical before/after SHA-256 `4c00f0cf0d73e501b942ce2ef65f7203df3d014f23bddf220ca90b2b3da3824b`. Both surfaces have zero boundary/nonmanifold edges and three flat cut faces. The complete pocket selection remains 19 residues / 165 atoms with verified N/O/C colors. This hash differs from the historical audit because ligand radius changed; equality is checked within each current cut, not fabricated by restoring a historical hash.

The final glass audit records **16,498 surface cells and 3,935 DNA cells, all quadrilateral**, at 0.8 / 0.55 angstrom spacing and 0.018 angstrom line width. Both overlays preserve the original evaluated molecule. `examples/presets/frosted_mesh_checks.json` and `cutaway_checks.json` contain sanitized evidence. Overlays and cuts are authored for the resolved camera and must be regenerated if a saved scene's camera is changed later. Their remesh affects only decorative overlays, never atomic coordinates or the original cartoon/surface.

The final DNA pigment is `#22EDD8`, with stronger lime `#ADC92E`. As a diagnostic color check, selected cyan pixels had median RGB 139/210/202 before tuning and 105/201/191 afterward; the reference region measured 110/206/192. These are region samples, not registered pixel-match scores. The supplied screenshots and PSEs are not committed.

### Executed evidence

`$TASK` is `/private/tmp/blender-reference-presets`; `$BLENDER` is the installed Blender 5.0.1 executable. Local render commands used `PRESS_BIOTITE_CCD_PATH=$TASK/ccd/components.bcif`, task-local `BLENDER_RENDER_HOME`, the existing configured `BLENDER_RENDER_SITE`, and the installed Molecular Nodes extension on `PYTHONPATH`. All workers were isolated and stopped explicitly. The existing development environment and PyMOL were reused.

| Exact command/check | Result |
|---|---|
| `.venv/bin/python "$TASK/rematch.py" <folder> "$TASK/rematch-config/<case>.json" ...` | **28 successful local PNG renders**, visually compared across the cases listed below. Successful execution does not imply visual acceptance; earlier grids, poses and cellular highlights were rejected. |
| `.venv/bin/python "$TASK/rematch.py" rematch-final "$TASK/rematch-config/glass_clean.json" "$TASK/rematch-config/cut_final.json"` | Cutaway final: **1600 × 1072 / 128 samples**, 25.82s; geometry audit passed. The glass candidate was superseded by the DNA/grid and color refinement. |
| `.venv/bin/python "$TASK/rematch.py" rematch-dna-final "$TASK/rematch-config/glass_dna_grid.json"` | **1600 × 900 / 256 samples**, 69.35s; clean surface/DNA grid inspected at full size and in close-ups; geometry audit passed. |
| `.venv/bin/python "$TASK/rematch.py" rematch-vivid "$TASK/rematch-config/glass_vivid.json"` | Final glass: **1600 × 900 / 256 samples**, 74.34s; vivid colors inspected against the reference, with unchanged grid geometry. |
| `"$BLENDER" --background --python-use-system-env --python-exit-code 1 --python scripts/validate_reference_render.py -- "$TASK/rematch-checks-final"` | **Passed**: closed ordinary/procedural cuts, preserved interior, final camera/cut direction and up, four camera fits, explicit focus/scale, native nucleic thickness isolation, rendered gradient/opacity, continuous bright rims with preserved centers, visible front-facing quad cells and modern Blender glow size/quality. Log: `rematch-checks-final.log`. |
| `PYTHONDONTWRITEBYTECODE=1 BLENDER_RENDER_HOME=$TASK/test-home PYMOL_BRIDGE_SESSION_ROOT=$TASK/test-sessions PYMOL_BRIDGE_TMP_ROOT=$TASK/test-tmp .venv/bin/python -m pytest -q -p no:cacheprovider` | **179 passed, 4 inherited warnings, 4.73s**. Log: `rematch-pytest-final.log`. This full-suite run preceded the final pigment-only edit, which was subsequently covered by the actual render and final installed-wheel contract tests. |
| `.venv/bin/python "$TASK/update_rematch_gallery.py"` | **Passed**: two reviewed previews/presets, grid detail and geometry audits refreshed; all 14 gallery image/preset hashes verified. Existing delivery contact sheet/HTML also regenerated from current records. |
| `.venv/bin/python "$TASK/build_wheel.py"` then `.venv/bin/python -m build --wheel --outdir "$TASK/wheels" "$TASK/wheel-source"` | **Passed** in an isolated temporary build environment. Final wheel SHA-256: `e5bc1c663d8a4ae08b11988a412ccb38de77614033d9f803862344c58a193eb9`. The dev environment was not modified. |
| `"$TASK/installed-env/bin/python" -m pip install --force-reinstall --no-deps --no-index "$TASK/wheels/pymol_plugin_bridge-0.2.0b1-py3-none-any.whl"` | **Passed** in the task's temporary installed environment. |
| `PYTHONDONTWRITEBYTECODE=1 BLENDER_RENDER_HOME=$TASK/installed-test-home PYTHONPATH=$TASK/installed-env/lib/python3.11/site-packages <checkout>/.venv/bin/python -m pytest -q -p no:cacheprovider "$TASK/wheel-check"` from the installed site directory | **89 passed, 2 inherited warnings, 0.90s**. The tests were recopied from the final checkout; the installed wheel precedes dev dependencies. This is not a clean dependency-install or installed-host UI test. |
| Python zipfile/AST/SHA-256 comparison, `git diff --check` | **Passed**: all 65 package Python/JSON files match the built wheel; Python parses; 14 gallery hashes match; legacy presets, instructions and plan files are unchanged. |

Trial case inventory for the render command: `rematch-v1`: cut_open, cut_pink, glass_front, glass_turn, glass_duplex; `rematch-v2`: cut_rim, cut_soft, glass_split_a/b/c; `rematch-v3`: glass_dna_a/b/c; `rematch-v4`: glass_mesh_a/b/c; `rematch-v5`: glass_fine_a/b; `rematch-v6`: glass_quad_a/b; `rematch-v7`: glass_grid_a/b; `rematch-v8` and `rematch-v9`: glass_both; plus the four final-case renders above. The original DNA range trial used unsupported colon notation and selected too little DNA; final JSON uses the explicit `residue_range` field.

Failures are separate from passes. Two intermediate Blender assertions were corrected: the test initially expected the cut axis to point away from the viewer, and its first synthetic grid was too dense for the declared pixel-coverage criterion. The final executed assertions verify the correct viewer-facing sign and visible cells with transparent gaps. The first repository test run had **1 new test failure / 178 passes** because its error-message matcher expected the literal word `pattern`; the schema correctly rejected the invalid color, and the matcher was fixed. A `--no-isolation` wheel build failed because this dev environment has no `setuptools.build_meta`; the isolated build above succeeded. No new failures remain. No fresh baseline full-suite run was performed before edits; the inherited four dependency warnings remain, and no pre-existing failing test was identified.

No scenes were requested. No Slurm allocation, Modal render, remote publication, production-service modification, credential/privacy/budget change, Linux/HPC acceptance or installed Codex/ChatGPT/PyMOL UI check was performed. Those acceptance items remain unverified. Changes are left on the existing branch for review. The next bounded task, if requested, is exact-source/composition matching for the remaining analogue references; Milestone B remains unstarted.

## Milestone B: trusted source previews and optical depth of field (2026-09-07)

The user confirmed that Milestone A passed review, requested a commit of the reviewed visual changes, and authorized **Milestone B only**. Those preceding changes were committed first as `8b4b6a828c369961d8e27ba6e16afb2fe2a0eadf` (`Refine viewer-facing cutaway and vivid glass mesh presets`). B started from that commit on the existing `codex/milestone-a-json-contracts` branch in the release worktree, with a clean working tree after the commit. The separate master checkout was not edited. Read AGENTS and the complete 605-line plan and current progress record; no applicable nested AGENTS was found. Both plan paths and the original `docs/plan/` remain tracked and unchanged. The user's review statement is the new review context; no new GitHub review contents are inferred.

### Reuse, implementation and boundary

- [x] Reuse `spec.resolve_options`, existing built-ins/schema, exported StructureBundle, `_process_render_request`, and the shared `_render` scene builder. No replacement renderer, file-tree migration, service or per-preset rendering code.
- [x] Add `blender-render dev` / `python -m blender_render.developer`: catalog/schema/inspect/validate, fresh preview, save/reuse, and reference brief/decision. The separate repository developer skill authorizes trusted Python and JSON development when requested; the consumer skill and hosted execution permissions are unchanged.
- [x] Snapshot all package Python/assets, bundle and resolved JSON. Fresh controller and Blender processes load the snapshot. The default preview checkout is the working directory, including for an installed CLI; it never silently defaults to site-packages. Every loaded package module is checked against snapshot paths/hashes. No preview cache or existing-output overwrite.
- [x] Isolate Blender user resources, configuration, temporary files and CCD. Reuse the installed extension's 49 bundled wheels by extracting them into temporary state, with hashes of the exact extracted bytes. No dependency installation into the development environment. Temporary state is cleaned unless explicitly retained for debugging.
- [x] Implement the genuinely missing **camera depth of field** in `blender_custom_render.py`, after final camera placement. The prior `focus_style` affected framing, not optical focus. `camera.depth_of_field.focus_distance_angstrom` sets optical-axis focal distance at 0.01 Blender units/Å; `f_stop` controls aperture (default 2.8). Omission disables the effect. Explicit perspective override is required; raw viewport combinations and unknown/out-of-range fields fail. Camera transforms and molecular geometry stay unchanged.
- [x] Persist validated `user/name` and `project/name` presets outside site-packages, with explicit namespaces, revisions, digests and optional provenance. Built-ins cannot be overwritten. A second CLI process reads the saved preset, and a fresh preview reproduces it.
- [x] Bind developer-observed visual properties to an authorized local PNG digest/dimensions. A small catalog ranker or a developer-nominated base records supported overrides versus a dedicated new design. Palette overrides must affect a material actually used by the chosen preset. Raw reference bytes/paths do not enter a render request. The two acceptance references are public-fixture PNGs produced in this run and visually inspected; this is not a claim of automatic vision or matching the user's earlier external artwork.
- [x] Prove fixed-JSON Python development, fixed-Python parameter behavior/defaults, scene effects, source/runtime identity, saved-preset reuse, reference routes, and retained pastel/green behavior. Review the diff and preserve exact evidence.

The PR finding [local authoring/catalog](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528249), mapped to A/B/D above, is now addressed for the developer workflow. Consumer-side selection/catalog expansion remains D. Historical lifecycle, Slurm, cancellation, delivery and release findings remain mapped to C–E; B does not claim to fix them.

### Exact executed checks

Commands below ran from the edited release checkout unless noted. `$TASK` is `/private/tmp/blender-render-b`; `$CHECKOUT` is that checkout. `$BLENDER` is the existing `/Applications/Blender.app/Contents/MacOS/Blender`. `$SITE` is the existing `.validation/config/runtime/site`; `$MN` is the existing Blender 5.0 `extensions/blender_org` directory; `$CCD` is `/private/tmp/blender-reference-presets/ccd/components.bcif`. `$BUNDLE` is `/private/tmp/blender-render-milestone-a-live/1sps-payload.json`, the previously verified public 1SPS pose export. B reuses this bundle; it does not claim a new PyMOL export.

| Exact command/check | Result |
|---|---|
| Baseline `PYTHONDONTWRITEBYTECODE=1 BLENDER_RENDER_HOME=$TASK/baseline-home PYMOL_BRIDGE_SESSION_ROOT=$TASK/baseline-sessions PYMOL_BRIDGE_TMP_ROOT=$TASK/baseline-runs .venv/bin/python -m pytest -q -p no:cacheprovider` | **179 passed, 4 inherited warnings, 4.20s**. No pre-existing test failures. |
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_developer.py` during development | Initial **1 failed / 22 passed**: the equivalence test accidentally replaced the full camera instead of merging the preset's fields. Corrected the test fixture; **23 passed**, 0.27s. Two later regressions cover invalid output shape and installed-CLI checkout selection. |
| Final `PYTHONDONTWRITEBYTECODE=1 BLENDER_RENDER_HOME=$TASK/test-home PYMOL_BRIDGE_SESSION_ROOT=$TASK/test-sessions PYMOL_BRIDGE_TMP_ROOT=$TASK/test-runs .venv/bin/python -m pytest -q -p no:cacheprovider` | **204 passed, 4 inherited warnings, 8.43s**. Log: `pytest-milestone-b.log`. 25 new tests. Earlier expanded runs passed 202 and 203 tests before the final two regressions. |
| `.venv/bin/python scripts/validate_developer.py --bundle "$BUNDLE" --output "$TASK/milestone-b" --blender "$BLENDER" --runtime-path "$SITE" --runtime-path "$MN" --runtime-wheels "$MN/molecularnodes/wheels" --ccd "$CCD"` | **Passed: nine fresh previews and six independent scene reopenings**. Log: `milestone-b.log`; complete local evidence under `milestone-b/`. Six optical-focus renders use 640 × 480 / 128 samples; pastel, green and palette-only renders use 640 × 480 / 64 samples. Every render uses seed 7; focus comparisons disable adaptive sampling. |
| Scene inspection invoked by that runner: `"$BLENDER" --background --factory-startup --disable-autoexec <case>/render.blend --python-exit-code 1 --python scripts/inspect_developer_scene.py -- <case>/scene.json` | **Passed six times**, with isolated user resources. Actual DOF values, fixed camera matrices and fixed evaluated geometry verified in the saved scenes. |
| Two fresh CLI negative previews, commands retained verbatim in `$TASK/rejection-checks.json` | Unknown `camera.not_a_capability` rejected with exit 1 **before Blender started**; a temporary source copy declaring Blender 5.0.0 rejected the actual 5.0.1 runtime **before rendering**. These are expected rejection passes. They preceded only the final default-checkout argument correction; the final suite covers that correction. |
| `.venv/bin/python -m build --wheel --outdir "$TASK/wheels" "$TASK/wheel-source"` | **Passed**, isolated source/build staging; final log `build-milestone-b.log`. No changes to the existing development environment. |
| `.venv/bin/python -m venv "$TASK/installed-env"`, then `"$TASK/installed-env/bin/python" -m pip install --force-reinstall --no-deps --no-index "$TASK/wheels/pymol_plugin_bridge-0.2.0b1-py3-none-any.whl"` | **Passed** in temporary state. The wheel was refreshed after final source changes. |
| From `$TASK/installed-env/lib/python3.11/site-packages`: `PYTHONDONTWRITEBYTECODE=1 BLENDER_RENDER_HOME=$TASK/installed-test-home PYTHONPATH=$TASK/installed-env/lib/python3.11/site-packages "$CHECKOUT/.venv/bin/python" -m pytest -q -p no:cacheprovider "$TASK/wheel-check"` | **114 passed, 2 inherited warnings, 1.20s**. Five test files copied outside the checkout; installed package first on PYTHONPATH. Log: `installed-tests-milestone-b.log`. This is installed-wheel contract coverage using existing dev dependencies, not a clean dependency installation or installed-host UI acceptance. |
| Python ZIP/SHA-256/AST checks and `git diff --check` | **Passed**. All **68 package Python/JSON files** are byte-identical in the final wheel and reviewed checkout. All Python parses. Final source identity matches the executed matrix; checked-in review PNG hashes match its outputs. Legacy presets, AGENTS and both plan files remain unchanged. |

Final wheel SHA-256: `c38070e4799863edbda65cb15cb34e08c0dca8f7c8f381b5599b7dffb62aeb48`.

The final source snapshot covers **75 package files**, SHA-256 `f971c73c4d58f25541196150fd16232c23fbc911e2d83c6a40c1fc9e874c3180`. The public bundle SHA-256 is `f8cbaead548c0ebf6be491d050fe67d39a5314dfcb4c9c7c5cfe2c2bdfedc35a`. The actual loaded renderer file SHA-256 is `b21b80119d6d6fc26cf5404fb7401360ea030f6c5628a2fc5cd272642aa27591`, loaded from the per-run `source/blender_render/blender_custom_render.py`, not the installed package or another worktree. Twelve loaded package modules are individually recorded and verified. Full paths, binary identity, source-file inventory, preset/spec hashes, processes and dependency locations are in each local `preview.json` / `source.json` / `manifest.json`.

Runtime: **Blender 5.0.1, Molecular Nodes 4.5.10, Blender Python 3.11.13, NumPy 1.26.4, Biotite 1.6.0**, with the existing development Python 3.11.15 controlling previews. The runtime versions are checked against `runtime.lock.json`. Actual Blender executable SHA-256: `e147a62d4dc3c53ded1361810e16921f88e13938a4f62cfea81fbf1fee2b3b6d`. This records the runtime used rather than assuming that a reported source commit controls a warm worker.

### Causal results and review artifacts

All six inspected focus scenes contain the same **73,454 evaluated vertices**, geometry SHA-256 `dca7bdae5539f06f4e234952ec5335d1ce5d3f7af23e39abc9426d3afe535f21`, and exactly equal camera matrices. The scene inspector reports structure depths of approximately 60.59–91.68 Å. Actual focus distances 82 and 65 Å, apertures 0.5 and 8, and omitted-aperture default 2.8 were verified in saved scenes and visibly compared in PNGs.

- **Fixed JSON, changed Python:** only the temporary source copy's aperture assignment changes from `float(cfg.get("f_stop", 2.8))` to that value multiplied by 16. No repository mutation or checkout/reset is used for this experiment. Its source hash is `05c9664caa2572cec4c4488a339b737b8298b6645904f0e2172795e393b73937`; the source-file comparison confirms exactly one changed Python file. Input and resolved JSON remain fixed, specification SHA-256 `271c51c5c500468790446e5b6bf11f39969cb62cafabbe608fae87a6aa0a34b1`. Actual aperture becomes 8 and the image gains depth of field.
- **Fixed Python, changed JSON:** changing f/0.5 to f/8 gives specification SHA-256 `834554bc07dd9e9cea10afbc1bcf95d9572998f48de016055ae743b45d20e19a`. Its output matches the Python-mutated case within mean absolute RGB difference **0.000004340278** on the 0–255 scale. This is far below the 0.1 tolerance and is not claimed byte-identical. It demonstrates the intended same scene effect through two independent changes.
- Closing the aperture changes mean RGB by **2.391767578125** and increases noise-smoothed edge energy from **4.238912371235065** to **9.036348371291695**. Moving focus from 82 to 65 Å at fixed Python changes mean RGB by **3.434723307292**, with the near structure becoming clearer. Scene values and effect-specific detail changes establish more than sampling-noise differences.
- Saving/restarting/reusing `user/macro-focus` preserves the resolved JSON and preview identity while running a different Blender PID; mean image difference is **0.000004340278**. All nine preview processes are distinct, and none reports a cache hit.
- Pastel and green retain `mn_cartoon`, `mn_native_sticks`, ligand spheres disabled and PNG-only output. The full macro validation example explicitly requests `.blend`; scene creation is still opt-in.
- The palette reference selects the actual pastel base and used material override; its resolved JSON is checked against the rendered palette case. The selective-focus reference is low fit for built-ins and routes to a dedicated new preset using the new camera capability. The images, camera effects and those classifications were visually inspected.

Portable public evidence is in [examples/developer/acceptance.json](../../examples/developer/acceptance.json). [The developer workflow](../developer-workflow.md) includes the two review PNGs, full example JSON, commands and skill link. Raw source/runtime manifests and all nine outputs remain local under `$TASK/milestone-b`; no personal filesystem paths or supplied research sessions are added to the examples.

### Failures, limitations and next task

Pre-existing failures: none in the 179-test baseline; the four dependency deprecation warnings are inherited. New failures: none unresolved. The initial new test fixture failure is recorded above. The first preview failed to import `databpy` because the managed extension dependency path was absent. A subsequent trial produced an internal PNG but failed the required post-render identity check when Blender's factory reset removed files from that managed dependency directory; that trial is **not** an accepted preview. A normal-startup diagnostic expanded the installed extension's managed cache. This task therefore does not claim that cache stayed unchanged throughout these initial diagnostics. Final previews isolate all Blender user resources and extract bundled wheels privately; the shared-cache file inventory stayed unchanged during the final acceptance checks, and the supplied CCD's bytes and modification time stayed unchanged after private CCD copying was added. No shared render service was contacted or changed.

One properly isolated probe then passed. Three earlier nine-preview matrices passed rendering assertions while review tightened reference-material applicability, private CCD staging and installed-CLI source selection. The first matrix's reference-route assertion alone was insufficient because it did not bind the chosen override to the rendered preset; the final matrix corrects that acceptance check. Final checks above were repeated on the exact reviewed source after these corrections. Initial debug snapshots were retained only through explicit `--keep-source`; ordinary acceptance previews clean their temporary source/runtime/staging state.

Not executed / unverified: EEVEE visual output, Linux/HPC/Slurm/Modal execution, shared-service lifecycle or migration, deployment, publication, installed Codex/ChatGPT/PyMOL UI delivery, clean full dependency installation and automatic image understanding. The 82 Å focal distance is fixture/camera-specific; a different structure or camera needs a different focal plane. The reference ranker is deliberately small and relies on inspected agent observations; consumer selection and hosted capability promotion remain future work. Local preset namespaces are supported in the developer CLI; consumers can still receive their exported full JSON until D adds catalog integration.

Stopped at **Milestone B, ready for review**. B changes remain uncommitted for this review; the preceding requested commit is `8b4b6a8`. Proposed next bounded task, only after review: Milestone C's stable operation/bundle identity and lifecycle regression foundation, beginning with transient export metadata and retry semantics. Do not automatically start it or allocate/deploy a backend.

## Milestone B review fixes — source validation and saved-camera policy

This follow-up started on `codex/milestone-a-json-contracts` at `8b4b6a828c369961d8e27ba6e16afb2fe2a0eadf`, preserving the existing uncommitted B implementation and examples. It addresses only the two B blockers. The shared DOF renderer itself is unchanged in this follow-up. No reset, commit, service change, remote compute or publication occurred.

- [x] Capture safe local catalog paths as exact bytes without parent-installation style validation. Fresh snapshot Python verifies the captured digest, envelope, identity, capabilities, style and overrides before Blender starts; built-in lookup remains in the snapshot.
- [x] Save format-2 envelopes with required `camera_policy`; new saves default to `preserve_input`, and the macro example explicitly saves `authored`. Format 1 remains legacy `authored`, reported by inspection without rewriting the file; replacement saves write format 2.
- [x] Pass local defaults and provenance through a library-only `PresetDefinition` into the shared resolver. Authoring no longer turns named presets into authoritative full JSON or duplicates merging.
- [x] Enforce precedence: explicit camera JSON/overrides, then explicit preserve-view flag, then stored policy. Built-ins default to preservation. Reject preservation that would discard DOF; retain full-JSON authority and independent scene opt-in.
- [x] Repeat focused, repository, installed-wheel and live rendering acceptance on the final package source; update public evidence and review the diff. Stop uncommitted for review.

### Executed commands and outcomes

The commands below ran in the release checkout except the installed-wheel tests. For this follow-up, `TASK=/private/tmp/blender-render-b-fixes`, `CHECKOUT=/Users/mb/Documents/blender_bridge-release`, `BUNDLE=/private/tmp/blender-render-milestone-a-live/1sps-payload.json`, `BLENDER=/Applications/Blender.app/Contents/MacOS/Blender`, `SITE=$CHECKOUT/.validation/config/runtime/site`, `MN=/Users/mb/Library/Application Support/Blender/5.0/extensions/blender_org`, and `CCD=/private/tmp/blender-reference-presets/ccd/components.bcif`. The existing public 1SPS CIF-first export was reused; no new PyMOL export is claimed.

All pytest runs set `PYTHONDONTWRITEBYTECODE=1`, `BLENDER_RENDER_HOME=$TASK/test-home`, `PYMOL_BRIDGE_SESSION_ROOT=$TASK/test-sessions`, and `PYMOL_BRIDGE_TMP_ROOT=$TASK/test-runs`; the baseline used `baseline-` in place of `test-`.

| Command/check | Executed result |
|---|---|
| Baseline `.venv/bin/python -m pytest -q -p no:cacheprovider` | **204 passed**, 4 inherited warnings, 4.34s; `baseline.log`. |
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_developer.py tests/test_developer_policy.py tests/test_spec.py` | **87 passed**, 2 inherited warnings, 1.15s; `focused.log`. |
| Final `.venv/bin/python -m pytest -q -p no:cacheprovider` | **232 passed**, 4 inherited warnings, 4.35s; `pytest-final.log`. Includes 28 new policy/snapshot tests. |
| `.venv/bin/python -m build --wheel --outdir "$TASK/wheels" "$TASK/wheel-source"` | Passed after authorized build-dependency network retry; isolated staging, `build-retry.log`. |
| `"$CHECKOUT/.venv/bin/python" -m venv "$TASK/installed-env"`, then `"$TASK/installed-env/bin/python" -m pip install --no-deps --no-index "$TASK/wheels/pymol_plugin_bridge-0.2.0b1-py3-none-any.whl"` | Passed; `install-retry.log`. Existing development environment was not modified. |
| Installed-wheel contract invocation below, outside checkout | **147 passed**, 2 inherited warnings, 1.87s; `installed-tests-final.log`. Six copied test files; actual imported module path asserted inside installed environment. |
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/validate_developer.py --bundle "$BUNDLE" --output "$TASK/matrix" --blender "$BLENDER" --runtime-path "$SITE" --runtime-path "$MN" --runtime-wheels "$MN/molecularnodes/wheels" --ccd "$CCD"` | **Nine fresh previews and six independent scene reopenings passed**; `matrix.log`. Same case names, sample counts and scene-inspection command as the previous matrix. |
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/validate_developer_policy.py --installed-site "$TASK/installed-env/lib/python3.11/site-packages" --bundle "$BUNDLE" --output "$TASK/policy" --blender "$BLENDER" --runtime-path "$SITE" --runtime-path "$MN" --runtime-wheels "$MN/molecularnodes/wheels" --ccd "$CCD"` | **Three fresh previews and seven expected pre-Blender rejections passed**; `policy.log`. Exact child commands and controller/schema identities retained in `policy/acceptance.json`. |
| Final ZIP/SHA-256/AST checks, artifact hash checks and `git diff --check` | Passed. All 68 package Python/JSON files match the installed wheel; executed package snapshot still matches current source. |

Installed-wheel tests ran from `$TASK/installed-env/lib/python3.11/site-packages` with that directory first on `PYTHONPATH`, using the existing dev Python and dependencies. The Python invocation was:

```python
from pathlib import Path
import blender_render.core as core
import pytest
assert Path(core.__file__).resolve().is_relative_to(Path('/private/tmp/blender-render-b-fixes/installed-env').resolve())
print('Verified installed module:', core.__file__)
raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', '/private/tmp/blender-render-b-fixes/wheel-check']))
```

Copied contract files: `test_developer.py`, `test_developer_policy.py`, `test_spec.py`, `test_style_schema.py`, `test_reference_presets.py`, `test_runtime_contracts.py`.

### Evidence and causal checks

Final 75-file package source SHA-256: `a23861ec92c26e68000f6dcb8bc6b8677253336e3d70503d28fa569b4dcb7b58`. Wheel SHA-256: `1c9aed74df911fa9631e3a38d370bfe93bc167000ba13abbbd899e9960063ec7`. Runtime manifests again verify Blender **5.0.1**, Molecular Nodes **4.5.10**, Python **3.11**, NumPy **1.26.4**, Biotite **1.6.0**, binary SHA-256 `e147a62d4dc3c53ded1361810e16921f88e13938a4f62cfea81fbf1fee2b3b6d`, and CCD SHA-256 `dffdb3a19600d51854058daecfbc9bd1b94aab413a04e7afed8d4b7670621d47`. Loaded package-module hashes and snapshot paths were verified by every successful preview.

The six reopened scenes retain identical camera matrices and **73,454 vertices**, geometry SHA-256 `dca7bdae5539f06f4e234952ec5335d1ce5d3f7af23e39abc9426d3afe535f21`. Fixed-JSON Python mutation uses temporary source SHA-256 `2ea41a411da6345d38a40686a11d8776ea32d41bbfa69afc90122c9c716c8c9e`, preserving resolved-spec SHA-256 `271c51c5c500468790446e5b6bf11f39969cb62cafabbe608fae87a6aa0a34b1`. The independently changed JSON aperture produces the same scene effect, mean RGB difference **0.000007595486**. Aperture/focus changes give image differences **2.391768663194** / **3.434724392361**; edge energy increases **4.238912371235 → 9.036322282065** when closing the aperture. Visual inspection confirms intended focus changes. Authored save/restart reproduces the specification and actual camera/DOF, mean image difference **0.000004340278**. These are numerical matches within tolerance, not byte-identical PNG claims.

The older-controller test uses an isolated copy of the built installed wheel with only DOF removed from its schema, not a historical published release. Its schema SHA-256 is `9ee699aba4ee6e28ed20f96338d3ce50376ffbf7694f3931050d5e484ecb28cd`; current schema is `8098a4997d5421ec1cfafb07e0228b4a4bb9cd2f8017775757f8ae770d1f5193`. It successfully previews the named macro through the selected current checkout. Conversely, a selected checkout with the reduced schema rejects it before Blender starts. Other expected rejections cover incompatible preservation, malformed envelope JSON, wrong identity, wrong style digest, missing policy and unsupported policy. Unit tests separately cover captured-byte digest tampering, catalog edits after capture, invalid identifiers and symlink escape.

Live built-in versus saved-local pastel comparison gives identical camera, **480 × 360** viewport-derived output and resolved-spec SHA-256 `7fea0e35872bb53083eb19e629e72853dd64b294883fe1aa62152689b3f7af78`; mean RGBA difference **0.000001446759**. Unit parity covers both policies, both namespaces, explicit flags, camera overrides and format-1 compatibility. Positive catalog byte hashes are recorded at capture time; later negative-test catalog edits do not change earlier evidence. All 12 successful previews use fresh processes. Six scenes are explicitly requested; other outputs remain PNG-only.

Public portable evidence is refreshed in [acceptance.json](../../examples/developer/acceptance.json), [policy-acceptance.json](../../examples/developer/policy-acceptance.json), `focus.png` and `deep_focus.png`. Full local commands/manifests remain under `$TASK`. The selected shared managed-cache inventory (four files) and CCD bytes/mtime stayed unchanged, recorded in `isolation-final.json`; this is not an exhaustive filesystem audit.

### Failures, limits and review boundary

Pre-existing suite failures: none; dependency warnings inherited. Setup failures: the first isolated wheel build could not fetch build dependencies under restricted network; the authorized retry passed. An initial installed-environment command used the wrong working directory, so venv creation and installation failed and an unguarded test attempt ran in invalid context (**25 failed, 107 passed, 1 skipped**). That attempt (`installed-tests.log`) is excluded from acceptance; corrected absolute paths plus an installed-module assertion produced the 147-pass result above. No new code failures remain unresolved.

Not executed: EEVEE visual acceptance, Linux/HPC/Slurm/Modal, service lifecycle, deployment/publication, PyMOL/Codex/ChatGPT UI delivery, clean full dependency installation or a real historical-controller release. Format-1 behavior is preserved through compatibility interpretation, not automatic file migration. The separate A review findings (ignored yaw, legacy adapter allowlist, boolean adapter inconsistencies and unused `anchor_model_center`) and optional reference-ranking improvements are not resolved by this work.

Stopped at the **B review boundary with changes uncommitted**. Next bounded task: review these two fixes and their evidence, then address the separate A findings in an explicitly scoped follow-up before wider promotion. Do not automatically begin C.

## Milestone A review fixes — camera consumption and adapter parity

The user authorized fixing A after the read-only review. Starting branch/HEAD remained `codex/milestone-a-json-contracts` / `8b4b6a828c369961d8e27ba6e16afb2fe2a0eadf`, with the existing uncommitted B implementation and its review fixes preserved. The requested v2 plan was absent; the available canonical v3 plan and AGENTS remain the reference. This follow-up repairs the four concrete A findings, without implementing C or changing any service.

- [x] Absolute yaw/pitch overrides now select `mode=override` and `direction_source=preset` unless explicitly specified. Contradictory explicit settings fail shared semantic validation before rendering. Full JSON must specify a compatible camera. The shipped pastel JSON loses its two formerly ignored absolute angles, preserving its actual PyMOL-derived camera. No renderer-specific preset-name gate was introduced.
- [x] Legacy `/render` removes transport metadata only and delegates render-option validation to the shared contract. Overrides and keep-temp survive; unknown fields fail instead of disappearing. A real in-process HTTP/queue test verifies the resolved specification using fake compute; it is not live service acceptance.
- [x] Explicit `color_follow=False` disables the JSON setting while preserving other color parameters. The resolved flag forwarded through gateways reflects the resolved JSON, so a second resolution cannot undo it. PyMOL and the legacy Python client distinguish omitted booleans from explicit false, preserving authored settings on omission. Explicit legacy `blend_output` requests a scene.
- [x] Removed unsupported `camera.anchor_model_center` from the schema. Both boolean values now fail clearly.

### Compatibility boundary

Preset identifiers, representations and supported styles remain intact. Validation is intentionally tighter: older full JSON with `direction_source=pymol` plus absolute yaw/pitch must remove those unused angles to preserve its former appearance, or select `direction_source=preset` to apply them. Full JSON using `anchor_model_center` must remove it. Saved envelopes containing these invalid controls require corrected style JSON and a regenerated digest; envelope-format compatibility alone does not exempt invalid style controls. This is a documented restriction of formerly accepted no-op inputs, not a claim of byte-for-byte compatibility for them. No legacy files are rewritten automatically. See the developer workflow for migration details.

### Exact executed evidence

For this follow-up, `TASK=/private/tmp/blender-a-contract-fixes` and `CHECKOUT` is the release checkout. The public bundle and installed Blender/Molecular Nodes/CCD paths are the same as the preceding B record. No new PyMOL export, dependency upgrade or shared-runtime installation occurred.

| Command/check | Result |
|---|---|
| Pre-edit review: `PYTHONDONTWRITEBYTECODE=1 BLENDER_RENDER_HOME=/private/tmp/blender-b-review-home PYMOL_BRIDGE_SESSION_ROOT=/private/tmp/blender-b-review-sessions PYMOL_BRIDGE_TMP_ROOT=/private/tmp/blender-b-review-runs .venv/bin/python -m pytest -q -p no:cacheprovider` | **232 passed**, four inherited warnings, 4.40s. |
| Final `PYTHONDONTWRITEBYTECODE=1 BLENDER_RENDER_HOME=/private/tmp/blender-a-fix-home PYMOL_BRIDGE_SESSION_ROOT=/private/tmp/blender-a-fix-sessions PYMOL_BRIDGE_TMP_ROOT=/private/tmp/blender-a-fix-runs .venv/bin/python -m pytest -q -p no:cacheprovider` | **241 passed**, four inherited warnings, 5.10s; `$TASK/tests.log`. Includes all B tests and nine additional parameterized cases. |
| `.venv/bin/python -m build --wheel --outdir "$TASK/wheels" "$TASK/wheel-source"` | Passed after authorized build-dependency network retry; isolated source staging, `build-retry.log`. |
| `"$CHECKOUT/.venv/bin/python" -m venv "$TASK/installed-env"`, then `"$TASK/installed-env/bin/python" -m pip install --no-deps --no-index "$TASK/wheels/pymol_plugin_bridge-0.2.0b1-py3-none-any.whl"` | Passed; `install.log`. |
| Installed-wheel invocation below | **194 passed**, four inherited warnings, 5.09s; `installed-tests.log`. Nine test files copied outside the checkout; imported module path asserted inside the installed environment. Uses existing dev dependencies, not a clean dependency installation. |
| `PYTHONDONTWRITEBYTECODE=1 "$CHECKOUT/.venv/bin/python" "$TASK/verify.py"` | **Six fresh local Blender previews and three independent scene reopenings passed**; `live.log`, `acceptance.json`. Public 1SPS only; 480 × 360, 32 samples, seed 7. |
| Final package/wheel SHA checks, Python AST checks and `git diff --check` | Passed; all 68 wheel Python/JSON files match final source. |

Installed tests ran from `$TASK/installed-env/lib/python3.11/site-packages`, with that directory on `PYTHONPATH`, `PYTHONDONTWRITEBYTECODE=1` and `BLENDER_RENDER_HOME=$TASK/installed-home`, using the dev interpreter. The Python body was:

```python
import pytest
from pathlib import Path
import blender_render.core as core
assert Path(core.__file__).resolve().is_relative_to(Path('/private/tmp/blender-a-contract-fixes/installed-env'))
print('Installed module:', core.__file__)
raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', '/private/tmp/blender-a-contract-fixes/wheel-check']))
```

The copied files were `test_spec.py`, `test_developer.py`, `test_developer_policy.py`, `test_style_schema.py`, `test_reference_presets.py`, `test_runtime_contracts.py`, `test_jobs_api.py`, `test_pymol_adapter.py` and `test_remote_client.py`.

The live driver invokes `developer.preview` on the current checkout for `angle30`, equivalent `full30`, `angle60`, default pastel, default green and the existing DOF example. Only the three angle cases request scenes. It reopens those scenes using `Blender --background --factory-startup --disable-autoexec CASE/render.blend --python-exit-code 1 --python scripts/inspect_developer_scene.py -- CASE/scene.json`, with isolated user resources. The exact driver and logs remain under `$TASK`; compact public evidence is in `examples/developer/a-contract-acceptance.json`.

Full JSON and equivalent preset overrides produced identical camera matrices, resolved JSON and PNG pixels (mean difference **0.0**). Changing yaw from 30° to 60° at pitch 12° changed the camera, while all three scenes retained **73,454 vertices**, geometry SHA-256 `dca7bdae5539f06f4e234952ec5335d1ce5d3f7af23e39abc9426d3afe535f21`. Independent numeric inspection of camera forward vectors against the requested trigonometric direction gave maximum errors **1.662193e-7** and **2.143249e-7**. The two views were visually inspected; these are low-sample verification previews, not publication images.

Pastel and green remained PNG-only with `mn_cartoon` / `mn_native_sticks`; their camera matrices exactly match the earlier B baseline manifests. The DOF smoke preview reports enabled focus at **81.9999993 Å**, f/0.5. Every preview verifies the loaded source and pinned runtime; all six Blender PIDs differ. Final source SHA-256: `501cf6f9d1a873fa4a41e6bf47ed6741d1b4b2dc9f84c553bb05054b17061a16`. Wheel SHA-256: `d97a08f64586c453b96b168ec4da978f14b30d19424c6d425ea586660081ade9`.

### Failures and remaining acceptance

Pre-existing suite failures: none. The first implementation run had **four failed / 228 passed**: earlier full-JSON camera fixtures combined PyMOL direction with ignored absolute angles. Those tests now declare the consuming camera direction, and explicit rejection tests cover contradictory documents. An intermediate run passed 240 tests; adding legacy-client omission coverage produced the final 241. The first wheel build could not fetch dependencies under restricted network; the authorized isolated retry passed. No new failures remain unresolved.

The four reviewed A defects are fixed with local contract and camera-render evidence. This does not certify every possible schema field or backend. B's complete nine-preview causal matrix and older-controller policy matrix remain historical evidence at their recorded source hash; they were not fully rerun after this A follow-up. All B unit tests and the DOF smoke preview pass on the new source. Remote/HPC/Slurm/Modal, shared service lifecycle, installed PyMOL/Codex/ChatGPT UI delivery, EEVEE visuals and clean full dependency installation remain unverified. No service, allocation, credentials, budgets, privacy or publication settings were changed.

Changes remain **uncommitted for review**. Next bounded task: review the A fixes and the explicit compatibility tightening; repeat the full B matrix on the combined final source before claiming refreshed combined A/B acceptance. Do not automatically start another milestone.

## Final Milestone A/B checkpoint

This checkpoint supersedes the preceding recommendation to repeat the entire B render matrix before assessing closure. The user explicitly requested reuse of applicable executed evidence and no unnecessary expensive previews. Read AGENTS, the v3 plan, this progress history, the tracked diff and the untracked B modules/tests/validation scripts. Branch and HEAD remain `codex/milestone-a-json-contracts` / `8b4b6a828c369961d8e27ba6e16afb2fe2a0eadf`. **Only this progress document was changed during the checkpoint; no Python, JSON, tests, presets or rendering behavior changed.** No tests or Blender processes were rerun merely for this documentation edit.

**Closure assessment: the listed local A and B acceptance criteria are proven by executed evidence, with the evidence boundaries below. No listed criterion is missing or only implemented-but-not-executed.** This is closure of the bounded implementation milestones, not certification of every possible style/input combination or of a remote/installed-host release. The validation tightening for formerly ignored absolute angles and `anchor_model_center` remains explicit; it does not claim unchanged acceptance of those old no-op configurations.

### Evidence identity and reuse

Current 75-file package SHA-256 remains `501cf6f9d1a873fa4a41e6bf47ed6741d1b4b2dc9f84c553bb05054b17061a16` (**A-final**). The existing final wheel matches this source for all 68 packaged Python/JSON files; its SHA-256 is `d97a08f64586c453b96b168ec4da978f14b30d19424c6d425ea586660081ade9`.

The earlier full B matrix and policy comparison ran on source `a23861ec92c26e68000f6dcb8bc6b8677253336e3d70503d28fa569b4dcb7b58` (**B-causal**). It is not relabeled as a new render of A-final. Read-only verification at this checkpoint established:

- The shared renderer (`blender_custom_render.py`), rendering transaction (`rendering.py`), preview launcher (`developer.py`), Blender provenance entry point (`developer_blender.py`) and authoring module (`authoring.py`) are byte-identical between B-causal and A-final.
- The differences are limited to the already reviewed `api.py`, `core.py`, `pymol_bridge_plugin.py`, `remote_client.py`, `spec.py`, `style.schema.json` and removal of two inactive pastel-camera fields. These changes have the recorded A-final contract coverage and targeted camera previews.
- Re-resolving all **nine B-causal resolved documents** using current `resolve_options({"style_doc": document}, {})` returned identical documents. This is a current read-only resolution check, not another Blender run.
- Applying current `reference_brief` to the two recorded PNGs and their recorded observations returned exactly the stored preset decisions. The palette, shallow-focus and deep-focus PNGs were visually inspected again. Both reference PNGs are public-fixture outputs, not user research data or inferred hidden shaders.
- Recomputed PNG hashes and checked source/Blender manifest identities for **six A-final, nine B-causal and three B-policy previews**. The six A-final source checks demonstrate actual loading of the current checkout. The prior B controls retain their independently recorded source, runtime and scene evidence.

This combination justifies reusing the B causal evidence for the unchanged behavior; it does not assert that the complete B matrix ran again on A-final. A full matrix repeat remains a useful later promotion check, rather than a missing local B criterion.

### Executed evidence register

Detailed exact environment values and commands remain in the preceding A and B execution sections. These aliases identify the same retained runs, not new executions:

| Evidence | Exact command / inspection | Executed result |
|---|---|---|
| A-final contracts | `PYTHONDONTWRITEBYTECODE=1 BLENDER_RENDER_HOME=/private/tmp/blender-a-fix-home PYMOL_BRIDGE_SESSION_ROOT=/private/tmp/blender-a-fix-sessions PYMOL_BRIDGE_TMP_ROOT=/private/tmp/blender-a-fix-runs .venv/bin/python -m pytest -q -p no:cacheprovider` | **241 passed**, four inherited warnings, 5.10s; `/private/tmp/blender-a-contract-fixes/tests.log`. Includes current B tests. |
| A-final wheel | Installed-module assertion followed by `pytest.main(['-q', '-p', 'no:cacheprovider', '/private/tmp/blender-a-contract-fixes/wheel-check'])`, run outside checkout with installed package first on `PYTHONPATH`; exact interpreter/env body recorded above | **194 passed**, four inherited warnings, 5.09s. Nine copied test files; actual built wheel, existing dev dependencies. |
| A-final live | `PYTHONDONTWRITEBYTECODE=1 "$CHECKOUT/.venv/bin/python" /private/tmp/blender-a-contract-fixes/verify.py` | **Six previews / three scene reopenings passed**: named yaw 30°, full JSON yaw 30°, named yaw 60°, pastel, green and DOF. Camera forward-vector errors below 2.2e-7, geometry unchanged; full/named PNG mean difference 0.0. |
| B-causal live | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/validate_developer.py --bundle "$BUNDLE" --output /private/tmp/blender-render-b-fixes/matrix --blender "$BLENDER" --runtime-path "$SITE" --runtime-path "$MN" --runtime-wheels "$MN/molecularnodes/wheels" --ccd "$CCD"` | **Nine previews / six scene reopenings passed**, plus reference decisions. Independent Python mutation, JSON aperture/focus/default controls, named restart and legacy presets. |
| B-policy live | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/validate_developer_policy.py --installed-site /private/tmp/blender-render-b-fixes/installed-env/lib/python3.11/site-packages --bundle "$BUNDLE" --output /private/tmp/blender-render-b-fixes/policy --blender "$BLENDER" --runtime-path "$SITE" --runtime-path "$MN" --runtime-wheels "$MN/molecularnodes/wheels" --ccd "$CCD"` | **Three previews and seven expected pre-Blender rejection cases passed**. Simulated older-controller schema, selected-checkout validation and 480 × 360 built-in/local camera/spec parity. |
| Earlier A surface/export | The existing PyMOL `export.py` / `scripts/validate_export.py` commands and `/private/tmp/blender-render-milestone-a-live/verify_renders.py`, followed by its recorded `check_scene.py` reopening | CIF/DSS/pose/transform export passed; full-JSON and equivalent override surface renders and explicitly requested scenes passed. This is earlier execution evidence, supplemented by A-final adapter-to-worker surface regressions, not a fresh export or surface render at this checkpoint. |
| Checkpoint read-only verification | Python SHA checks against retained `preview.json`/`source.json` records; current resolver and reference-decision comparisons described above; `git diff --check` | Passed. **No new pytest suite or expensive Blender run** was needed for the documentation-only checkpoint. |

`CHECKOUT`, `BUNDLE`, `BLENDER`, `SITE`, `MN` and `CCD` denote exactly the local paths defined in the preceding B-fix command section. Portable evidence: [A contract/camera results](../../examples/developer/a-contract-acceptance.json), [B causal results](../../examples/developer/acceptance.json), [B policy results](../../examples/developer/policy-acceptance.json). Prior setup failures, corrected test-fixture failures and invalid installed-test attempts remain recorded in their original sections; none is counted as acceptance.

### Milestone A acceptance table

Classification vocabulary: **Proven** = proven by an executed test, preview or inspected scene; **Implemented, not executed** = code exists but the necessary acceptance has not run; **Missing** = required behavior or evidence is absent. A unit/adapter proof is not represented as visual or remote acceptance.

| Criterion | Classification | Concrete proof and scope |
|---|---|---|
| StructureBundle plus resolved RenderSpec JSON is the common renderer input | **Proven** | `test_worker_consumes_exact_snapshot_and_scene_opt_in`, gateway parity, CLI/PyMOL custom-style tests and STDIO/HTTP MCP contract tests in the executed A-final suite. `rendering._resolved_request` verifies the style hash; `_process_render_request` materializes CIF/view plus that style for the common renderer. A-final live previews use the same transaction. Remote execution itself is a later milestone. |
| Full JSON and equivalent preset-plus-overrides resolve identically | **Proven** | `test_full_json_and_preset_overrides_are_equivalent` covers pastel and green; A-final `angle30` and `full30` have identical resolved JSON, inspected camera matrices and PNG pixels. Arrays replace; objects merge in `spec._merge`. |
| Custom styles do not inherit pastel-only gates | **Proven** | `test_reference_gates_follow_representation_not_names` and both `test_custom_surface_through_adapter_to_worker` variants passed on A-final. Earlier two real surface renders and reopened scene confirm no inappropriate cartoon/sticks requirement. Gate behavior was not changed by B or the subsequent A fixes. |
| Explicit camera settings survive resolution and affect orientation | **Proven** | A-final yaw 30°/60° previews and three reopened scenes, not only JSON assertions. Expected forward-vector errors 1.66e-7 / 2.14e-7; all scenes have 73,454 vertices and the same geometry hash. Explicit camera wins over preservation flags. |
| Contradictory camera settings fail clearly | **Proven** | `test_absolute_angle_override_selects_consuming_camera` rejects explicit absolute-angle/PyMOL-direction conflicts. B tests reject DOF with PyMOL/orthographic/raw-view settings and preservation that discards DOF; the live `incompatible_policy` case failed before Blender started. This covers the defined angle/direction and DOF-policy contract, not every possible camera combination. |
| Unknown and unsupported fields fail before rendering | **Proven** | `test_invalid_options_fail_before_dispatch` uses a never-called worker and checks no staging; dead `anchor_model_center` values rejected. Invalid DOF controls/envelopes/digests and older checkout capabilities have executed rejection coverage. This is the declared schema/control boundary, not a claim that every historical field has exhaustive visual coverage. |
| Pastel and dried-paint green still work | **Proven** | Both A-final real PNGs use `mn_cartoon` and `mn_native_sticks`; actual camera matrices match the earlier baseline. All catalog/schema tests pass and identifiers/aliases are preserved. |
| PNG-only and explicit scene behavior remain distinct | **Proven** | A-final pastel/green/DOF produce PNG only; the three angle cases explicitly requested scenes and those scenes reopened successfully. Full JSON, overrides and explicit false/true precedence have executed contract tests. |
| Legacy adapters preserve overrides and reject unknown options | **Proven** | `test_legacy_render_uses_shared_options_and_rejects_unknown` exercises authenticated in-process HTTP, queue admission and captured fake-compute input, including overrides and keep-temp; unknown top-level controls return 400. CLI/PyMOL/custom-style and legacy Python-client tests cover their respective adaptation. No live service is implied. |
| Explicit `color_follow=False` is preserved | **Proven** | Both `test_color_follow_explicit_false_and_gateway_parity` cases pass, including repeated resolution and retained mix. PyMOL and legacy Python-client tests distinguish omission from false. This is executed option/JSON behavior; no additional color-follow-specific image comparison is claimed. |

### Milestone B acceptance table

| Criterion | Classification | Concrete proof and scope |
|---|---|---|
| JSON-only variation actually previewed | **Proven** | B-causal `json_palette` is a real public-fixture PNG using an existing pastel material override. Its image and decision were inspected again; current resolution/decision are unchanged. |
| Genuine Python-level rendering behavior implemented and previewed | **Proven** | `_configure_depth_of_field` enables Blender optical DOF and consumes distance/f-stop, independently of geometry/framing. B-causal inspected scenes and A-final DOF smoke render verify this effect; it is not a recolored preset or schema-only implementation. |
| Fixed JSON plus changed Python changes intended scene/output | **Proven** | B-causal `focus` versus `python_change`: identical bundle and resolved-spec hash, exactly one temporary Python file changed (`aperture_fstop` multiplied by 16), changed source digest, actual f-stop 0.5 → 8, unchanged geometry/camera. Python-mutated output matches the independently configured JSON case within mean RGB difference 0.000007595486. Shared effect/preview code is unchanged at A-final. |
| Fixed Python plus changed JSON control changes intended behavior | **Proven** | B-causal `json_aperture`, `json_focus` and `json_default`: same source hash, actual scene aperture/focal-distance/default values inspected. Image differences 2.391768663194 / 3.434724392361 and edge-energy increase 4.2389 → 9.0363 establish the intended effect beyond sampling noise. Images re-inspected at this checkpoint. |
| Preview proves selected checkout/source digest was loaded | **Proven** | Six A-final previews report the current source digest and per-module snapshot paths/hashes, pinned runtime and distinct Blender PIDs. Byte/hash checks, snapshot cleanup, no-cache behavior, concurrent-edit rejection and catalog capture tests have executed evidence. Earlier B manifests retain their own honest source identities. |
| Saved user/project preset survives restart and is reusable | **Proven** | B-causal saves `user/macro-focus` with `authored` in one CLI process and previews it through another; specification/camera/DOF preserved. A-final `test_save_restart_reuse_and_revision`, namespace/collision tests and policy-parity tests pass. User preset restart is live-render proven; project namespace uses the same implementation with executed persistence/resolution contracts, not a separately claimed project Blender run. |
| One reference image handled as existing-preset override | **Proven** | Recorded `json_palette` PNG plus inspected observations selects `builtin/preset_1_interface`, high fit, used `inner_chain_a` color override. Validation binds the chosen override to the actual rendered material. Current decision exactly matches the recorded decision. |
| One low-fit reference recommends a new preset rather than extensive one-off overrides | **Proven** | Recorded shallow-focus PNG plus inspected observations yields low fit, `new_preset_recommended=true`, route `developer/new-preset`, unmatched `camera.depth_of_field` relative to the base and an empty override set. The reusable macro preset demonstrates that route. Current decision unchanged. This does not claim automatic vision or general best-fit ranking quality. |

### Diff assessment and intentional deferrals

No further source fix is needed to close these exercised A/B criteria. There is one merge/option-resolution implementation in `spec.py`; authoring supplies validated defaults/provenance rather than replacing named presets with authoritative full JSON. The developer wrapper owns isolation/provenance and calls the existing rendering transaction; it does not implement another style engine. The compatibility API and clients remain tested entry points, not dead duplicate renderers. Schema validation remains centralized with additional semantic checks. The new DOF fields each have real scene-value evidence. The removed `anchor_model_center` field fails clearly rather than surviving as a no-op. No new preset-name rendering gate was added.

The original angle tests were insufficient alone because they asserted retained JSON values; A-final inspected orientation and expected forward-vector checks now close that gap. Likewise, the reference unit test uses a synthetic PNG only for contract coverage: acceptance relies on the separately rendered and visually inspected molecular PNGs. The simulated old-controller test is accurately labeled as schema simulation, not a historical installed release.

Deferred, without blocking local B closure:

- **C:** stable molecular/operation identity, lifecycle/locking/cancellation/artifact recovery, generic Slurm launch, side-effect-free reads, compatible-runtime negotiation and stage timings. Existing implementations are not assumed accepted merely because local renderer contracts pass.
- **D:** comprehensive reference ranking/capability registry (the small ranker currently derives capabilities from the catalog plus DOF), medium-fit behavior, expanded consumer catalog integration, and smooth nonblocking PyMOL/MCP presentation. These are optional improvements beyond the two exercised B reference cases.
- **E:** actual Modal/Slurm promotion, cross-backend comparisons, remote MCP and installed ChatGPT/Codex/PyMOL visible delivery, clean dependency installation and publisher review. None is required to close B, and none ran in this checkpoint.
- **Later validation expansion:** exhaustive conditional legacy-camera/material field coverage, EEVEE visuals, additional fixtures and a complete combined-source B matrix rerun when preparing promotion or after changes to the behavior under test. These are not silently marked as passes.

### Proposed Milestone C decomposition — planning only

Keep the existing modules and contracts. Start only after a separate instruction; the steps below create no jobs, allocations or services.

| Bounded step | Existing scope | Acceptance before proceeding |
|---|---|---|
| C1. Stable identity and retry semantics | `export.py`, `jobs.py`, `gateway.py`; separate scientific content identity from transient export fields and logical operation identity | Equivalent transformed multi-object exports have stable semantic fingerprints; coordinate/selection/state changes remain significant; transport retries reuse one outer/child operation while explicit repeats create distinct operations. |
| C2. Worker ownership and cancellation | `render_worker_manager.py`, service/queue lifecycle | FIFO ownership survives timeout/cancel/failure; a still-running render cannot release its worker slot early; cancellation is owner-scoped and terminates the intended work; fake-worker regressions precede any authorized runtime test. |
| C3. Durable results and controller recovery | `jobs.py`, `service.py`, artifact storage/recovery | Controller restart cannot advertise missing PNG/scene artifacts; partial delivery retries fetch existing output without rendering again; completed/deleted/expired states and budgets remain consistent. Respect drain/stop requirements for any eventual service upgrade. |
| C4. Profile launch and runtime compatibility | Existing HPC/profile/gateway modules and runtime lock | Configured standard Slurm and site-specific launchers remain profiles of one engine; status/catalog/quota/diagnostics never allocate; unsupported capabilities reject before dispatch. First use command/transport fakes; real allocations remain separately authorized. |
| C5. Responsiveness, selection and timings | Shared service/client submission and existing input adapters | Normal submission returns a durable handle before long preparation; raw-upload selection is honored; one request creates one operation; no unconditional quota/catalog calls; sanitized timings distinguish conversion, queue/startup, scene build, render and delivery. |

Do not mix all five steps with a deployment or file-tree migration. C1 is the proposed first bounded task. **A/B closure assessment complete; all changes remain uncommitted for review, and C has not begun.**

## Milestone C1 — operation lifecycle and idempotency

Starting branch: `codex/execution-reliability`, committed A/B checkpoint
`815e3dffdf34de0451a2903021c620ef07d82827` (`complete JSON render bridge and developer workbench`).
The release worktree was clean. The separate `blender_bridge` master checkout was
not modified. Read the contributor guide, v3 plan, progress history and checkpoint
implementation diff. No nested AGENTS was found. The user's C1 request explicitly
includes early handles, read-only access, gateway mapping, worker admission and
stages, extending the narrower historical C1 decomposition above.

**C1 is ready for review at the requested fake/local acceptance boundary.** The
initial implementation stopped when automatic approval review blocked the CLI
routing/start edits. The user subsequently explicitly authorized those source
changes with fake-only validation; the closure work below completes them. No
blocked edit was bypassed before that authorization. No MCP tool/schema/preset
selection changes were made. No Slurm, Modal, shared service, credentials or paid
compute were used. Changes remain uncommitted. Real runtime release gates are
not asserted by this assessment.

### Implemented state-machine changes

- Reuse the existing SQLite FIFO, job IDs, cost ledger and resolver. Add optional
  logical `operation_key` independently of transport aliases; explicit repeats
  remain `rerender=True`. Preserve the operation after artifact expiry/deletion,
  instead of automatically creating a replacement after the old TTL.
- Add durable raw-file admission through `RenderService.render_file` and
  `POST /v1/render-files/{format}`. Commit the job before deferred conversion;
  validate its StructureBundle and bind resolved JSON before backend entry.
  A cancelled conversion continues holding the FIFO slot until it returns.
- Preserve omission versus explicit false in retry hashes when JSON contains
  the effect controls; this prevents identity lookup bypassing A's resolver.
- Normalize only exporter-owned UUID names; preserve original selection/state
  and actual active object states as well as transforms/coordinates/colors/DSS/view.
- Map distinct gateway outer jobs to distinct children; reuse persisted child IDs
  and stable child keys after response loss. Fail on consumed/missing requested
  artifacts rather than treating those children as completed usable output.
- Move HTTP worker admission release into the execution thread's finalizer. Wait
  for actual Blender process exit before giving up worker ownership. Timeout or
  request cancellation cannot release an alive worker, and late completion releases
  admission once.
- Add compact stages with timestamped transitions and worker stage journaling.
  Status, owner-scoped recent operations, catalog/capability lookup and configured
  client reads do not start compute. Absent cached connections report limitations.
  `doctor` is passive; explicit setup can still probe the runtime.

Detailed API and compatibility notes: [operation lifecycle](../operation-lifecycle.md).
Preset JSON, schema, resolver semantics and A/B developer capabilities remain
unchanged. The shared Blender source change only records stage boundaries; no new
image effect was implemented and no new Blender image equivalence is claimed.

### Executed evidence

All commands used the existing release `.venv/bin/python`, with
`PYTHONDONTWRITEBYTECODE=1`, `BLENDER_RENDER_HOME=/private/tmp/blender-c1/home`,
`PYMOL_BRIDGE_SESSION_ROOT=/private/tmp/blender-c1/sessions` and
`PYMOL_BRIDGE_TMP_ROOT=/private/tmp/blender-c1/runs`. Tests create isolated stores,
fake processes and in-process HTTP clients. No real remote destination is contacted.

| Exact command (after the environment assignments above) | Executed result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider` before edits | **241 passed, four inherited warnings, 5.32s**; `baseline.log`. No pre-existing test failures. |
| Same full-suite command, first implementation check | **239 passed, two failed, five warnings**; `intermediate.log`. Old raw-file test expected synchronous conversion; a test-created HostBackend omitted the new optional progress attribute. Both corrected. The fifth warning was the latter test thread error, not inherited. |
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_operation_lifecycle.py tests/test_jobs_api.py tests/test_worker_http.py` | Initial **43 passed, one failed, four warnings**; `focused-1.log`. The existing cancellation assertion raced preparation versus actual worker entry; it now waits for the worker-entry event. |
| Full-suite command after those corrections | **256 passed, four inherited warnings, 4.93s**; `suite-2.log`. |
| Full-suite command after database migration, existing-read, killed-process and stage-forwarding coverage | **260 passed, four inherited warnings, 5.11s**; `suite-3.log`. |
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_operation_lifecycle.py tests/test_jobs_api.py tests/test_worker_http.py tests/test_hpc_lifecycle.py` | **53 passed, four inherited warnings, 3.26s**; `focused-final.log`. This preceded the final raw-input compatibility/cancellation-stage edits. |
| `.venv/bin/python -m pytest -q -p no:cacheprovider` after final raw-input, key-validation, cancellation-stage and converter-configuration edits | **262 passed, four inherited warnings, 5.28s**; `suite-final.log`. No remaining test failures. |
| `git diff --check` | **Passed**, no whitespace errors. Reviewed the final client, gateway and worker-admission diff; the explicit-start compatibility gap below remains, so this is not a clean C1 closure. |

Logs above are under `/private/tmp/blender-c1`. The tests prove one reservation /
dispatch on concurrent retries, explicit repeats, durable admission before a gated
converter, queued restart, no backend dispatch after conversion cancellation or
invalid JSON, distinct gateway children and same-child recovery, no new operation
on missing output, absence of read-side startup, stable temporary names and
significant scientific fields, late timeout/cancellation release, and live delivery
of fake worker stage events before its response.

### C1 acceptance boundary and unresolved work

| Required outcome | Current classification |
|---|---|
| Separate operation identity from content recovery | **Proven in unit/service/API tests.** Stable operation keys reject changed-input/options reuse; explicit repeats get distinct IDs. Consumer propagation of keys remains an adapter responsibility. |
| Durable handle before conversion/allocation/cold-start/render | **Proven in service/file API and CLI fake integration.** Molecular files enter durable admission; HPC CLI uses a local outer controller and deferred GatewayBackend. The fake server test observes handle return, then conversion, then allocation. JSON bundles enter the same queue without conversion. |
| Read-only operations do not start compute | **Proven for shared-client/API/CLI reads and passive diagnostics using fakes and audit guards.** Unreachable/stopped compute is reported. Explicit local/HPC `service start` alone selects the mocked startup/allocation path and returns the compatible health result. |
| Stable multi-object identity with scientific changes significant | **Proven by exporter/identity regressions with fake PyMOL.** No fresh real PyMOL export was executed; historical fingerprints are not migrated. |
| Correct nested gateway identity and available artifacts | **Proven with an isolated fake child service.** No real gateway/allocation tested. |
| Retain admission while cancelled/timed-out work is alive; late release once | **Proven with gated fake HTTP workers, request cancellation and a fake non-exiting Blender process.** No real process-kill stress test executed. |
| Compact operation stages | **Implemented and fake/local journal forwarding executed.** Actual Blender and remote live timing streams are not claimed. |

The previously blocked **C1** CLI routing and explicit-start compatibility work is
now implemented and executed with fakes. `--no-wait` performs one logical admission
and no status/result retrieval. Waiting uses the admitted ID. A simulated lost
response after commit followed by fresh argument parsing and a changed transport
key recovers the same operation; explicit repeats get distinct IDs. Local `serve`
configures the deferred trusted-user converter. No remaining C1 implementation
gap is identified within this requested scope; real runtime checks remain unverified.

After C1 review, proposed **C2**: profile-specific standard Slurm launch, runtime
compatibility negotiation, raw-selection adapter parity and authorized real worker
cancellation/timing verification. Proposed **C3**: delivery receipts and partial
download resume, preserved results across controller replacement and explicit
artifact acknowledgement/retention policy. C1's missing-artifact guard does not
solve file recovery or partial downloads. No C2/C3 work, installed-host UI validation,
deployment or publication was started.

### Authorized CLI closure — 2026-09-08

Exact files changed in this follow-up (the prior C1 diff is preserved):

- `blender_render/cli.py`: raw-file admission, operation/repeat flags, flushed
  handle reporting, deferred service converter/backend wiring, explicit start and
  attach-only status. Removed the CLI synchronous raw import/export route.
- `blender_render/client.py`: explicit `start_service`; opt-in CLI admission
  routing to the local outer controller for HPC profiles. Generic construction
  remains lazy; existing gateway child connections remain direct.
- `blender_render/runtime.py`: isolated controller roots, launch arguments for
  worker profile/interpreter, deferred trusted local conversion and interpreter
  mismatch rejection. Existing process launchers are reused, not executed here.
- `tests/test_cli_operation_routing.py`: 22 focused cases, including parameterized
  read-only paths, durable service integration, interrupted response, explicit
  start, fake controller launch and ordered deferred gateway dispatch.
- `tests/test_spec.py`: existing adapter fake accepts additive identity keywords;
  its rendering/camera/artifact assertions are unchanged.
- `tests/c1_boundary_guard.py`: opt-in reproducible pytest audit guard. Any actual
  subprocess/spawn/system call, socket connect/bind or DNS resolution fails the
  run, including an attempt caught by application code. Only the two named
  pre-existing A/B child-process tests are skipped under this guard.
- `docs/operation-lifecycle.md` and this progress file: routing, trust boundaries,
  compatibility, executed evidence and acceptance status.

All commands ran in the release checkout. The initial 20-case CLI run used the
environment assignments documented above and
`.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_cli_operation_routing.py`:
**20 passed, two inherited warnings, 0.58s**. After adding the controller wiring
cases, the temporary `/private/tmp/blender-c1/c1_boundary_guard.py` was used with
`PYTHONPATH=/private/tmp/blender-c1` and the focused command below: **115 passed,
four warnings, 3.66s**, `closure-focused.log`.

The first guarded full-suite command (same prefix, no test paths) reported
**282 passed, two failures, four warnings, 5.25s**, `closure-suite.log`.
The guard prevented, before execution, two pre-existing test process attempts:
Git metadata in `test_snapshot_tracks_python_and_assets` and a second interpreter
in `test_save_restart_reuse_and_revision`. These are fake-only constraint failures,
not new production regressions. No attempted child process ran. With those two
tests explicitly skipped, the temporary guard run with `-rs` reported **282 passed,
two skipped, four warnings, 5.17s**, `closure-suite-fake-only.log`. No other check
was skipped. The repository guard now reproduces this boundary without changing
the ordinary A/B tests.

Final reproducible commands (shell `pipefail` enabled when teeing the logs):

```sh
export PYTHONPATH=tests PYTHONDONTWRITEBYTECODE=1
export BLENDER_RENDER_HOME=/private/tmp/blender-c1/home
export PYMOL_BRIDGE_SESSION_ROOT=/private/tmp/blender-c1/sessions
export PYMOL_BRIDGE_TMP_ROOT=/private/tmp/blender-c1/runs
.venv/bin/python -m pytest -q -p no:cacheprovider -p c1_boundary_guard tests/test_cli_operation_routing.py tests/test_operation_lifecycle.py tests/test_jobs_api.py tests/test_worker_http.py tests/test_hpc_lifecycle.py tests/test_spec.py
.venv/bin/python -m pytest -q -rs -p no:cacheprovider -p c1_boundary_guard
git diff --check
```

- Focused: **115 passed, four inherited warnings, 3.45s**;
  `/private/tmp/blender-c1/closure-focused-repo-guard.log`.
- Full eligible repository suite: **282 passed, two explicitly skipped, four
  inherited warnings, 5.32s**; `closure-suite-repo-guard.log` in the same directory.
- Both final runs reported `C1 audit: real process/network boundary attempts = []`.
  Test fixtures additionally reject unmocked `ensure_local`, `hpc.connect`,
  `hpc.remote`, local export and Blender startup. SSH/qlogin/srun/scancel and Modal
  are unreachable through these guarded boundaries. Only in-process fake servers,
  fake workers, temporary files and mocked startup functions were used. The test
  runner and read-only Git inspection are not application service/compute launches.
- Final diff review found no competing CLI raw-file upload/submit fallback, no
  read-side call to startup or SSH, and no changes to renderer/preset/camera/artifact
  semantics in this follow-up. `git diff --check` passed.

Every C1 criterion in the table is proven at the authorized fake/local boundary.
No installed-wheel contract run, real process-kill test, Blender preview, HPC/Modal
execution or remote timing stream was run here. The two A/B child-process checks
remain unverified on this final source under the task's restrictions; their earlier
executed evidence is not replaced by mocks. No C2/C3 implementation, commit,
deployment or publication was performed. Stop here for C1 review.

### Separate C1 review and checkpoint follow-up — 2026-09-08

The existing commits already separate A/B and C1; do not rewrite them merely to
match suggested staging commands:

- A/B: `815e3dffdf34de0451a2903021c620ef07d82827`.
- Initial C1: `6b334042f95f26a1e8bc3f330e281d56c1a9a714`.

The separately requested read-only review (task
`01a0819a-9b41-7290-aa94-13c99d984563`) found four blockers, superseding the earlier
clean-C1 assessment: cold HPC attach lost the token removed from status; content
reuse did not bind every operation key; drain rejected already-admitted deferred
conversion; queued raw requests reloaded preset JSON after admission.

Narrow corrections preserve the A/B resolver and C1 queue: retrieve credentials
with explicit attach-only after ready status, persist operation-key aliases on
reuse, allow only admitted conversion dispatch through drain with owner/budget
checks, and capture the library PresetDefinition before queuing raw files.
Old queued raw jobs lacking a named-preset snapshot fail rather than substitute
current assets. Re-review also required each operation alias to retain its own
accepted request hash and input identity across cleanup, not borrow the original
job's request. This is now covered for equivalent preset names and deleted uploads.
The final separate read-only review found all four blockers resolved and no
remaining blockers within the fake/local C1 boundary. Unknown captured catalog
identifiers additionally return a validation error before admission.

Changed files for these corrections: `blender_render/hpc.py`, `jobs.py`, `spec.py`,
`service.py`; `tests/test_cli_operation_routing.py`, `test_operation_lifecycle.py`;
`docs/operation-lifecycle.md` and this record. No real compute or shared service
was touched. With the same fake-only environment and audit guard documented above:

```sh
.venv/bin/python -m pytest -q -p no:cacheprovider -p c1_boundary_guard tests/test_cli_operation_routing.py tests/test_operation_lifecycle.py tests/test_jobs_api.py tests/test_hpc_lifecycle.py tests/test_spec.py
.venv/bin/python -m pytest -q -rs -p no:cacheprovider -p c1_boundary_guard
```

Executed: **119 passed, four inherited warnings, 3.61s** (`review-fixes-focused.log`)
and **288 passed, two previously documented A/B process-test skips, four warnings,
5.65s** (`review-fixes-suite.log`). Logs are under `/private/tmp/blender-c1`; both
reported zero real process/network boundary attempts. No tests failed in this
correction run. Real HPC cold start remains unexecuted; its complete handshake is
now tested using fakes instead of replacing `hpc.connect` with a ready response.

After the alias-identity correction, the same commands passed **120 focused / 289
suite** tests (3.59s / 5.50s), with two suite skips and four inherited warnings;
`review-fixes-focused-final.log` and `review-fixes-suite-final.log`. Final checkpoint
validation, including invalid catalog capture, passed **121 focused tests (3.51s)**
and **290 suite tests, two documented skips (5.42s)**, both with four inherited
warnings and zero audited boundary attempts; `review-checkpoint-focused.log` and
`review-checkpoint-suite.log`. `git diff --check` passed. All seven C1 criteria
remain satisfied at this fake/local boundary; real-runtime checks remain deferred.

GitHub inspection confirmed `magnusbauer/blender_render` is private and PR #1 is
draft on `codex/public-release`. The local checkpoint branch is
`codex/execution-reliability`; pushing it does not change PR #1's head or draft
state. This worktree initially had no origin remote. Push/checkpoint outcome will
be recorded after review; no successful push is claimed yet.

The user's current milestone order supersedes earlier proposed decompositions:
**C2 is artifact lifecycle/result recovery only; C3 is backend parity; D is thin
interfaces and preset selection; E is installed-product validation.** Do not mix
Slurm portability, MCP/ChatGPT UX, or preset selection into C2. Start
`codex/c2-result-recovery` only after a clean C1 checkpoint.

### Reviewed C1 checkpoint and push outcome

The separate review cleared all four original blockers after the alias follow-up.
The corrective checkpoint is **`4878801b7b8e3379cfeb9642f7db18c2812f0749`**,
`fix: close C1 review gaps in admission and retry identity`, on
`codex/execution-reliability`. It includes the plan/progress updates; A/B remains
`815e3df` and the initial C1 commit remains `6b33404`. Nothing was reset, squashed,
amended or force-pushed. Status and whitespace checks passed before committing.

Configured the previously absent `origin` to the verified private repository URL
and executed `GIT_TERMINAL_PROMPT=0 git push -u origin HEAD`. **Push failed (128):**
`could not read Username for 'https://github.com': terminal prompts disabled`.
No successful remote checkpoint is claimed. GitHub connector inspection confirmed
PR #1 remains draft on `codex/public-release`; its head was not changed. Local Git
authentication must be configured before retrying the authorized push. No tokens
or credentials were read, altered or placed in repository files.

Then executed `git switch -c codex/c2-result-recovery` successfully from `4878801`.
C2 work below remains uncommitted for review. No C3, D or E implementation began.

## Milestone C2 — artifact lifecycle and result recovery

Scope: completed-result recovery only. Reused the existing job/store/API and client
download boundary; no new renderer, queue, preset semantics, backend launcher or
plugin/preset-selection work. Detailed policy: [result recovery](../result-recovery.md).

Changed files: `blender_render/delivery.py` (verified local receipts and atomic,
resumable downloads), `client.py` (shared recovery boundary and attach-only scoped
client), `api.py` (opt-in non-consuming delivery and explicit checksum acknowledgement),
`jobs.py` (idempotent owner-scoped delivery receipts and explicit drained result
handoff), `cli.py` (partial result stays visible and exits nonzero),
`tests/test_result_recovery.py`, `docs/result-recovery.md`,
`docs/operation-lifecycle.md`, and this progress record.

| Acceptance | Executed evidence |
|---|---|
| Recover a completed job without molecular input or new render | Fresh-client result recovery after original input cleanup; backend call list remains exactly the original job ID. |
| Preserve successful PNG when scene download fails; resume missing artifacts | In-process API + fake scene test returns PNG and per-artifact error, then finishes the same job from a fresh client and recreates outputs at a new destination. CLI prints partial output and exits nonzero. |
| Writable destinations, atomic save, size/checksum validation | Invalid parent fails before network; corrupt response leaves existing user output unchanged and sends no acknowledgement. Verified retry succeeds. |
| Receipts are scoped and do not confuse stale local paths with success | Owner/credential and service URL isolation, deleted-file failure, simultaneous result calls serialized with receipt locks; no new backend dispatch. |
| Response loss is not permission to rerender | Lost acknowledgement response repeats idempotently. Verified local files remain usable after server deletion. |
| Preview does not consume the original; retention is explicit | Preview and `delivery=ack` preserve the original; legacy GET still deletes. Wrong acknowledgement hash fails. Original expiry remains effective; no TTL extension. |
| Protect unexpired outputs during planned replacement | Drained isolated store copies verified artifacts to replacement data root; restart with same DB retains owner/job/expiry and PNG-only artifact set. Repeated handoff verifies matching destination. |
| Missing artifacts are not advertised as completed usable output | Unplanned replacement into empty data root reports expired/unavailable, with no render or conversion. |

The additive explicit-ack path preserves the hosted TTL and legacy signed-download
behavior. New resumable clients require the advertised delivery policy instead of
silently using consuming legacy GETs. Receipt scope is deliberately credential-bound;
credential rotation does not reuse old credential receipts. No remote deployment
or privacy/storage policy change was executed. Planned handoff is an explicit
library-only operator step, not an automatic status/recovery side effect.

Exact executed commands, in the release checkout, using the same guarded
environment as C1 (`PYTHONPATH=tests`, `PYTHONDONTWRITEBYTECODE=1`, and the three
isolated `/private/tmp/blender-c1` home/session/run assignments above):

```sh
.venv/bin/python -m pytest -q -p no:cacheprovider -p c1_boundary_guard tests/test_result_recovery.py tests/test_jobs_api.py tests/test_cli_operation_routing.py tests/test_operation_lifecycle.py
.venv/bin/python -m pytest -q -rs -p no:cacheprovider -p c1_boundary_guard
git diff --check
git diff --stat
```

- Initial focused run: **87 passed, four inherited warnings, 3.86s**, `c2-focused.log`.
- Final focused run after concurrency, stale-copy, service-scope and CLI partial
  status regressions: **91 passed, four warnings, 3.57s**, `c2-focused-final.log`.
- Full eligible repository suite: **302 passed, two documented A/B child-process
  skips, four inherited warnings, 5.74s**, `c2-suite.log`.
- Logs are under `/private/tmp/blender-c1`; all runs reported zero audited real
  process/network attempts. No failed C2 tests, services, Blender processes, SSH,
  Slurm/Modal allocations or paid compute. The opt-in guard still prevents any
  real boundary attempt from passing silently.
- `git diff --check` passed. Final diff review checked that result paths contain
  no admission/upload fallback, acknowledgements cannot start compute, successful
  artifacts remain visible on partial failure, and old download TTL/behavior is
  not silently redefined.

The initial C2 implementation was subsequently blocked by the two reproduced
failures below; this paragraph's original readiness assessment is superseded.
At that point no installed-wheel or real
backend/storage handoff validation is claimed; destructive loss of both local
verified copies and expired/deleted originals is not recoverable. Broader gateway
and hosted adoption of the new acknowledgement policy awaits compatible backend
installation and interface work, not a silent deployment in this task.

Next: **C3 backend parity** (runtime/capability and execution/storage compatibility,
including separately authorized real profile checks). Then **D interfaces/preset
selection**, then **E installed-product validation**. Stop at C2 for review.

### C2 blocker corrections — authoritative artifact lifecycle

Starting state: branch `codex/c2-result-recovery`, HEAD
`4878801b7b8e3379cfeb9642f7db18c2812f0749`, with the C2 changes listed above
already uncommitted. The read-only review reproduced (1) handoff copies surviving
acknowledgement and expiry and (2) a fresh receipt reporting success without PNG.
Those initial tests did not prove these cases; the earlier completion claim is
superseded by this correction and its regression evidence. No C3 began.

Files changed in this correction: `blender_render/artifacts.py` (new shared
inventory/copy lifecycle), `jobs.py` (completion, migration, tombstones and
handoff), `service.py` (artifact lookup through authorized managed copies),
`api.py` (inventory metadata query), `delivery.py` (required-output receipts),
`tests/test_result_recovery.py`, `docs/result-recovery.md`, and this file.
Existing C2 edits in `client.py`, `cli.py` and `docs/operation-lifecycle.md` were
preserved. Renderer, presets, RenderSpec, cameras, materials and geometry are
unchanged. No commit or branch switch was performed.

**State invariants.** Completion freezes expected name, hash, size, original media
type, required flag and expiry. Mutable availability is separate and authoritative.
Delivered/expired/deleted/lost never transition back to available. Physical copies
cannot authorize a tombstone. Operator-selected roots register durably before
handoff; all registered roots and the legacy root are reconciled. Tombstones commit
before deletion, including interrupted acknowledgements. Cleanup sweeps terminal
jobs and orphan staging/job directories, is repeatable and does not follow links
outside managed storage. Result exposure can persist loss and retry cleanup, but
has no compute admission route. No TTL extension or delivery-policy change.

**Recovery contract.** The additive `result?inventory=true` query exposes expected
inventory plus current availability after bytes are gone. Ordinary terminal result
and artifact retrieval retain HTTP 410. Available download lists are separate.
Receipts include expected/required flags, availability, verified-local state and
checksum failure. A known required inventory plus verified PNG is mandatory for
completion; requested scenes are also required. Manifest is diagnostic. Incomplete
results retain verified outputs, identify missing required artifacts and return
`artifact_unavailable` / `recover_verified_local_copy_or_stop`. Result-only retries
can finish transient transfer failures while remote bytes remain available.

**Migration.** Additive SQLite columns/root registry preserve jobs, reservations,
charges and original expiry. Old rows use trusted result/manifest descriptors and
prior draft acknowledgement metadata; PNG is required conservatively. Unknown
original required inventory cannot produce success. New acknowledgements rely on
the single artifact-state record, not the legacy receipt table. Version-1 local
receipts require server inventory refresh before asserting completion. Destructive
loss and copies outside registered managed storage cannot be recovered or swept.

| Final C2 criterion | Evidence / assessment |
|---|---|
| Immutable expected inventory survives consumption/deletion/expiry | Proven: parameterized lifecycle test compares every descriptor and original expiry before and after four terminal/partial cases. |
| Handoff then PNG ack then replacement does not resurrect PNG | Proven: partial and all-delivered cases delete source plus two registered copies; deliberately restored stale PNG is purged at replacement. |
| Remaining artifacts retain original availability/TTL | Proven: partial delivery retains scene/manifest; expiry removes every remaining managed directory. |
| Expiry before replacement, explicit deletion, orphan staging, repeated cleanup | Proven: four-case replacement test plus legacy/transient deletion and symlink safety test. |
| Interrupted file deletion cannot undo durable delivery | Proven: forced cleanup OSError after ack commit; replacement sees delivered and purges every PNG copy. |
| Lost tombstones cannot be restored | Proven: missing PNG reconciled to lost, physical recreation stays unavailable and is purged. |
| Fresh client after consumed PNG remains incomplete | Proven: legacy consuming GET followed by fresh receipt; missing required PNG and stable error/action; remaining artifacts do not establish success. |
| Existing receipt, missing/modified local PNG | Proven: restart/reuse succeeds only with verified bytes; missing and modified files fail, modification records checksum failure. |
| PNG default / explicitly requested scene / diagnostic manifest | Proven: both scene settings with missing manifest; verified required outputs complete, diagnostic error stays visible. |
| Partial transfer, result-only resume, atomic/checksummed output | Proven: interrupted scene, corrupt response, unwritable destination, ack response loss, concurrent receipt serialization and fresh-destination recovery. |
| Owner/service scope; preview non-consuming; legacy deletion preserved | Proven: authenticated API contracts, receipt isolation, preview and consuming/non-consuming retrieval tests. |
| Legacy migration preserves accounting and avoids false success | Proven: metadata-present and metadata-absent migration tests retain charges/reservations; unknown inventory cannot complete. |
| No recovery dispatch or real compute | Proven at fake boundary: original backend call list stays one job; recovery traffic limited to GET/ack; audit reports no process/network attempts. |
| Installed package contains and executes the final lifecycle contracts | Proven: 104 outside-checkout tests, imported module paths asserted, all 71 package Python/JSON files match wheel. |
| Real profile/storage replacement and installed-host UX | Intentionally unverified; C3/E, not evidence claimed here. |

**Exact execution.** Repository commands ran from
`/Users/mb/Documents/blender_bridge-release` with command-prefix assignments
`PYTHONPATH=tests PYTHONDONTWRITEBYTECODE=1 BLENDER_RENDER_HOME=/private/tmp/blender-c1/home PYMOL_BRIDGE_SESSION_ROOT=/private/tmp/blender-c1/sessions PYMOL_BRIDGE_TMP_ROOT=/private/tmp/blender-c1/runs`:

```sh
.venv/bin/python -m pytest -q -p no:cacheprovider -p c1_boundary_guard tests/test_result_recovery.py tests/test_jobs_api.py tests/test_operation_lifecycle.py tests/test_cli_operation_routing.py > /private/tmp/blender-c2-fixes/focused.log 2>&1
.venv/bin/python -m pytest -q -rs -p no:cacheprovider -p c1_boundary_guard > /private/tmp/blender-c2-fixes/suite.log 2>&1
git diff --check
```

Final focused: **104 passed, 4 inherited warnings, 4.06s**. Repository suite:
**315 passed, 2 intentional A/B child-process skips, 4 inherited warnings, 6.22s**.
Both logs report `C1 audit: real process/network boundary attempts = []`.
The skipped tests are `test_snapshot_tracks_python_and_assets` and
`test_save_restart_reuse_and_revision`; no Blender previews were necessary or run.
Intermediate runs had three failures (two old expectations superseded by inventory
recovery and registered-copy lookup, one legacy unavailable-result regression),
then one HTTP 404-versus-410 regression; corrected before final passes. No
pre-existing failing test or unresolved new failure was identified.

Wheel source staged with `shutil.copy2/copytree` into
`/private/tmp/blender-c2-fixes/wheel-source`: `pyproject.toml`, README, licenses,
notices and the current package; tests copied to `wheel-check`. No generated
build files were written into the checkout. Existing isolated environment used:
`/private/tmp/blender-render-b-fixes/installed-env` (setuptools 79.0.1 and bundled
wheel already available). No build dependencies fetched and dev env unchanged.
From the staged source, then the temporary task directory respectively:

```sh
/private/tmp/blender-render-b-fixes/installed-env/bin/python -c 'import setuptools.build_meta; print(setuptools.build_meta.build_wheel("/private/tmp/blender-c2-fixes/wheels"))' > /private/tmp/blender-c2-fixes/build.log 2>&1
/private/tmp/blender-render-b-fixes/installed-env/bin/python -m pip install --force-reinstall --no-deps --no-index /private/tmp/blender-c2-fixes/wheels/pymol_plugin_bridge-0.2.0b1-py3-none-any.whl
```

Both passed offline. Pip reported its inherited unwritable-cache warning and
disabled caching. Wheel SHA-256:
`fbadcee1b2c92f82d89f5c9ccf9af68f215bb3a258270216b9c4cb96d4175a84`.
ZIP byte comparison verified **71/71 package Python/JSON files** against checkout.

Installed tests ran from the isolated environment's `lib/python3.11/site-packages`
with `PYTHONPATH=/private/tmp/blender-render-b-fixes/installed-env/lib/python3.11/site-packages:/private/tmp/blender-c2-fixes/wheel-check`,
`PYTHONDONTWRITEBYTECODE=1`, and isolated home/session/run variables under
`/private/tmp/blender-c2-fixes/installed-{home,sessions,runs}`. The dev Python
executed this inline runner (output `installed.log` in the temporary task):

```python
from pathlib import Path
import blender_render.artifacts, blender_render.delivery, pytest
site=Path('/private/tmp/blender-render-b-fixes/installed-env/lib/python3.11/site-packages')
for module in (blender_render.artifacts, blender_render.delivery):
    assert Path(module.__file__).is_relative_to(site), module.__file__
    print('Installed module:', module.__file__)
root=Path('/private/tmp/blender-c2-fixes/wheel-check')
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','-p','c1_boundary_guard'] + [str(root/name) for name in ('test_result_recovery.py','test_jobs_api.py','test_operation_lifecycle.py','test_cli_operation_routing.py')]))
```

Installed result: **104 passed, 4 inherited warnings, 4.24s**, zero audited boundary
attempts. This reuses dev test dependencies; it is not a clean dependency install.
Only packaging/test runner processes were used. No service, Blender, SSH, Slurm,
Modal, network compute, credentials change, deployment or paid compute occurred.

Final diff review checked shared copy deletion, commit-before-delete ordering,
terminal/orphan sweeps, required-PNG completion and absence of recovery admission
fallbacks. Whitespace check passed. **C2 corrections are proven at the requested
fake/local and installed-wheel contract boundaries and remain uncommitted for
review.** Real backend/storage checks remain unverified. Next bounded task remains
C3 backend parity (consistent resolved inputs, runtime/capability validation and
result delivery across profiles), followed by D interfaces/preset selection and
E installed-product validation. None began in this correction.

### C2 final review follow-up — 2026-09-09

The subsequent read-only review found three additional defects not covered by the
104/315/104 evidence above: a consumed required PNG was still advertised as a
usable content-reuse result; pre-C2 partial-delivery migration could resurrect a
stale PNG; and trusted macOS path aliases were rejected. That review supersedes
the previous blanket closure statement. This follow-up implements the approved
three-fix plan only, on the same branch/HEAD, preserving all uncommitted C2 work.

Changed in this follow-up: `blender_render/artifacts.py`, `blender_render/jobs.py`,
`tests/test_result_recovery.py`, `docs/result-recovery.md`, and this progress file.
No engine, preset, RenderSpec, camera, geometry, material, backend launcher or
interface-selection implementation changed. No commit or branch switch occurred.

| Review correction / acceptance | Final executed evidence |
|---|---|
| Required remote artifacts determine reusable completion | A shared assessment requires known inventory and every required artifact available. Both consumed-PNG and consumed-requested-scene tests return the same historical operation, `reused=true`, unavailable/expired status and `result_available=false`, with one original dispatch. Ordinary result returns artifact-unavailable; inventory and remaining artifact recovery still work. |
| Missing diagnostics do not determine completion | PNG-only and explicit-scene tests now also assert ordinary result and status remain usable after manifest loss, before verified client delivery. |
| Preserve pre-C2 partial-delivery tombstones | A genuine shrinking result list, with no draft acknowledgement records and a deliberately stale PNG on disk, migrates PNG to delivered and purges it. Remaining scene/manifest survive; charges, reservation and expiry stay identical. Unknown inventories remain incomplete. |
| Trusted path aliases work without following substituted roots | Configured paths canonicalize before storage use. Alias and canonical handoff paths register once. Substituting a registered canonical handoff root with a symlink does not delete external user files. |
| Replacement follows the single-controller contract | Replacement/migration/interrupted-cleanup tests close the original store before opening the replacement; no live service is involved. |
| Lost result-metadata response is recoverable | First GET executes then its response is discarded before receipt creation. A fresh result-only retry completes the same job; traffic is only GET/ack and original dispatch count stays one. |
| Earlier C2 contracts remain covered | All receipt, checksum, partial delivery, tombstone, orphan cleanup, ownership, preview, retention and operation-retry contracts pass in focused and installed suites. |

Migration uses original manifest metadata only for expectation. For older completed
rows, artifacts omitted from the trustworthy shrinking current list are delivery
tombstones, not available files. Unknown absent entries become lost. Existing
new-format immutable inventories are not rewritten. Tombstones still commit
before physical purging. Read-only status does not create compute; the shared
availability assessment does not mutate operation identity or render history.
Terminal `delivered` remains delivered; other incomplete remote results retain
the existing unavailable/expired response compatibility. No automatic rerender.

Exact final repository tests ran from the release checkout with prefix:
`PYTHONPATH=tests PYTHONDONTWRITEBYTECODE=1 BLENDER_RENDER_HOME=/private/tmp/blender-c2-final/home PYMOL_BRIDGE_SESSION_ROOT=/private/tmp/blender-c2-final/sessions PYMOL_BRIDGE_TMP_ROOT=/private/tmp/blender-c2-final/runs`.

```sh
.venv/bin/python -m pytest -q -p no:cacheprovider -p c1_boundary_guard tests/test_result_recovery.py tests/test_jobs_api.py tests/test_operation_lifecycle.py tests/test_cli_operation_routing.py > /private/tmp/blender-c2-final/focused.log 2>&1
.venv/bin/python -m pytest -q -rs -p no:cacheprovider -p c1_boundary_guard > /private/tmp/blender-c2-final/suite.log 2>&1
git diff --check
```

Focused: **109 passed, 4 inherited warnings, 4.16s**. Repository: **320 passed,
2 intentional skips, 4 inherited warnings, 7.20s**. The unchanged skips are
`test_snapshot_tracks_python_and_assets` and `test_save_restart_reuse_and_revision`,
the two A/B child-process acceptance tests outside this fake-only task. An initial
focused run also passed 109 tests before strengthening diagnostic-status assertions.
No new test failure occurred in this follow-up; the reviewed defects are separately
documented above, not treated as passes in the earlier evidence.

Offline packaging reused `/private/tmp/blender-render-b-fixes/installed-env`.
Source and tests were copied with Python `shutil` into
`/private/tmp/blender-c2-final/{wheel-source,wheel-check}` using the same staging
file set as above. From the staged source, then the temporary task directory:

```sh
/private/tmp/blender-render-b-fixes/installed-env/bin/python -c 'import setuptools.build_meta; print(setuptools.build_meta.build_wheel("/private/tmp/blender-c2-final/wheels"))' > /private/tmp/blender-c2-final/build.log 2>&1
/private/tmp/blender-render-b-fixes/installed-env/bin/python -m pip install --force-reinstall --no-deps --no-index /private/tmp/blender-c2-final/wheels/pymol_plugin_bridge-0.2.0b1-py3-none-any.whl > /private/tmp/blender-c2-final/install.log 2>&1
```

Build and install passed without fetching dependencies; pip disabled its
unwritable cache. Final wheel SHA-256:
`f69444184425be2b5af0846d3c51e50b2b1191a7ffb822085845e4feaca7c812`.
Read-only ZIP/hash comparison verified all **71 package Python/JSON files** equal
between checkout, wheel and the installed package.

Installed tests ran from the existing isolated environment's site-packages with
`PYTHONPATH=/private/tmp/blender-render-b-fixes/installed-env/lib/python3.11/site-packages:/private/tmp/blender-c2-final/wheel-check`,
`PYTHONDONTWRITEBYTECODE=1`, isolated `installed-home`, `installed-sessions` and
`installed-runs` under `/private/tmp/blender-c2-final`, using dev Python and this
inline runner (output `/private/tmp/blender-c2-final/installed.log`):

```python
from pathlib import Path
import blender_render.artifacts, blender_render.delivery, pytest
site=Path('/private/tmp/blender-render-b-fixes/installed-env/lib/python3.11/site-packages')
for module in (blender_render.artifacts, blender_render.delivery):
    assert Path(module.__file__).is_relative_to(site), module.__file__
    print('Installed module:',module.__file__)
root=Path('/private/tmp/blender-c2-final/wheel-check')
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','-p','c1_boundary_guard']+[str(root/name) for name in ('test_result_recovery.py','test_jobs_api.py','test_operation_lifecycle.py','test_cli_operation_routing.py')]))
```

Installed: **109 passed, 4 inherited warnings, 4.27s**. All three final test logs
report zero audited real process/network attempts. Packaging/test runners are the
only executed processes; no service, Blender, SSH, Slurm, Modal, deployment,
credentials change or paid compute. This remains installed-wheel contract coverage
using dev dependencies, not a clean dependency-install or real backend validation.

Final review checked shared required-output assessment, unchanged retry identity,
legacy removals taking precedence over physical copies, canonical root deduplication
and substitution safety, and no recovery admission fallback. `git diff --check`
passed. The three reproduced blockers are now covered by executed regressions;
changes remain uncommitted for independent checkpoint review. Broad hashing
performance optimization and physical power-loss durability remain unverified.
Real local controller/storage replacement, Slurm filesystem/tunnel recovery,
Modal controller/Volume persistence and cross-profile runtime/result parity remain
deferred to C3/E with separate authorization. No C3, D or E work began.

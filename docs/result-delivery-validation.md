# Result delivery validation

## Compact GIF viewer follow-up

A subsequent ChatGPT attempt reached file materialization and then reported the
conversation-length limit. Live read-only records show the latest render completed
in 117.2 seconds and produced a 1,782,692-byte GIF. The exact ChatGPT failure cause
is not visible from the service. Previously the full GIF appeared as base64 in
widget metadata and as a native file-import reference. The updated GIF response
contains neither: its viewer receives only a signed preview URL, while a bounded
PNG image remains available to the model. The preview route verifies ownership
through the signature and preserves the artifact until its existing expiry.
There are no written download links, GIF import prompts requested by this tool,
or new GPU jobs for preview access. Permanent storage in ChatGPT is not promised.
The earlier embedded-GIF evidence below describes the superseded implementation.

The September 7 record below describes the former link-based flow. The current
`codex/chatgpt-inline-results` change replaces those written links with an inline
preview and native original-file handoff. Returning a reference does not confirm
host storage; cleanup still follows actual transfer or expiry. A fresh ChatGPT
import/display check is required for this change.

## September 18 preset/GIF workflow follow-up

The user supplied a conversation transcript showing a missing gradient, a manually
repainted image, and many tool calls after a GIF request. The consumer had forced
transparency, and its MCP request schema exposed no animation controls. The
server's actual recent job timings showed roughly 45–49 seconds of execution,
including 8–10 seconds of conversion, but repeated admissions queued later jobs
for more than eight minutes. These are service timings, not a full ChatGPT trace.

Implemented preset-inherited transparency, exact camera/background lookup,
structured one-job animation, direct primary GIF handoff, embedded GIF preview,
and concise server/skill instructions prohibiting per-frame submissions and
misrepresenting image edits as preset rendering. Owner-only animation opt-in uses
the existing deployment workflow; unrestricted hosted animation stays disabled.

Executed a real public 1QYS CIF consumer request through local Apptainer/RTX GPU,
PyMOL export and in-process MCP presentation. The 640×360, 32-sample, 36-frame
spin completed in 79.95 seconds, including 0.30 seconds for conversion. The GIF
was 1,853,978 bytes with infinite looping. Opaque PNG alpha and authored gradient
were checked, and the PNG was visually inspected. The MCP result contained one
GIF attachment and embedded matching GIF bytes; it did not request frame-archive
materialization. This is not a hosted L4 timing measurement or ChatGPT UI gate.
Reproduction: `scripts/validate_consumer_animation.py` with an installed wheel,
GPU image, PyMOL Python and a new output directory. No research structure was used.

## September 18 viewer follow-up

The user reported a file-materialization approval prompt followed by repeated
render captions with no visible image. Read-only inspection confirmed a completed
job whose PNG remained available, not delivered. Native image/file content alone
did not establish display or attachment import in that ChatGPT conversation.

The follow-up binds `render_result` to a new image viewer, passing the existing
bounded PNG in hidden tool-result metadata. The viewer has no external image
fetch, download button, or render/submission action. Both MCP Apps notifications
and ChatGPT metadata envelopes are supported. Missing or invalid preview data
produces an explicit error. Full-resolution attachment delivery and expiry are
unchanged; the ChatGPT materialization permission cannot be bypassed by the app.

Real Chromium with simulated host envelopes passed initial display, notification,
wrapped metadata, legacy metadata, missing data, and invalid URL rejection, without
network requests. This is browser execution evidence, not a ChatGPT acceptance
check. The new viewer still requires an actual refreshed ChatGPT connection test.
The viewer update deployed successfully to the existing owner-only endpoint.
Live health and OAuth resource discovery returned 200, and unauthenticated MCP
returned 401. Full tests: 1,304 passed; installed-wheel tests: 184 passed.

Reproduce with `scripts/validate_inline_widget.py --browser /path/to/chromium`
using Playwright and an installed package outside the checkout.

## September 18 inline-delivery rollout

The staged inline-delivery build deployed successfully to the existing owner-only
service after retrying the provider billing blocker. Health and OAuth discovery
passed; unauthenticated MCP access was rejected. Existing ownership and lifetime
accounting were preserved. No GPU render was submitted in this deployment check.
Repository tests: 1,303 passed; installed-wheel focused tests: 154 passed.
Actual ChatGPT inline display, native attachment import, and retention after
reopening remain unverified. Deployment health does not establish those UI gates.

## Historical September 7 link-based validation

Validated on 2026-09-07 against the private, owner-only Modal service.

The user's pastel `1sps_pose.pse` render completed in about 31 seconds
(job `90f9193d1225b5c53800d46172cbda5b`), but its ChatGPT card stayed on
"Loading the completed render" without an image or downloads. The original
PNG, Blender scene, and manifest were recovered before expiry. The PNG is
2048 by 1536, RGBA, with alpha values from 0 to 255.

The result tool now supplies a bounded native image and ordinary **View PNG
preview**, PNG, Blender scene, and manifest links. It has no output-template
binding and no MCP resource attachments. The old UI resource remains readable
for compatibility. Resource attachments caused repeated file-materialization
prompts during a browser test; ordinary links let the reply finish without
importing all files into ChatGPT. The skill requires a final answer containing
usable links; an activity caption is insufficient.

## Executed checks

| Check | Result |
|---|---|
| Repository suite | 111 passed; four upstream deprecation warnings |
| Installed wheel outside repository | 39 relevant tests passed |
| Live authenticated MCP result | Image and text only, no resource attachments; preview URL and download links returned successfully |
| Preview payload | 199,526 bytes, 1024 by 768, RGBA, alpha 0 to 255 |
| Actual ChatGPT result retrieval | Final reply with download links; no repeated file-materialization prompt in the repaired flow |
| View PNG preview | Clicked in ChatGPT; actual pastel PNG opened and was visually inspected in Chrome |
| PNG download | Clicked in ChatGPT; downloaded 1,626,313-byte PNG, 2048 by 1536, RGBA, alpha 0 to 255 |
| Blender download | Clicked in ChatGPT; downloaded 541,593-byte scene, opened successfully by Blender 5.0.1 with scripts disabled; Cycles, transparent film, 2048 by 1536 |
| Privacy and quota | Zero pending inputs and reserved cost; owner testing exemption preserved |

The live preview/download test used public fixture `examples/1sps_pose.pse`,
`preset_1_interface`, transparency, and an optional Blender scene. Job
`56022608f80931c6be67db93ff9bbe15` ran for 47.04 seconds, completing 62.27 seconds
after submission. Including an earlier diagnostic render, two intentional GPU
test jobs were submitted; estimated conversion/render costs totaled $0.187739.
Retrieving and opening their results did not submit additional compute.

## Remaining UI limits

The checked ChatGPT Work surface hid remote Markdown images even when the
saved final answer contained the correct preview URL. The ordinary **View PNG
preview** link worked, so the result tool and skill require it explicitly.
Inline display across ChatGPT surfaces is not a passed acceptance check.

Chrome displayed ChatGPT's standard external-link confirmation before opening
preview/download links. This is distinct from the repeated attachment-import
prompts removed from the result flow. Global permissions were not changed.

An earlier output-only follow-up in Pro mode lacked the connector; OpenAI's
[app support notes](https://help.openai.com/en/articles/11487775-connectors-in)
exclude Pro models. The successful delivery check used the existing
`Validate Blender Render` Work conversation. This verifies result retrieval,
preview opening, and downloads; it does not establish a fresh attachment-picker
upload through the final plugin in every ChatGPT mode.

Molecular styles, camera transfer, compute backends, admission, and retention
are unchanged by this repair. The downloadable PNG retains full resolution.
The baseline HPC SIF was not rebuilt. Publication remains deferred.

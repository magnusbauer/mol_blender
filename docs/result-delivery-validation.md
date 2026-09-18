# Result delivery validation

The September 7 record below describes the former link-based flow. The current
`codex/chatgpt-inline-results` change replaces those written links with an inline
preview and native original-file handoff. Returning a reference does not confirm
host storage; cleanup still follows actual transfer or expiry. A fresh ChatGPT
import/display check is required for this change.

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

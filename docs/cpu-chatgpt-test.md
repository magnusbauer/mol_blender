# Small local CPU test

This branch uses the shared Blender renderer. The test settings are explicitly
CPU, two render threads, 128 × 128 pixels and four samples. They are in
`examples/cpu-smoke-options.json`; normal presets keep their existing defaults.
The developer launcher also passes `--threads 2` to Blender and bounds common
math-library thread pools. This is a thread limit, not OS CPU affinity.

## Reproduce the local render

Use the existing managed runtime configuration containing `blender`,
`runtime_site` and `pymol_python`. The script reads that runtime without updating
it and exports the repository's public `examples/1bna.cif` through PyMOL first.

```sh
python scripts/validate_cpu_smoke.py \
  --runtime-config /absolute/path/to/existing/runtime/config.json \
  --output .validation/cpu-128
```

The output directory must be new. The script verifies the PNG dimensions,
recorded CPU device, fixed thread count, sample count, and Blender command.
The image is deliberately noisy: this checks execution, not visual quality.

## ChatGPT image handoff

The viewer now sends the current PNG/JPEG through `ui/update-model-context`
after the host initialization handshake. Its **Ask ChatGPT about this image**
button sends an image-containing `ui/message`. Capability checks, bounded
requests, duplicate suppression and explicit errors prevent silent loops.
A successful bridge response means the host accepted the message, not that
model perception or long-term storage has been independently established.

File saving is optional and user initiated. Saved file IDs remain private UI
state; `imageIds` is no longer the image-delivery mechanism. The new resource
URI is `ui://blender-render/inline-result-v12.html`; older URI aliases remain
readable. Refresh the connected tool/resource definitions when testing.

A local private HTTP server can now accept `serve --mcp --public-url
https://YOUR_TEST_ORIGIN`. The HTTPS origin is used consistently for signed
image/download URLs and widget CSP. The server remains on loopback and retains
bearer authentication; an HTTPS proxy and a compatible registered ChatGPT
authentication flow still need to be supplied. Do not expose an unauthenticated
render service. This is separate from the Linux/Apptainer lab OAuth gateway.

For the CPU test, send the limits in `examples/cpu-smoke-options.json` as the
render request (with a unique `request_key`). They are request settings, not a
server-wide resource cap. No Modal or cluster deployment is needed.

## Executed validation, 2026-10-13

- Apple M3 Mac; public 1BNA; actual Blender 5.0.1 render completed.
- CPU, FIXED two threads, 128 × 128, four samples verified in actual outputs.
- Blender process time: 9.74 seconds, including process startup and scene loading.
- PNG visually inspected; deliberately noisy DNA preview is visible.
- CPU/schema/resolution/developer checks: 97 passed.
- Result delivery/MCP/CLI/animation regression checks: 47 passed.
- Isolated Chrome viewer checks passed for image display, explicit file saving,
  saved-file reopening, initialization, standard context/message image bytes,
  duplicate suppression and host rejection. This used a simulated host.
- Built wheel installed outside the checkout: nine MCP/result-delivery checks passed.
- Real ChatGPT browser/desktop model-perception checks are still pending.

Machine paths, generated environments and local artifacts remain ignored. The
original checkout and its linked worktrees were not modified.

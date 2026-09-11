# Direct access from an approved lab network

An operator can explicitly enable a lab-network proxy in front of the private
renderer. This is an opt-in exception to the usual client-token workflow. The
Blender controller remains authenticated on loopback. The proxy allows only its
configured source CIDRs, supplies a separate controller credential, and scopes
jobs to the actual TCP source IP. It ignores client-supplied identity and
forwarding headers. It never distributes a credential to clients.

This is **workstation identity**, not individual login identity. Users sharing a
workstation, Digs session host, NAT address, or SSH tunnel endpoint share access
to that identity's jobs. Direct HTTP traffic is plaintext inside the approved
network. Use the existing authenticated HTTPS or SSH workflows when individual
identity or encrypted transport is required.

## PyMOL

Restart PyMOL after updating the plugin. Load the current
`blender_render/pymol_bridge_plugin.py`, then use the hostname
provided by the lab operator:

```python
fetch 1qys
blender_render mode=lab, server_host=RENDER_HOST, server_port=8768, rough=1
```

No client token or tunnel is needed inside the allowed lab subnets. `mode=lab`
explicitly selects the lab HTTP transport. It disables environment HTTP proxies,
rejects redirects and public destination IPs, and connects to the same private
IPv4 address that it validated. Normal local, SSH and HTTPS modes retain their
existing behavior. A missing or unresolvable hostname fails instead of falling
back to another backend.

The same exported scene goes to the normal FIFO queue and Blender renderer.
Preset 1 remains the default. The PNG is downloaded to `render.png` in PyMOL's
current directory and displayed. `output_dir` and `output_name` control its
location; `save_blend=1` requests a Blender scene. Remove `rough=1` for full
quality. Remote rendering works from macOS without a local Blender installation.

Outside the lab, use your SSH access to the render host:

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8768:127.0.0.1:8768 USER@RENDER_HOST
```

Keep the terminal open and use `server_host=127.0.0.1` in the same PyMOL command.
Use your site's SSH jump host if required. This needs the operator to allow the
proxy's loopback source separately.

## Other clients

The proxy serves the same scene-upload, job, cancellation, status, and download
API to Python or other HTTP clients. In Python, use
`Client('http://RENDER_HOST:8768', '', lab_http=True)` from
`blender_render.client`. A configured endpoint profile can use
`{"kind":"lab_http","url":"http://RENDER_HOST:8768","private":true}`.
File-upload/conversion, administrator and internal resolved-execution routes are
not exposed. Export a scene locally through PyMOL; untrusted PSE conversion needs
the separate sandboxed gateway.

## Operator setup

Run `scripts/lab_network_proxy.py` on the same host as the private renderer. Use
an operator-owned mode-0600 credential with `serve --controller-token-file`,
separate from the operator/admin token. Keep the private controller bound to
loopback, for example port 8767. Explicitly configure only the site's lab CIDRs:

```sh
python scripts/lab_network_proxy.py --port 8768 --upstream-port 8767 \
  --allow-network LAB_CIDR --allow-network 127.0.0.1/32 \
  --controller-token-file /private/service/controller-token
```

`--start-command-file` optionally names an operator-owned JSON argv file for an
idempotent controller starter. The proxy calls it only when a new upload or
submission cannot establish its upstream connection before sending HTTP bytes.
It never restarts on status reads or replays a possibly submitted request. The
starter must serialize startup, check the existing service, and retain its
database, tokens, lifetime budgets and ownership across restarts. The renderer
still stops after 15 idle minutes; the lightweight proxy can remain available.
An explicit administrative drain remains closed until the operator resumes it;
it is not the same as an idle shutdown.

Before changing an existing controller, drain and stop it. The proxy has no
second queue or renderer. It rejects unbounded/chunked uploads, ambiguous request
lengths, public CIDRs, and client attempts to replace its upstream credentials.
Do not place it behind a source-address-rewriting proxy and assume individual
workstation identities survive.

The [DokuWiki draft](wiki/good_viz_blender.txt) is ready to adapt with the lab's
actual host and installation path. Publishing that draft is separate from
editing this repository.

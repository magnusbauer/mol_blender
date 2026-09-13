# Plugin installation and private routing

Blender Render packages one reviewed renderer behind explicit connection routes.
A generated plugin contains exactly one MCP server and never falls back to a
different Blender Render service.

| Route | MCP transport | Intended host | Compute behind the route |
| --- | --- | --- | --- |
| `hosted` | HTTPS plus the repository's registered app mapping | Codex or ChatGPT using the owner's restricted private beta | Owner-operated reviewed service |
| `private-stdio` | Local process | Codex CLI or desktop app | Local Blender, Apptainer, private HPC, or an explicitly private HTTPS profile |
| `private-http` | HTTPS | Codex; ChatGPT only after explicit connection registration | Self-hosted local/HPC gateway or a personal Modal deployment |

The package route and compute backend are separate. A self-hosted HTTP endpoint
can dispatch to local Blender or Slurm without changing the plugin transport.
Personal Modal is one possible service behind `private-http`; it is not a second
renderer.

OpenAI documents plugin installation and marketplace management in
[Use plugins](https://learn.chatgpt.com/docs/plugins), the package format in
[Build plugins](https://learn.chatgpt.com/docs/build-plugins), and MCP inspection
in [Model Context Protocol](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
The procedures below use the commands provided by the supported Codex CLI.

## Install the owner private beta

The checked-in `personal` marketplace points at the reviewed owner/private-beta
plugin. Register the repository root as a local marketplace, install the named
plugin, and inspect the result:

```sh
codex plugin marketplace add /absolute/path/to/blender_render
codex plugin add blender-render@personal
codex plugin marketplace list --json
codex plugin list --json
```

This installs only the plugin definition. It does not deploy the service,
authenticate an account, start Blender, or prove that the private beta accepts
the current user. Authentication and hosted acceptance remain live D5/E gates.

## Install a generated private plugin

Install the project wheel from an offline build before generating a local
package. The executable supplied for `private-stdio` must be an explicit literal
absolute path: `~`, environment-variable expressions, and relative paths are
rejected and are never expanded through the ambient home directory.

For local Blender, generate and install a complete private marketplace:

```sh
/absolute/path/to/blender-render plugin marketplace \
  /absolute/path/to/blender-render-local-marketplace \
  --route private-stdio \
  --name blender-render-local \
  --marketplace-name blender-render-local \
  --profile local \
  --executable /absolute/path/to/blender-render

codex plugin marketplace add /absolute/path/to/blender-render-local-marketplace
codex plugin add blender-render-local@blender-render-local
codex plugin marketplace list --json
codex plugin list --json
```

For a configured private HPC profile, first add the profile and then name it
explicitly during generation:

```sh
blender-render profile add ipd --file /absolute/path/to/private-ipd-profile.json

/absolute/path/to/blender-render plugin marketplace \
  /absolute/path/to/blender-render-ipd-marketplace \
  --route private-stdio \
  --name blender-render-ipd \
  --marketplace-name blender-render-ipd \
  --profile ipd \
  --executable /absolute/path/to/blender-render
```

`local` and `apptainer` are built-in private profiles. Other STDIO profiles must
already exist and be `kind: "hpc"` or explicitly marked `private: true`. A
shared HTTPS profile is rejected. Local STDIO has no hosted `.app.json` and does
not contain the owner's endpoint or app ID.

For a credential-free private HTTPS MCP endpoint:

```sh
/absolute/path/to/blender-render plugin marketplace \
  /absolute/path/to/blender-render-lab-marketplace \
  --route private-http \
  --name blender-render-lab \
  --marketplace-name blender-render-lab \
  --url https://render.example.edu/mcp
```

An OAuth-capable endpoint handles its own OAuth flow. A bearer-token service may
name an environment variable, but the generator never reads or writes the
secret:

```sh
/absolute/path/to/blender-render plugin marketplace \
  /absolute/path/to/blender-render-private-marketplace \
  --route private-http \
  --name blender-render-private \
  --marketplace-name blender-render-private \
  --url https://render.example.edu/mcp \
  --bearer-token-env-var BLENDER_RENDER_PRIVATE_TOKEN
```

ChatGPT cannot call a laptop's loopback service. A ChatGPT route needs an
authenticated TLS endpoint and the technical ID of an independently registered
MCP connection:

```sh
/absolute/path/to/blender-render plugin marketplace \
  /absolute/path/to/blender-render-chatgpt-lab-marketplace \
  --route private-http \
  --name blender-render-chatgpt-lab \
  --marketplace-name blender-render-chatgpt-lab \
  --url https://render.example.edu/mcp \
  --app-id plugin_asdk_app_REPLACEWITHREGISTEREDCONNECTION
```

A personal Modal endpoint additionally requires an explicit, non-secret audit
identity. It is not a token, email address, or account credential:

```sh
/absolute/path/to/blender-render plugin package \
  /absolute/path/to/blender-render-personal-modal \
  --route private-http \
  --url https://operator--renderer.us-east.modal.direct/mcp \
  --deployment-identity personal-render-v1
```

Private HTTP requires HTTPS and canonical `/mcp`. Before comparison and output,
the generator converts Unicode hostnames to IDNA ASCII, lowercases them, removes
a terminal DNS dot and default port `443`, and normalizes the accepted MCP path
and trailing slash. It rejects credentials, queries, fragments, token-bearing
URLs, every canonical equivalent of the owner endpoint, and Modal equivalents
without a deployment identity. The serialized URL is the canonical spelling.

## Switch routes

Keep only the intended route active when handling private structures. Remove the
currently installed plugin, then install the other explicitly:

```sh
codex plugin remove blender-render@personal
codex plugin add blender-render-local@blender-render-local
codex plugin list --json
codex mcp list --json
```

To switch back, reverse the named removals/additions. Never assume a missing or
invalid private route will fall back to the owner service: generated packages
have no such fallback. Generate a route switch at a new destination. `--force`
does not convert an existing package between hosted, STDIO, HTTP, bearer, or
registered-app topologies.

## Disable or remove the shared route

In a Codex surface with the plugin menu, open `/plugins`, select the installed
owner plugin, and disable it. To remove its CLI installation and optionally its
marketplace registration:

```sh
codex plugin remove blender-render@personal
codex plugin marketplace remove personal
codex plugin list --json
codex plugin marketplace list --json
```

Only remove `personal` after confirming it is the repository marketplace you
intend to unregister. Removing a marketplace is separate from deleting its
source directory or uninstalling the Python wheel.

## Update and reinstall

Regenerate a private plugin or marketplace at the same destination only with
explicit `--force`, keeping the same route, authentication/app topology, plugin
name, and marketplace name. Endpoint, executable, and profile details may be
updated within that topology. Then remove and reinstall its registration so
that the selected package is unambiguous:

```sh
/absolute/path/to/blender-render plugin marketplace \
  /absolute/path/to/blender-render-local-marketplace \
  --route private-stdio \
  --name blender-render-local \
  --marketplace-name blender-render-local \
  --profile local \
  --executable /absolute/path/to/blender-render \
  --force

codex plugin remove blender-render-local@blender-render-local
codex plugin marketplace remove blender-render-local
codex plugin marketplace add /absolute/path/to/blender-render-local-marketplace
codex plugin add blender-render-local@blender-render-local
```

For a direct plugin directory already referenced by an existing local
marketplace, regenerate that directory and reinstall its exact selector:

```sh
/absolute/path/to/blender-render plugin package \
  /absolute/path/to/existing-marketplace/plugins/blender-render-lab \
  --route private-http \
  --url https://render.example.edu/mcp \
  --force

codex plugin remove blender-render-lab@lab-marketplace
codex plugin add blender-render-lab@lab-marketplace
```

Generator ownership format 2 has a fixed allowlist for plugin and marketplace
layouts. The marker may contain hashes only for paths derived from the explicit
requested identity and the validated route metadata; neither an optional app
file nor the marketplace manifest can expand that identity. Absolute paths,
parent traversal, malformed or duplicate entries, symlinked owned paths, and a
wrong kind/version cause safe refusal. Unknown paths never become owned and are
never deleted. `--force` requires the old and new generated path sets and route
topology to be identical, replaces only unchanged allowlisted files, and
preserves unrelated regular files. A route, authentication/app, plugin-name, or
marketplace-name change requires a new destination. Recreate old format-1
output at a new path rather than trusting its broader marker.

The generator validates the final destination after publication. Its returned
inventory and digest are exactly those from `validate_package()` or
`validate_marketplace()` and cover the full final tree, including intentionally
preserved user extras.

After upgrading the wheel, regenerate the package and repeat the remove/add
sequence. Do not edit generated files in place; a changed owned file is a safe
refusal, so use a new destination if it must be retained.

## Inspect the active installation

Confirm the installed marketplace, plugin, and MCP registry without making a
connection:

```sh
codex plugin marketplace list --json
codex plugin list --json
codex mcp list --json
blender-render --version
python -m pip show pymol-plugin-bridge
blender-render profile list
```

Inspect the selected package's transport, optional registered app, and
descriptive routing metadata directly:

```sh
python -m json.tool /absolute/path/to/plugin/.mcp.json
python -m json.tool /absolute/path/to/plugin/.app.json
python -m json.tool /absolute/path/to/plugin/routing.json
```

`.app.json` is expected only where an app connection was explicitly generated;
its absence is correct for local STDIO. `routing.json` format 2 records the
route, transport, adapter type, input source/location, expected compute,
profile description, and no-shared-fallback assertion. These fields must agree
with `.mcp.json` and optional `.app.json`; they describe the package and are not
a second runtime policy engine.

Check the installed renderer's `engine_build` identity and the preset catalog
digest:

```sh
python -c 'import json; from blender_render.engine import engine_identity; print(json.dumps(engine_identity(), sort_keys=True))'
blender-render presets
python -c 'from blender_render.presets import compact_catalog; print(compact_catalog()["digest"])'
```

The first output includes `engine_build`; the second lists packaged presets; the
third prints the catalog digest used to compare builds. `codex mcp get NAME
--json` may be used for a specific registered MCP server after finding `NAME` in
`codex mcp list --json`.

## Refresh tools and conversations

After installing, switching, or regenerating a package, start a fresh
conversation so the host loads the selected plugin and current tool schemas.
In that conversation, open `/mcp` to confirm the Blender Render server and
inspect the loaded tool schemas, including each tool name and input fields.
Schema or tool changes require another fresh conversation; an existing
conversation may retain its previously loaded definitions.

This inspection confirms configuration and loaded metadata only; it does not establish
public approval, deployed authentication, live endpoint reachability,
Blender/PyMOL execution, hosted acceptance, or paid-compute readiness.

## Uninstall and delete generated output

Remove the exact generated plugin and marketplace registrations, uninstall the
wheel, and only then delete the exact output directory you created:

```sh
codex plugin remove blender-render-local@blender-render-local
codex plugin marketplace remove blender-render-local
python -m pip uninstall pymol-plugin-bridge
rm -r -- /absolute/path/to/blender-render-local-marketplace
```

Do not use a repository root, home directory, glob, or unresolved variable as
the deletion target. Removing generated output does not remove profile files;
review those separately before deleting them.

## Packaging boundary

`plugin package` emits one plugin directory for an existing marketplace.
`plugin marketplace` emits a complete new local marketplace and never edits
Codex configuration. Generated variants contain no molecular inputs,
credentials, caches, machine state, owner identity, or rendered artifacts. They
are written only to the requested output directory and are not included as
user-specific wheel or source-distribution package data.

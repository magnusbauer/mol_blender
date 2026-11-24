# mol-blender documentation

Start with the installation route for the way you want to work. Local rendering
is the default; a shared cluster is optional.

## Get started

- [Windows native local rendering](install-windows.md): PyMOL command, isolated runtime and reversible startup integration.
- [Windows validation scope](windows-validation.md) and [Fork/PR walkthrough in Chinese](windows-git-guide-zh.md).

- [Control desktop PyMOL with co-PyMOL](install-co-pymol.md): connect Codex,
  Claude Code or Cursor to your live PyMOL session, then enable Blender rendering.
- [Install molblender-studio in Claude](install-claude.md): build the complete
  plugin or skill ZIP, install it in the chosen Claude environment and set up its
  runtime.
- [Install the renderer CLI and PyMOL bridge](../README.md#install-and-configure):
  configure the renderer directly and make a first local image.

An agent can follow these guides too. Give it this repository and the
[installation instructions in AGENTS.md](../AGENTS.md#installation-requests),
and specify your client and workflow.

## Render and edit

- [co-PyMOL usage and troubleshooting](co-pymol/README.md): live-session control,
  example requests, upgrades and removal.
- [Presets and selections](reference-presets.md): appearance, public examples,
  camera behavior and adapting chain-specific styles.
- [Animation](animation.md): camera motion, recipes, MP4/GIF output and limits.
- [Edit a look in Blender](blender-editing.md): save supported scene edits as a
  reusable preset.
- [Edit through local chat](chat-editing.md): connect to a Blender window, change
  its look, preview and save.

## Advanced usage and administration

- [Repeated local rendering](local-worker.md): start, inspect and stop the optional
  warm Blender worker.
- [Runtime environments](runtime-environments.md): separate PyMOL and Blender
  environments, pinned dependencies and Linux/Apptainer builds.
- [Shared cluster rendering](shared-cluster.md): connect to an existing service,
  recover jobs, or configure and operate a Slurm gateway.

## Developer reference and release information

- [Completed-render delivery API](hosted-adapter.md): the optional adapter for
  developers embedding completed images in an authenticated host.
- [Third-party notices](../THIRD_PARTY_NOTICES.md) and
  [current runtime sources](../mol_blender/runtime-sources.json).
  The [historical dependency inventory](dependency-source-manifest.json) preserves
  upstream provenance, including dependencies no longer used; it is not the
  current dependency list.

# Local renderer development

Use `python -m blender_render.cli dev` from a trusted checkout when inspecting or validating newly edited Python/schema. The installed `blender-render dev preview` command also previews a checkout: `--checkout` defaults to the working directory, never site-packages. This is a small command-line workflow around the existing resolver, StructureBundle and rendering transaction. It does not contact a service, authenticate, allocate compute or deploy code. The separate [developer skill](../plugins/blender-render/skills/develop/SKILL.md) permits trusted Python extension when requested; the consumer skill is unchanged.

## Inspect, edit and validate

```sh
blender-render dev catalog
blender-render dev inspect builtin/preset_1_interface
blender-render dev schema
blender-render dev validate examples/developer/macro_focus.json
```

`inspect` returns the complete JSON alongside identity; `schema` supplies supported controls, units and bounds. Edit the existing owning Python module when JSON cannot express a behavior. Materials live in the shared scene/shader helpers, representations and camera construction in `blender_custom_render.py`. Unknown JSON fields fail before Blender starts. There is no per-preset renderer and no uploaded Python execution on a service.

Absolute `camera.yaw_deg` / `pitch_deg` overrides select `mode=override` and `direction_source=preset` unless the override explicitly specifies those fields. Full JSON must declare a compatible camera; an explicit conflicting `direction_source=pymol` is rejected. To rotate relative to a saved view, use `yaw_offset_deg` / `pitch_offset_deg` with the PyMOL direction. The shipped pastel preset retains its saved-view direction; its formerly ignored absolute angles were removed. Older JSON combining that direction with absolute angles must remove the unused angles to retain its old appearance, or select the preset direction to apply them. `anchor_model_center` is now rejected because it never had a renderer implementation. These are validation tightenings, not silent camera substitutions; old saved envelopes containing these invalid controls require an explicitly corrected style and regenerated digest.

An explicit `color_follow=false` disables JSON color following while retaining its other parameters. Omitting it preserves the JSON setting. PyMOL and the legacy Python client distinguish omitted booleans from explicit false; the legacy HTTP endpoint forwards render controls, including overrides and keep-temp, to shared validation and rejects unknown controls. No compatibility adapter merges styles independently.

The Milestone B addition is **depth of field**. Existing camera fitting/focus targets controlled composition, not optical defocus. The new object enables Blender camera depth of field after final framing:

```json
{
  "camera": {
    "mode": "override",
    "projection": "PERSP",
    "depth_of_field": {
      "focus_distance_angstrom": 82,
      "f_stop": 0.5
    }
  }
}
```

This is an override fragment; [macro_focus.json](../examples/developer/macro_focus.json) is a full example for public 1SPS. Omit `depth_of_field` to disable it. Focus distance is required, 0.01–1,000,000 Å, measured from the final camera **along its optical axis**. Molecular Nodes geometry uses 0.01 Blender units per Å. This distance is independent of the saved-view transfer's `world_scale`. `f_stop` is dimensionless, 0.1–128, default 2.8; lower numbers produce stronger defocus. Explicit `mode=override` and `projection=PERSP` are required. Raw viewport output is rejected. The executed acceptance uses Cycles; EEVEE output has not been validated in this milestone. No lens, framing, selection, atom coordinates or geometry changes accompany this effect.

82 Å is specific to this example's camera. It is not a universal ligand distance. Refit/reorient a camera before choosing its focal plane. The scene inspector below reports actual vertex depth quantiles to help choose a distance.

## Preview the edited checkout

Provide a normalized bundle from the existing exporter or PyMOL adapter. Use public fixtures for acceptance, such as the repository's `examples/1sps_pose.pse`; retain the CIF-first export, DSS annotations and saved view. User-supplied PSE conversion retains the existing sandbox requirement. The developer preview takes bundle JSON rather than implementing a second input converter.

```sh
blender-render dev preview /tmp/public-1sps-bundle.json \
  --checkout "$CHECKOUT" \
  --style-json examples/developer/macro_focus.json \
  --output /tmp/macro-preview-01 \
  --blender "$BLENDER" \
  --runtime-path "$VALIDATION_SITE" \
  --runtime-path "$MN_EXTENSION_PARENT" \
  --runtime-wheels "$MN_EXTENSION_PARENT/molecularnodes/wheels" \
  --ccd "$CCD"
```

These variables identify the user's **existing local runtime**, not shareable preset settings. `--runtime-path` may repeat. When an extension ships bundled dependency wheels, `--runtime-wheels` extracts those existing files into temporary state without pip, network access, installation or Blender's shared extension installer. Their hashes are recorded. Blender's entire user-resource directory, including its extension cache, is isolated. Supplying paths to a dependency directory Blender manages itself is unnecessary when bundled wheels are available.

Each preview copies the package's Python and assets, checks that source did not change during the copy, and then starts fresh controller Python and Blender processes from that snapshot. The resolved JSON and bundle are also captured. For named local presets, the launcher checks the identifier and catalog path, then captures exact envelope bytes and their SHA-256 without validating style against its installed schema. Snapshot Python verifies that digest and validates the envelope, style and overrides before starting Blender. Thus a controller with an older schema can preview capabilities in the selected checkout; an incompatible checkout rejects them. The process invokes the same `_process_render_request` transaction and `_render` engine used elsewhere. There is no cache and output directories must be new. Timeouts terminate the preview process group. Temporary staging/source/runtime copies are cleaned unless `--keep-source` explicitly retains them for debugging.

The preview directory contains:

- `render.png`, plus `render.blend` only if JSON or `--save-blend` requests it;
- `resolved-style.json` and `manifest.json`, including the constructed camera's depth-of-field values;
- `source.json` and `preview.json`: original checkout/HEAD/dirty context, every package-file hash, source/spec/input identities, actual loaded module paths/hashes, Blender binary, versions, dependency locations and CCD identity;
- separate controller and Blender logs. A failed verification is a failed preview even if Blender wrote a PNG internally.

The full macro example explicitly requests a scene for validation. Ordinary presets and the CLI remain PNG-only by default. Generated scenes can be inspected with `scripts/inspect_developer_scene.py` in a fresh Blender process using `--disable-autoexec`; do not load arbitrary supplied `.blend` files.

## Save and restart

```sh
blender-render dev --catalog-dir /tmp/my-presets save user/macro-focus \
  /tmp/macro-preview-01/resolved-style.json \
  --description 'Selective focus on the molecular binding interface' --camera-policy authored
blender-render dev --catalog-dir /tmp/my-presets inspect user/macro-focus
blender-render dev --catalog-dir /tmp/my-presets preview /tmp/public-1sps-bundle.json \
  --preset user/macro-focus --output /tmp/macro-preview-02 \
  --blender "$BLENDER" --runtime-path "$VALIDATION_SITE" \
  --runtime-path "$MN_EXTENSION_PARENT" \
  --runtime-wheels "$MN_EXTENSION_PARENT/molecularnodes/wheels" --ccd "$CCD"
```

Commands can run in different processes. The default local catalog is `~/.config/blender-render/presets`; use `--catalog-dir` for isolated work or project storage. IDs must be `user/name` or `project/name`; the same name in both namespaces stays distinct. Built-ins cannot be overwritten. An explicit `--replace` saves the next revision. Save the plain style JSON through the validator again after editing; stored envelopes include a digest to detect accidental tampering. `--provenance` accepts a local brief/decision JSON. Local preset names are currently a developer-CLI facility; existing consumer paths can use the exported full `style_doc`. Consumer catalog/selection expansion belongs to Milestone D.

New local presets use envelope format 2 with an explicit `camera_policy`. Saving defaults to `--camera-policy preserve_input`: a saved clone of a built-in uses the same input-pose and viewport-dimension defaults. Use `--camera-policy authored` to reuse the preset's camera and authored dimensions, as required for the macro example. This is catalog metadata, not a new render-JSON field. Named presets enter the shared resolver as defaults, never as implicitly authoritative full JSON.

Camera precedence is explicit camera JSON/overrides, then preview `--preserve-view` / `--no-preserve-view`, then the saved policy (built-ins default to preservation). Full JSON remains authoritative. Explicit dimensions still win over viewport defaults. Preservation with authored depth-of-field controls fails clearly rather than dropping the effect; choose `authored` or supply an explicit compatible camera override. The save operation also rejects DOF with `preserve_input`.

Existing format-1 presets remain readable without rewriting their files. Inspection reports `camera_policy=authored` and `camera_policy_source=legacy_format_1` to preserve their previous camera behavior. An explicit replacement save upgrades to format 2 using the requested policy; its default is `preserve_input`, so use `--camera-policy authored` when retaining the old behavior. Scene generation remains controlled by `output.save_blend` or an explicit preview scene request, independently of camera policy.

`scripts/validate_developer_policy.py` complements the B matrix with an installed controller whose temporary schema lacks DOF, a checkout that rejects DOF, named/built-in live pose parity, and malformed-envelope/policy rejection before Blender startup. It records controller module/schema identity as well as the actual snapshot renderer identity. Supply `--installed-site` from the temporary built-wheel installation plus the ordinary bundle/runtime arguments.

## Reference images and proof

Inspect an authorized PNG before writing observations. Record visible representations, palette, material family, background, edges, lighting, ligand treatment and composition. Put requested capabilities in a separate list. Do not infer hidden node graphs from pixels. Run:

```sh
blender-render dev reference reference.png --observations observations.json
```

Required observation fields are `representations` (string list), `material_family` (text), `palette` (string list), and `required_capabilities` (string list, possibly empty). Optional `overrides` must pass the shared resolver; palette overrides must name materials actually used by the candidate. Other supported text fields are `background`, `lighting`, `edges`, `composition`, `ligand_treatment`, and `notes`. The agent supplies the observations and may nominate `base_preset` after inspecting the image and catalog. All ranking and fit decisions delegate to the [D1 shared selector](preset-selection.md), using the installed engine capability declaration explicitly. Exact documented aliases preserve existing observable descriptions. PNG provenance contains SHA-256 and dimensions only. Reference-only decisions have null resolved-spec hashes; the library can optionally resolve against a real bundle subject to compatibility and explicit low-fit approximation gates. Extensive design replacement or incompatible requirements route to `developer/new-preset`; soft visual mismatches are explicitly labeled approximations. Raw PNG bytes, metadata and local image paths do not enter decisions.

Run the focused live acceptance with `scripts/validate_developer.py`, supplying the same public bundle/runtime arguments and a new output directory. It executes nine previews and six independent scene reopenings. It proves a temporary **Python aperture-scaling change with input and JSON fixed**, then the equivalent **JSON aperture change with Python fixed**, and a separate focal-distance change/default. Camera/geometry hashes must stay identical, actual camera values must agree, equivalent Python/JSON effects must produce matching images, and closing the aperture must restore image edge detail. It also proves fresh source identity, persisted-preset restart/reuse, a JSON palette-only variation, two reference-decision routes, and retained pastel/green PNG-only behavior. Synthetic contract tests complement these real renders. See the [progress record](plans/blender-render-progress.md) for executed results and limitations.

Public 1SPS, same geometry, camera and 82 Å focal plane. These are 640 × 480, 128-sample validation renders; the low-aperture image is intentionally selective. [Acceptance evidence](../examples/developer/acceptance.json) records both the Python-change and JSON-control comparisons.

| f/0.5, selective focus | f/8, deeper focus |
|---|---|
| ![Selective focus](../examples/developer/focus.png) | ![Deeper focus](../examples/developer/deep_focus.png) |

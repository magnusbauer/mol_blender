# D1: shared preset selection and compact catalog

D1 is a library contract for selecting among the fourteen installed built-ins.
It performs no rendering, admission, allocation, network access or image analysis.
The resolver, renderer, MCP adapters and execution profiles retain their existing
semantics. Preset 1 remains the default. Automatic selection does not enumerate
user/project storage; its existing persistence and `resolve_authored()` remain
available, with string revisions at public boundaries and integer revisions on disk.

## API

```python
from blender_render.engine import CAPABILITIES
from blender_render.selection import SelectionPolicy, SelectionRequest, select_preset

request = SelectionRequest(
    preset="green_paint",                    # optional canonical ID, alias or qualified ID
    revision="1",                            # optional; requires an explicit preset
    hard_requirements={"content": ["protein"], "transparent_background": True},
    visual_brief={"material_family": "dried_paint", "representations": ["cartoon"]},
    overrides={"geometry": {"secondary_structure_palette": {"sheet": "#D7A9E3"}}},
)
decision = select_preset(request, available_capabilities=CAPABILITIES,
                         policy=SelectionPolicy())
print(decision.to_json())
```

`CAPABILITIES` is the installed engine's declaration, not an observation of a
worker. Other callers must supply their own capability declaration. Passing a
validated `bundle=...` allows exact-context resolution; it does not execute it.
Do not pass an empty synthetic object to obtain an executable hash.

All records are validated dataclasses. `SelectionRequest` accepts nested
`HardRequirements` and `VisualBrief` records or dictionaries. Unknown fields,
wrong types, nonfinite/non-JSON overrides and invalid resolver fields fail.
Inputs are copied defensively; `to_dict()` returns a fresh JSON-compatible copy
and `to_json()` sorts keys without whitespace. Revisions accept positive integers
or their canonical decimal strings and are exposed as strings. Empty, boolean,
fractional and noncanonical revisions are rejected.

| Record | Fields |
| --- | --- |
| `SelectionRequest` | `preset`, `revision`, `hard_requirements`, `visual_brief`, `overrides`, `allow_low_fit_approximation=False` |
| `HardRequirements` | String collections `content`, `representations`, `required_capabilities`; optional booleans `transparent_background`, `save_blend`; positive integers `width`, `height`; `camera_policy` (`preserve_input` or `authored`) |
| `VisualBrief` | String collections `representations`, `palette`; optional text `material_family`, `background`, `lighting`, `edges`, `composition`, `projection`, `ligand_treatment` |
| `SelectionPolicy` | Optional collections `allowed_presets`, `allowed_override_sections`, `allowed_capabilities`; `public=False`, `allow_scene=True`; optional positive `max_width`, `max_height`, `max_samples` |
| `PresetDecision` | `format_version=1`, `catalog_digest`, qualified `preset_id`, string `preset_revision`, `fit`, `reasons`, `overrides`, `unmatched_features`, `unsupported_requirements`, `new_preset_recommended`, `route`, `resolved_spec_sha256`, `hard_requirements_satisfied`, `execution_compatible`, `approximation_authorized` |

`overrides` records caller-supplied JSON edits. Keep the request's hard requirements
alongside the decision: those explicit requirements become resolver convenience
options, with the resolver's existing precedence. The decision is not a new render
request or a transport document. D2 will handle consumer transport integration.

## Deterministic matching and fit

An explicit preset is never substituted. Unknown IDs and revision mismatches
raise `SelectionError`. Canonical names, existing aliases and `builtin/` plus a
canonical name resolve through the existing ID resolver. An empty policy allowlist,
or an explicit name excluded by it, raises `SelectionError`. No new shared-host
allowlist exists. Policy-permitted bases whose configurations cannot meet hard
requirements, capability declarations or configuration restrictions still produce
an inspectable low-fit recommendation.

Automatic candidates first have their overrides validated through
`spec.resolve_options()`. An override must name a used material; an unused name
is an error, not a low-fit edit. Candidates for which those edits are invalid are
inapplicable. If none remain, raise a validation error. Compatible candidates rank
before incompatible recommendations. Rank the remaining candidates lexicographically:

1. Fewer missing content and representation requirements, including visual representations.
2. Material-family match, then edge and ligand-treatment match.
3. Fewer unmatched palette, background and lighting properties.
4. Fewer unmatched projection and composition properties.
5. Fewer effective edits, ignoring values already equal to the base.
6. Default preset first, then qualified ID alphabetically.

There is no weighted aggregate score. Catalog enumeration order and set-like
metadata order do not affect the decision. Supported content describes shipped
selection coverage (`protein`, `nucleic`, `ligand`, `ion`, `water`), not observed
atoms in a bundle or a promise that every atom will be selected. Within each
molecular selection, conditions intersect (AND); separate rendered style entries
contribute their coverage by union (OR). `polymer` can include protein and nucleic
content, so `polymer=true, nucleic=true` establishes only nucleic coverage.
False flags add no filter, matching the renderer. Replacement selections recognize
these flags and the exact existing queries `polymer`, `nucleic`, `ligand`, and
`polymer and not nucleic` (protein narrowing; there is no schema `protein` flag).
Unknown queries and additional chain/residue/proximity filters cannot establish
hard coverage without atom inspection. No general query parser is introduced.

- **High:** hard requirements and execution compatibility pass, representation
  and material language match, all requested visual descriptors match after edits,
  and edits stay small.
- **Medium:** execution compatibility passes but soft descriptors are unmatched.
  Reasons explicitly label this an approximation; no new preset is required.
- **Low:** hard/configuration/capability incompatibility, representation or material
  mismatch, or extensive design replacement. Always recommends a new preset and
  identifies the closest base and unmatched features.

The small-edit boundary is structural. Output/camera controls, palettes/background,
existing lighting parameters and edits to at most two used materials can be small
while representations and material models are unchanged and required capabilities
stay within the authored base's declaration. Selecting its existing authored camera
therefore stays small even when default saved-view resolution omits those controls.
Replacing representations/selections, adding/removing material models or required
capabilities, changing the light-type sequence, replacing more than two materials,
or other geometry changes produces low fit. Lists replace in the resolver; there
is no list-index merge. Dead fields (including `anchor_model_center`) still fail;
transport-owned output filenames/format cannot be selection overrides. Explicit
camera controls must have a consumer in their selected mode (including direction,
intrinsics and fit controls); background colors must match the solid/gradient mode.
These checks reject inactive edits without changing inherited resolver defaults. Rough-mode
and all existing resolver behavior remain unchanged.

Explicit material controls must be active under the selected renderer material
model. The shared `presets.active_material_controls()` view follows the existing
paint dispatch: nested `paint` controls are consumed, while shader siblings such
as `base_color` are inactive and newly supplied edits raise `StyleValidationError`.
`glow` is read independently by the existing compositor and retains its schema
requirements. Legacy preset documents are not rewritten; inactive fields are
excluded from material-model and palette descriptors.
For non-paint materials, attribute/gradient pigment replaces the base-color
input. That field remains active only where a shadow/highlight/emission fallback,
enabled color following or the existing chain-palette resolver consumes it.
AO/rim auxiliary controls require the clay branch; emission color requires an
emission or edge-highlight consumer. The shared helper checks these same conditions
for validation and descriptors. Chain-anchor dependency checks use copies and the
existing pure palette resolver; they never assign colors to a render request.
With a supplied bundle, color following counts as a base-color consumer only
when applicable atom, chain or class hints are present and the mix retains that
color. Top-level bundle hints take precedence over view hints, as in existing
payload materialization. Known-absent or inapplicable hints cannot activate an
otherwise disconnected edit or contribute its palette descriptor. Reference-only
and static catalog descriptors retain configured tendencies, without a hash.

## Descriptor vocabulary

D1 strips outer whitespace and lowercases text; hex colors become uppercase.
Set-like collections are sorted and deduplicated. Unknown descriptor text is
unmatched, never parsed into assignments and never echoed in generated reasons.
Feature diagnostics use field labels or validated capability identifiers.

Catalog tendencies come from existing JSON: representation names; material-family
metadata (`clay`, `frosted`, `milky`, `dried_paint`); hex palette values in used
materials/geometry; `solid`, `gradient`, hex solid backgrounds or `transparent`;
light types such as `area`; `smooth`, `mixed`, `rounded` edges; `structure`,
`focus_style`, `depth_of_field` composition; and `sticks` ligand treatment.
Palette tendencies are authored values, not a claim of observed pixel colors or
actual PyMOL color assignments. Composition is assessed from the resolved camera;
the catalog's authored composition may differ from its default saved-camera policy.
The nested paint `dark_paint_tint` is represented exactly as a tagged linear RGB
descriptor, for example `linear_rgb:[0.2,0.3,0.4]`, using float components and compact
JSON. This tint is a shader multiplier, not a predicted pixel color or a hex palette
assignment. Supported paint tint and geometry palette edits remain small edits.

Projection matches explicit `ORTHO`/`PERSP` camera JSON or a supplied boolean input
`orthoscopic` value. Without either, a projection observation remains unmatched;
no camera assignment is generated. Identifier collections require canonical lower-case
tokens.

The following exact aliases retain the recorded developer observations:

| Field | Alias | Normalized descriptor |
| --- | --- | --- |
| material_family | `dried paint` | `dried_paint` |
| projection | `orthographic`, `ortho`; `perspective`, `persp` | `ORTHO`; `PERSP` |
| background | `black`, `white`, `Pale blue gray` | `#000000`, `#FFFFFF`, `#C6D6DF` |
| lighting | `Soft highlights`, `soft` | `area` |
| edges | `Smooth ribbons and round sticks` | `smooth` |
| edges | `rounded ribbons` | `rounded` |
| composition | `One compact protein and a highlighted ligand` | `structure` |
| composition | `Selective focus within a compact protein; other depths are soft` | `depth_of_field` |
| ligand_treatment | `Pink sticks`, `native sticks` | `sticks` |

The pink-sticks alias describes representation only; color remains an explicit
palette observation/edit. A focus observation does not invent a focus distance,
camera, or shader. No natural-language parser or automatic visual assignments exist.

## Compatibility and exact-context hashes

`hard_requirements_satisfied` means the proposed configuration meets the structured
hard requirements. `execution_compatible` additionally checks supplied capabilities
and selection policy. Neither field claims admission, authorization, live worker
availability, a validated runtime, or successful execution.
Both hard camera policies are checked against the effective resolved camera after
normal resolver precedence. An explicit camera override is retained even when it
defeats a hard requirement; the resulting decision is low fit and incompatible.
Input pose with modified intrinsics, framing or scale cannot establish either
complete input preservation or an authored camera. Mode-inactive legacy camera
fields do not determine the effective policy.

Without a real bundle, `resolved_spec_sha256` is **null**, including high fit.
With a valid supplied bundle, high/medium decisions can expose exactly
`resolve_options(...)["render_spec"]["sha256"]`. D1 validates the supplied bundle
with the existing payload validator and delegates exact camera, viewport and output
resolution to that resolver. The hash covers the **RenderSpec JSON**, not bundle
identity: saved pose/FOV changes that leave this JSON unchanged need not change
this hash. Admission separately binds the actual bundle and immutable spec.

Low fit defaults to `route="developer/new-preset"` and a null hash even when a
bundle is supplied. `allow_low_fit_approximation=True` records explicit permission
in `approximation_authorized`. Only when hard requirements and execution compatibility
both pass can a low-fit decision use `preset-overrides` and, with a bundle, expose
a hash. It remains low fit with `new_preset_recommended=True`. Permission cannot
bypass missing capabilities, hard requirements, or policy restrictions.
One shared final gate controls routing and hash eligibility. Execution compatibility
and satisfied hard requirements are necessary; low visual fit additionally needs
explicit approximation authorization. Hard-failure low fit always has a null hash,
regardless of authorization. Invalid or inactive edits raise validation errors
before a decision can be returned. Reference-only decisions always have null hashes.

## Catalog and developer delegation

`presets.catalog()` retains its list shape and prior IDs/fields, adding content,
palette/background/lighting/edge/composition tendencies, default camera policy,
authored-camera tendencies, style digest, shared schema references for controls,
and `shared_host_status="eligible_under_current_policy"` for all fourteen built-ins.
This is static eligibility under the unchanged resolver policy, not availability
or a separate approval/admission list. Caller policy can be stricter.

`presets.compact_catalog()` returns `format_version`, `schema_digest`, compact
`entries` and `digest`. Its digest hashes canonical JSON containing the first three
fields, with entries sorted by qualified ID and set-like metadata normalized.
Style digests hash authored style JSON; the schema digest hashes parsed schema JSON.
The digest excludes timestamps, filesystem locations, runtime observations and
engine-build identity. Schemas are referenced once by category, not duplicated.

`authoring.reference_brief(image, observations, *, bundle=None,
allow_low_fit_approximation=False)` retains its `visual_brief`/`preset_decision`
envelope and original observable descriptors. It validates one PNG byte snapshot,
serializes reference SHA-256 and dimensions only, and delegates to `select_preset`
with `engine.CAPABILITIES` explicitly. There is no separate ranking/fit algorithm.
PNG metadata, paths, URLs and pixels are absent from reference provenance and
decisions. Unsupported observation fields fail; caller notes are observations,
not generated reasons. Reference-only decisions have no resolved hash.

## Consumer and acceptance boundaries

The render skill lists concise descriptions/aliases for known built-ins. It asks
for more catalog information only for unknown names, genuine ambiguity, known
catalog changes or broader reference comparison. Current MCP `list_presets` returns
identifiers only. Current MCP options have no decision, revision, override or
catalog-digest fields. Quota/admission remains authoritative at submission; quota
queries are for user questions or explaining a rejection. This paragraph records
the D1 boundary; D2's structured MCP request, compact catalog and recovery tools
are specified in [MCP consumer transport](mcp-consumer.md).

The offline runner `scripts/validate_c3_contracts.py` now includes D1, specification,
preset, schema and developer contracts in its outside-checkout wheel installation.
All application test/probe interpreters load `c3_boundary_guard` before imports.
Four explicit runner probes compare catalog and decisions across enumeration order,
hash seeds, source/staged/installed locations and working directories. Import origins
and recursive source/wheel/install byte equality remain mandatory. Dependencies
are reused from the prepared interpreter and never fetched.

D1 ends with library/installed-package evidence, not installed-host or backend
acceptance. MCP decision transport and compact responses remain D2. PyMOL wiring,
installation routing, Mac/HPC/Modal runtime checks, ChatGPT/Codex UI checks,
deployment and publisher review remain outside this change.

# Asset-fidelity reconstruction path

The reconstruction target is the **assets themselves**: complete model geometry, hierarchy, local/scene transforms, source UVs, projected UVs, texture placement/tiling, materials and source images. Historical floor/lighting/Mental Ray pixel matching is secondary unless it exposes an asset-format field needed by extraction.

## Geometry, hierarchy and transforms

The current production stack already reconstructs these as source data:

- every DSC `MODELS ... ROOT` HRC is assembled rather than assuming one master model;
- HRC preorder hierarchy and local S/R/T are retained;
- DSC relation code 110 validates the reconstructed model parent graph;
- DSC `ENVIRONMENT` SRT is used for root scene instances when authored;
- class-4 polygon meshes preserve per-corner normals/UVs and material-slot metadata;
- reconstruction-ready NURBS curves/surfaces are tessellated into the same hierarchy, with parameter UVs clearly distinguished from source texture projection.

## UV policy

### Authored polygon UVs

Class-4 HRC UVs are emitted verbatim as glTF `TEXCOORD_0`. UV provenance records whether a primitive contains meaningful source coordinates or an all-zero projection-dependent set.

### Projection-driven UVs

Generated Blender UVs are **additive named maps**. They never overwrite the imported source UV map.

The current practical generator supports a working table developed against the nested high-resolution walker/tank validation set:

```text
1  Planar XY candidate
2  Planar XZ candidate
3  Planar YZ candidate
4  Spherical anchor/candidate
5  high-resolution-subset Cylindrical candidate
```

Generated coordinates apply:

```text
projection/operator
-> URepeat / VRepeat          TXMP +2 / +4
-> U/V scale + offset         TXMP +6
-> source-pixel crop          TXMP +60..+66
```

Angular maps receive polygon-local seam correction.

### Important scope correction

The high-resolution nested set is not the whole art archive.

Nested high-resolution validation:

```text
code-400 edges     403
+24 values         1..5
identity +90       403/403
```

Full outer `bz2_art.7z` census:

```text
TXMP records        14,486
DSC scenes           1,139
code-400 edges         283
code-401 edges       9,985
```

Full-archive model-local code 400 uses:

```text
1: 18   2: 11   3: 5   4: 172   5: 9   6: 65   8: 3
```

Only 229/283 of those have effectively identity `+90`; 54 carry meaningful matrix state. Therefore the 1..5 identity generator is a useful **validated subset path**, not a universal BZ2 projection decoder. Codes 6/8 and nonidentity code-400 state are intentionally deferred rather than guessed.

See `artifacts/validation/txmp_full_archive_projection_summary.json`.

### NURBS parameter UVs

NURBS tessellation currently emits normalized surface parameter coordinates. They are useful topology coordinates but are not labeled as recovered Softimage texture projection until textured NURBS binding is reconstructed.

## Texture repeats and tiling

TXMP `+2/+4` are recovered as `SI_Texture2D` `URepeat/VRepeat` factors. This is a major asset-fidelity field rather than an edge case.

In the nested high-resolution relation-aware set:

```text
code 400   403 edges   304 non-unit repeat
code 401   707 edges    53 non-unit repeat
```

Representative source correlations include:

```text
bump1                  20 x 20
cementwall              4 x 1
hazardfloor / ceiling   1 x 4
arch ac00sa0            6 x 6
stripes                 2 x 1
rusty cylindrical       2 x 2
chrome support          3 x 1
```

The full outer archive contains an even broader repeat range, reinforcing that `+2/+4` are authored tiling state. The renderer-independent UV generator applies repeats before `+6`, and portable base textures compose repeat + scale/offset + crop through `KHR_texture_transform`.

See `artifacts/validation/txmp_repeat_summary.json`.

## Shared TXMP decoding

Model-local code-400 and material code-401 records now use one common parser. It preserves:

- `URepeat/VRepeat` at `+2/+4`;
- `UScale/VScale/UOffset/VOffset` at `+6`;
- raw projection/operator code at `+24`;
- the full aligned eight-float raw block at `+26..+57`;
- crop at `+60..+66`;
- raw auxiliary words at `+76/+78/+80/+82/+84`;
- aligned layer/mode words at `+86/+88`;
- texture-matrix S/R/T at `+90`.

The older model-local parser exposed `scope_u32_be/scope_u16_be` over bytes now known to overlap repeat fields. That redundant decoder has been removed so code 400 and 401 cannot drift onto different TXMP layouts.

## Corrected layer/mode words at +86/+88

The former odd-offset `u16le +87/+89` interpretation was a byte-alignment accident. The actual fields are aligned big-endian words at `+86/+88`.

Across 664 nested archival records:

```text
+86  +88   records   current corpus role
 1    2        8     bump candidate
 2    1       54     alpha-overlay candidate
 2    2      184     base/default candidate
 3    2      418     base/default candidate
```

The exact historic enum/property names remain unpromoted; only the aligned binary boundaries and corpus roles are used.

## `+90` matrix frontier

The high-resolution nested code-401 subset initially had 133 byte-nonzero matrix records. Four are merely ~`1e-7..1e-6` radian decomposition residue, so a `1e-5` identity tolerance leaves:

```text
effective identity     578 / 707
meaningful nonidentity 129 / 707
```

Those 129 are rotation-only: no meaningful matrix scale and no matrix translation.

The full outer archive is substantially broader:

```text
code 400   identity 229   meaningful nonidentity 54
code 401   identity 8101  meaningful nonidentity 1884
```

So the matrix problem remains important for **full archive extraction**, even though the high-resolution showcase subset was unusually friendly.

Raw HRC UVs cannot by themselves determine the `+90` direction/order: Softimage keeps raw projection coordinates separate from projection-definition transformation until the transform is frozen/baked. Exact UVW rotation application therefore needs authoritative source behavior, a frozen-before/after reference pair, or another valid transformed-coordinate anchor.

See `artifacts/validation/txmp_matrix_frontier_summary.json` and `txmp_full_archive_projection_summary.json`.

## `+24` codes and auxiliary flags

The production field remains deliberately named:

```text
projection_or_mapping_code_candidate
```

The full archive invalidated an earlier nested-subset inference: `+78=1` is **not** exclusive to code 5 and cannot prove `5 = Cylindrical`. In the outer archive `+78=1` occurs with multiple codes, including substantial code-6 and code-4 usage.

Therefore:

- the former global `+78 -> code5/Cylindrical` argument is retracted;
- `+76/+78/+80` remain raw auxiliary fields;
- projection-code labels are applied only within scopes that have independent support.

## Higher modes 7 and 8

The outer archive sharpens these modes considerably:

- code `7`: 93/93 resolved material code-401 edges use `reflection3`; this is strong reflection/environment-associated evidence;
- code `8`: outer code-401 uses `reflection3`, `reflection` and `backgr`; code-400 also contains three code-8 specular-map relationships.

The nested walker subset's code-8 `cavern`/glass association was therefore a subset-specific application, not a universal code-8 definition.

Blender preserves these states explicitly rather than forcing them through the ordinary 1..5 geometric UV generator.

## Model-local textures versus material layers

DSC relation scopes remain distinct:

- **400:** model-local texture/projection state;
- **401:** ordered material texture layers.

In the nested high-resolution scenes, 266 models have both code-400 and material code-401 texture relationships, but their target texture objects do not overlap. Common combinations include `rusty` model-local state with `stripes`/`blueglow` material layers. This supports reconstructing a composited material rather than treating the scopes as duplicate references to one texture.

Current Blender behavior preserves both scopes and avoids inventing the final cross-scope blend order. The next practical step is to reconstruct that composition using source order, alpha behavior and material role evidence.

## Current priorities

1. broaden the practical UV generator beyond the nested high-resolution subset without collapsing full-archive codes 6/8 into guessed labels;
2. recover the exact `+90` UVW rotation application for meaningful code-400/code-401 records;
3. reconstruct code-400 + ordered code-401 material composition, including alpha overlays and bump-map routing;
4. recover `UAlternate/VAlternate` and any non-default `UVSwap`/wrapping behavior from source-correlated anchors;
5. map special environment/reflection modes 7/8 to appropriate Blender constructs only when semantics are sufficiently anchored;
6. bind textured NURBS surfaces to their actual projection state.

Historical lighting, floor reflections, FxDirector rendering, lens shaders and Mental Ray equivalence remain secondary unless they directly unblock one of those asset goals.

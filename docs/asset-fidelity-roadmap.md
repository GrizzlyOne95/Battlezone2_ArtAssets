# Asset-fidelity reconstruction path

The reconstruction target is now the **assets themselves**: complete model geometry, hierarchy, local/scene transforms, source UVs, projected UVs, texture placement/tiling, materials and source images. Pixel-matching historical floors, lights, lens effects and Mental Ray output is useful validation evidence but is not a gate for ordinary asset extraction.

## Geometry, hierarchy and transforms

The existing reconstruction stack already treats these as production data rather than render approximations:

- every DSC `MODELS ... ROOT` HRC is assembled, rather than assuming one master model;
- the HRC preorder hierarchy and local S/R/T are retained;
- DSC `MODELS -> MODELS` relation code 110 is used to validate the reconstructed parent graph;
- DSC `ENVIRONMENT` SRT overrides the ROOT scene-instance transform when present;
- class-4 polygon meshes retain source polygon-corner normals/UVs and material-slot metadata;
- reconstruction-ready NURBS curves/surfaces are tessellated into the same hierarchy, with parameter-space UVs clearly distinguished from source texture projection.

## UV policy

There are now three deliberately separate UV cases.

### 1. Authored polygon UVs

Class-4 HRC stores UVs per polygon corner. These values continue to be emitted verbatim as glTF `TEXCOORD_0`. The UV-provenance stage records whether each primitive contains meaningful source UVs or an all-zero coordinate set.

### 2. Projection-driven polygon UVs

High-resolution Softimage assets frequently contain all-zero polygon UVs because the texture placement was authored as a texture projection instead of a frozen UV unwrap.

For these assets, Blender creates **additional named UV maps** instead of overwriting the imported source UV layer. The working projection table is:

```text
1  Planar XY
2  Planar XZ
3  Planar YZ
4  Spherical
5  Cylindrical
```

The supplied relation-code-400 corpus contains 403 resolved model-local projections. All 403 use only these five codes and all 403 have identity `+90` matrix SRT. This means the model-local asset path can move forward without making the unresolved non-identity matrix direction a blocker.

Generated UVs now apply, in order:

```text
projection/operator coordinates
-> URepeat / VRepeat          TXMP +2 / +4
-> U/V scale + offset         TXMP +6
-> source-pixel crop          TXMP +60..+66
```

Angular maps receive polygon-local seam correction so interpolation does not cross the long way around the U seam. The source UV layer remains intact for provenance and comparison.

### 3. NURBS parameter UVs

The current NURBS tessellator emits normalized surface parameter coordinates. Those are useful topology coordinates but are **not** presented as recovered Softimage texture projection. They remain explicitly labeled as parameter-space UVs until textured NURBS binding is reconstructed.

## Texture repeats and tiling

TXMP `+2` and `+4` are now recovered as the legacy `SI_Texture2D` `URepeat` and `VRepeat` factors. This is not a cosmetic edge case: **304 of the 403 resolved model-local code-400 projections use non-unit repeat values**.

The 664-record generic archival corpus contains:

```text
URepeat,VRepeat   records
1,1                  495
2,2                  118
1,4                   16
3,1                   13
20,20                  8
2,1                    8
4,1                    4
6,6                    2
```

The image/use correlations are strong and semantically coherent:

```text
bump1                  20 x 20
cementwall              4 x 1
hazardfloor / ceiling   1 x 4
arch ac00sa0            6 x 6
stripes                 2 x 1
rusty cylindrical       2 x 2
chrome support          3 x 1
```

The relation-aware scene corpus contains:

```text
code 400: 403 edges, 304 non-unit repeat
code 401: 707 edges,  53 non-unit repeat
```

Repeat is therefore no longer in the unresolved bucket. The renderer-independent projected-UV generator applies it before the confirmed `+6` placement, and portable base textures compose repeat + scale/offset + crop through `KHR_texture_transform`.

Derived validation is retained in `artifacts/validation/txmp_repeat_summary.json`.

## Other texture placement state

Material-level DSC code-401 texture records decode the same common TXMP state as model-local code-400 records:

- `URepeat`, `VRepeat` at `+2/+4`;
- `UScale`, `VScale`, `UOffset`, `VOffset` at `+6`;
- raw projection/operator code at `+24`;
- aligned raw eight-float block at `+26..+57`;
- crop rectangle at `+60..+66`;
- raw auxiliary words at `+76/+78/+80/+82/+84`;
- aligned raw layer/mode words at `+86/+88`;
- texture-matrix R/S/T at `+90`.

The full `+26..+57` block is intentionally preserved even though most of its semantic names are not yet proven. This prevents future alternate/wrap/effect work from needing another destructive reparse of the source archive.

For Blender, supported effective-identity-matrix projection layers receive dedicated generated UV maps. If a material-level layer has an unresolved projection code or meaningful `+90` matrix rotation, the pipeline does not promote a guessed projection. Confirmed image-space state remains available in the sidecar and fallback material path while transform direction is solved.

## Code-401 +90 matrix frontier

The matrix problem is narrower than the earlier raw count suggested. Of the 707 resolved material-level code-401 texture edges:

```text
byte-nonzero +90 records                    133
numerical decomposition residue only          4
effective identity at 1e-5 tolerance        578
meaningful non-identity                      129
meaningful non-unit +90 scale                  0
meaningful non-zero +90 translation            0
```

All 129 meaningful records are therefore **rotation-only**. Their projection-code distribution is:

```text
code 1   10
code 2   97
code 3    3
code 4   19
```

The four noise-only records contain only roughly `1e-7..1e-6` radian residue. The working identity test now uses `1e-5`, well below the smallest clearly authored rotation in the same corpus (about `0.00646` radians), so those four layers no longer get needlessly deferred.

One tempting validation route has also been explicitly rejected: comparing the raw HRC polygon UVs directly with a candidate `+90` transform. Softimage keeps projection-definition S/R/T separate from the raw projection cluster; changing the projection transform does **not** change the UV coordinates shown in the Texture Editor until the transform is frozen. Its `Texture.GetTransformValues` API computes the fully transformed UVW values from the raw projection plus transformation/effect state. Consequently, raw HRC UVs are useful source coordinates, but they cannot by themselves tell us whether the `+90` matrix should be applied direct/inverse or in what exact UVW order.

The next matrix target is therefore only the **exact UVW rotation application/direction** for those 129 records. We do not need to solve matrix scaling and translation simultaneously for the current code-401 corpus.

Derived evidence is retained in `artifacts/validation/txmp_matrix_frontier_summary.json`.

## Aligned layer/mode words at +86/+88

The `+86/+88` correction closes a real parser-layout defect. Earlier sidecars read little-endian `u16` values beginning at odd offsets `+87/+89`. A 664-record archival pass shows the actual fields are aligned big-endian words at `+86/+88`. The old `+87` read happened to reproduce `+86` in all 664 records, but the old `+89` read matched `+88` in only 610 because it consumed the first byte of the `+90` matrix on rotated textures. The existing coarse base/overlay/bump classification happened to remain the same in all 664 records, but the incorrect byte boundaries are no longer used.

The aligned pair distribution is:

```text
+86  +88   records   current corpus role
 1    2        8     bump candidate
 2    1       54     alpha-overlay candidate
 2    2      184     base/default candidate
 3    2      418     base/default candidate
```

These remain **corpus roles**, not invented legacy enum labels. The exact original semantic names for `+86/+88` are still deliberately unpromoted.

## Special material texture modes 7 and 8

The relation-aware archive pass narrows the remaining `+24` values substantially:

```text
relation 400: codes 1..5 only
relation 401: codes 1,2,3,4,7,8
```

Among resolved scene relationships:

- code `7`: 14 material-level edges, all relation 401; 11 use `chrome3`, two use `reflection3`, and one uses `reflection2`;
- code `8`: 21 material-level edges, all relation 401; all 21 use `cavern` on the recovered walker glass materials;
- every linked code-7/code-8 record has identity `+6` and identity `+90` state.

That makes 7/8 qualitatively different from the ordinary geometry projections used by code 400. They are treated as **special material texture modes**, not forced through the planar/spherical/cylindrical UV generator. Code 7 is strongly reflection/environment-associated and code 8 is strongly glass/environment-associated in this corpus, but those are intentionally descriptive candidates rather than hard-coded historical enum names.

Derived evidence is retained in `artifacts/validation/txmp_layer_mode_summary.json`.

## Model-local textures versus material layers

DSC relation code 400 and code 401 are intentionally kept separate:

- **code 400:** model-local projected texture state;
- **code 401:** ordered material texture layers.

The supplied scenes often use both on the same model, and the texture objects are commonly different. Blender therefore exposes mapped model-local texture nodes per object and keeps the existing ordered material-layer stack.

A model-local base texture is automatically connected only when the material does not already have a Base Color texture stack. When both model-local and material-level textures contribute, both are preserved and mapped, but their final cross-scope blend order is not invented until that relationship is better proven.

## Current priorities

Work should proceed in this order:

1. validate the repeat-aware projected UVs across ordinary walker/tank/mechanical assets;
2. recover the exact UVW rotation application/direction for the 129 meaningful code-401 `+90` records;
3. reconstruct the model-local/material-layer composition rules;
4. recover `UAlternate`/`VAlternate` and any non-default `UVSwap`/wrapping behavior from source-correlated anchors;
5. give special material modes 7/8 Blender equivalents only when their historical semantics are sufficiently anchored;
6. bind textured NURBS surfaces to their actual projection state.

Historical lighting, floor reflection matching, FxDirector rendering, lens shaders and Mental Ray pixel equivalence remain secondary unless they expose an asset-format field needed by one of the items above.

# TXMP projection and texture-placement reversal

This note records the current state of the Battlezone 2 Softimage texture/projection reversal. The extraction goal is a usable asset: geometry, hierarchy, transforms, source/projected UVs, tiling, image placement and material layers. Historical renderer matching is useful evidence but is not a prerequisite for ordinary asset reconstruction.

The original Softimage assets and renders used for validation are **not committed to this repository**. Decoder code and derived statistics are retained instead.

## Confirmed image-space placement

### Repeat factors: `+2/+4`

The big-endian `u16` values at post-path TXMP offsets `+2` and `+4` are recovered as legacy `SI_Texture2D` `URepeat` and `VRepeat`:

```text
+2   URepeat
+4   VRepeat
```

The public dotXSI field order and the source corpus agree with this assignment. Representative authored values include:

```text
bump1                  20 x 20
cementwall              4 x 1
hazardfloor / ceiling   1 x 4
arch ac00sa0            6 x 6
stripes                 2 x 1
rusty cylindrical       2 x 2
chrome support          3 x 1
```

Repeat is therefore part of the production UV path, not an unresolved flag. See `artifacts/validation/txmp_repeat_summary.json`.

### Scale and offset: `+6`

Four big-endian floats beginning at `+6` are source-correlated to:

```text
+6    UScale
+10   VScale
+14   UOffset
+18   VOffset
```

The production decoder exposes:

```text
si_texture2d_uv_scale
si_texture2d_uv_offset
```

The practical image-space chain is:

```text
projection/operator coordinates
    -> URepeat / VRepeat          (+2/+4)
    -> U/V scale + offset         (+6)
    -> source-pixel crop          (+60..+66)
```

### Crop rectangle: `+60..+66`

The crop rectangle is four big-endian source-pixel coordinates:

```text
+60   x0
+62   x1
+64   y0
+66   y1
```

Two independent image-size anchors validate full-image rectangles:

```text
glare.pic   64 x 64     0..63,  0..63
RUSTY.PIC  483 x 363    0..482, 0..362
```

The words at `+68/+70/+72` duplicate `x1/y0/y1`; they are not repeat counts. The old `crop_repeat_raw` name is retained only as a deprecated compatibility alias.

## Confirmed texture-matrix S/R/T: `+90`

The nine-float big-endian block beginning at `+90` is the compact `SI_Texture2D` texture/projection matrix state:

```text
+90   rotation X   radians
+94   rotation Y   radians
+98   rotation Z   radians
+102  scale X
+106  scale Y
+110  scale Z
+114  translation X
+118  translation Y
+122  translation Z
```

It is **not** an HRC/model transform. A readable source matrix with diagonal `(-1, 1, -1)` corresponds to binary rotation `(0, -pi, 0)`, scale `(1,1,1)`, translation `(0,0,0)`, independently confirming both field role and radian units.

Across the complete supplied package, including the nested high-resolution archive:

```text
TXMP records inspected       15,150
non-zero rotations            3,361
non-unit matrix scale             1
non-zero matrix translation       0
```

For relation-aware material code-401 state, the meaningful unresolved frontier is overwhelmingly rotation. The extraction path uses a `1e-5` matrix-identity tolerance to ignore four sub-microradian/microradian decomposition residues while retaining clearly authored rotations. See `artifacts/validation/txmp_matrix_frontier_summary.json`.

## Readable `glare.pic` source anchor

The surviving readable `ivstas00.xsi` `SI_Texture2D` block provides a useful field-order anchor:

```text
image size       64 x 64
crop             0..63, 0..63
UVSwap           0
URepeat/VRepeat  1, 1
UAlternate/VAlternate 0, 0
UScale/VScale    1, 1
UOffset/VOffset  0, 0
mappingType      1
matrix           identity
```

The matching TXMP carries unit repeat, identity `+6`, the same crop and identity `+90`.

The readable block also demonstrates that Softimage stores additional texture-layer/shader state separately from the projection-definition matrix. The production parser therefore preserves unresolved scalar/auxiliary fields rather than assigning names by proximity.

## Raw `+26..+57` scalar block

TXMP `+26..+57` is an aligned block of eight big-endian floats. The parser preserves all eight values:

```text
field_f32_be_26
field_f32_be_30
field_f32_be_34
field_f32_be_38
field_f32_be_42
field_f32_be_46
field_f32_be_50
field_f32_be_54
```

A readable `glare.pic` source block and matching binary record strongly suggest this area contains material/texture shader scalars such as ambient/diffuse/specular/blending/effect values, but the exact binary-to-source ordering is not yet fully promoted. Preservation is intentional so later field naming does not require another source-archive pass.

## Corrected layer/mode words: `+86/+88`

The earlier material-layer probe read little-endian words beginning at odd offsets `+87/+89`. A 664-record check shows the actual aligned boundaries are:

```text
+86  big-endian u16
+88  big-endian u16
```

The old `+87` read happened to reproduce aligned `+86` in 664/664 records. The old `+89` read matched `+88` in only 610/664 because it consumes the first byte of the `+90` matrix when that byte is nonzero. The coarse layer-role classification happened to remain unchanged, but the old field boundaries were invalid.

The production parser now uses `field_u16_be_86` / `field_u16_be_88` and retains the historical names only as deprecated aliases.

## `+24`: projection/operator state, but not one universal enum table

The `u16` at post-path offset `+24` is strongly projection/operator-related. It is **not** the readable dotXSI `SI_Texture2D.mappingType` field: source `mappingType=3` means explicit UVs, while BZ2 `+24=3` occurs on projection-dependent meshes with all-zero polygon UVs.

The crucial correction is that the earlier 1..5 interpretation was derived from a **specific nested high-resolution validation subset**, not the entire BZ2 source tree.

### Nested high-resolution validation subset

The nested `Archival.zip` scenes used for the walker/tank reversal contain:

```text
403 resolved model -> TEXTURES2D code-400 edges
+24 values: 1,2,3,4,5 only
+90 identity: 403/403
```

That subset produced a useful working table for practical high-resolution reconstruction:

```text
1   Planar XY candidate
2   Planar XZ candidate
3   Planar YZ candidate
4   Spherical anchor/candidate
5   Cylindrical candidate in this subset
```

Code `2` has particularly strong high-resolution evidence: the walker floor is an exact XZ grid and carries `+24=2`.

### Full outer `bz2_art.7z` census

A separate full-source census extracted all outer `modelsdirectory/TEXTURES2D/*.txt` and `SCENES/*.dsc` records from `bz2_art.7z`:

```text
TXMP records                         14,486
DSC scenes                            1,139
resolved relation-code-400 edges       283
resolved relation-code-401 edges     9,985
```

The full outer archive materially expands the observed state space.

Model-local code 400:

```text
+24   edges
1       18
2       11
3        5
4      172
5        9
6       65
8        3
```

Only 229/283 of those model-local edges have effectively identity `+90`; **54 carry meaningful nonidentity texture-matrix state**.

Material-level code 401:

```text
+24   edges
1      318
2     1487
3      343
4     7425
5      160
6      154
7       93
8        5
```

This proves two things:

1. codes `6`, `7` and `8` are real states in the broader source archive;
2. one global sequential `1..5` mapping cannot be assumed for every BZ2 source generation/asset family.

The production policy is therefore conservative: preserve `projection_or_mapping_code_candidate` raw, apply only mappings that are explicitly supported by the relevant validation scope, and defer unsupported codes/transforms rather than converting a subset inference into fabricated UVs.

Full statistics are in `artifacts/validation/txmp_full_archive_projection_summary.json`.

## Retraction: `+78` does not prove code 5 is Cylindrical

The nested 664-record set showed an apparently perfect correlation: every nested `+24=5` record had `+78=1`. That was useful evidence at the time but **does not survive the full archive census**.

In the outer source tree, `+78=1` occurs with multiple `+24` values and both relation scopes. Examples include:

```text
code 400: +24=6, +78=1, +80=1   52 edges
code 400: +24=4, +78=1, +80=1   29 edges
code 401: +24=2, +78=1, +80=1   52 edges
```

Therefore:

- `+78` is **not** a code-5-exclusive/cylindrical flag;
- the former +78-based argument for `5 = Cylindrical` is retracted globally;
- `+76/+78/+80` remain raw auxiliary state.

This is exactly why derived hypotheses are kept separate from confirmed source-correlated fields.

## Higher modes 7 and 8

The full outer archive gives stronger context than the smaller high-resolution set:

- code `7`: 93/93 resolved material code-401 edges reference `reflection3`; this is very strong reflection/environment-associated evidence;
- code `8`: the outer archive uses `reflection3`, `reflection` and `backgr`; the nested walker subset's `cavern`/glass usage was therefore a subset-specific application, not a universal code-8 definition.

The Blender handoff preserves modes 7/8 as explicit special material texture state instead of forcing them through planar/spherical/cylindrical UV generation.

## Transform separation

The extraction path keeps these jobs distinct:

```text
HRC local S/R/T                    object placement
TXMP +24 raw operator state        projection/mapping mode frontier
TXMP +90 matrix S/R/T              projection-definition transformation
TXMP +2/+4 repeat                  image tiling density
TXMP +6 scale/offset               image-space placement
TXMP +60..+66 crop                 source-image window
TXMP +76/+78/+80...                auxiliary state, semantics unresolved
```

A critical Softimage behavior also limits how `+90` can be validated: raw projection UVs and projection-definition transformation are separate until the projection transformation is frozen/baked. Raw HRC UVs therefore cannot by themselves determine whether the `+90` rotation should be applied direct/inverse or in what exact UVW order.

## Production reconstruction policy

For asset extraction:

1. preserve meaningful source HRC polygon UVs exactly;
2. distinguish all-zero projection-dependent UVs from real authored UVs;
3. apply confirmed repeat/scale/offset/crop state;
4. generate projected UV maps only for explicitly supported projection/operator cases;
5. preserve unsupported `+24`, nonidentity `+90` and special material modes as source state rather than inventing coordinates;
6. keep model-local code-400 textures distinct from ordered material code-401 layers until their composition rule is reconstructed.

The current practical high-resolution UV generator remains useful, but it is now correctly scoped rather than treated as a universal BZ2 projection enum decoder.

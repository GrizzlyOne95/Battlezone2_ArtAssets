# TXMP projection math reversal

This note records the current state of the Battlezone 2 Softimage model-local texture projection reversal. The goal is to recover the source-authored texture placement without fabricating `TEXCOORD_0` for meshes that intentionally contain zero baked UVs.

The original 1998 Softimage files used for this validation are **not committed to this repository**. Only decoder code, derived statistics, and reproducible conclusions are retained here.

## Confirmed TXMP image-space UV scale and offset

The four big-endian floats beginning at **post-path TXMP byte offset `+6`** are source-correlated to the legacy `SI_Texture2D` image-space placement fields:

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

The older `texture_2d_transform_candidate` aggregate is retained as a compatibility alias for sidecars produced while the field was still unresolved.

Across the supplied corpus, `(1, 1, 0, 0)` is the normal identity state while non-default records contain plausible authored texture zoom and placement values.

### `glare.pic` source anchor

The surviving readable `ivstas00.xsi` contains a `SI_Texture2D` entry for `glare.pic` with:

```text
image size = 64 x 64
crop       = 0..63, 0..63
U/V scale  = 1, 1
U/V offset = 0, 0
matrix     = identity
```

The supplied original `glare.pic` independently decodes as a 64x64 Softimage PIC. Archival TXMP records referencing `glare` carry the same identity `+6` quartet and the same `0..63` crop rectangle.

## Confirmed TXMP texture-matrix SRT

The confirmed nine-float block begins at **post-path TXMP byte offset `+90`**. It is stored as nine big-endian IEEE-754 floats in this field order:

```text
+90   rotation X   (radians)
+94   rotation Y   (radians)
+98   rotation Z   (radians)
+102  scale X
+106  scale Y
+110  scale Z
+114  translation X
+118  translation Y
+122  translation Z
```

The block is the compact R/S/T representation of the source `SI_Texture2D` texture matrix. It is **not** the HRC/model transform and is distinct from the image-space U/V scale and offset at `+6`.

The production decoder exposes:

```text
si_texture2d_matrix_rotation_xyz_radians
si_texture2d_matrix_scale_xyz
si_texture2d_matrix_translation_xyz
```

A surviving readable `SI_Texture2D` matrix with diagonal `(-1, 1, -1)` corresponds to binary rotation `(0, -pi, 0)`, scale `(1, 1, 1)`, translation `(0, 0, 0)`, establishing both the field role and radians independently of showcase renders.

## Corpus validation

The complete supplied package, including the nested `Archival.zip`, contains **15,150 TXMP records** inspected for the confirmed matrix block.

```text
TXMP records inspected:       15,150
non-zero rotation records:     3,361
non-unit scale records:            1
non-zero translation records:      0
```

The rotation corpus repeatedly contains meaningful radian values including `pi`, `-pi`, `pi/2`, `-pi/2`, and authored non-cardinal angles. The fixed `+90` position is therefore source-correlated rather than a numeric-plausibility guess.

The `+6` quartet also varies meaningfully. Examples include non-uniform scales, negative scales and fractional offsets tied to stripe, glow, ceiling, tank and floor texture objects.

## Showcase scene validation

### ISDF walker

```text
108 MODELS
34 model -> TEXTURES2D relation-code-400 projection edges
34/34 resolved to source TXMP records
```

Projection picture families include `rusty`, `cavern`, `bump1` and `chrome3`.

### High-resolution ISDF tank

```text
21 MODELS
1 model -> TEXTURES2D relation-code-400 projection edge
1/1 resolved
```

The local projection is:

```text
tank2-grid1 -> tank2-t2d85 -> rusty
```

Its `+90` matrix SRT is identity and its `+6` image-space state is `(1, 1, 0, 0)`.

## Post-path `+24`: projection creation type frontier

A new relation-aware corpus pass substantially narrows the `u16` at post-path offset `+24`.

The important correction is that **`+24` is not the readable dotXSI `SI_Texture2D.mappingType` field**. The public dotXSI exporter writes `mappingType = 3` for explicit UVs, but BZ2 model-local code-400 records with `+24 = 3` are attached to class-4 meshes whose baked polygon UVs are all `(0, 0)`. Those objects therefore still require projection state. Treating `+24 = 3` as explicit UV would be contradictory.

Across the extracted archival scenes, every resolved **model -> TEXTURES2D relation-code-400** projection falls into only five `+24` values:

```text
+24 code    code-400 edges    representative models / usage
1           55                walker bmerge9..13, cavern
2           18                grid1 / grid8_1 floor-support grids
3           22                grid / grid2 bump projection objects
4           54                revol13 family and American pilot body parts
5          254                walker body / mechanical parts
           ---
           403 total
```

Values `7` and `8` do occur in generic TXMP records, but not in this extracted model-local code-400 projection set. That separation is another indication that the field describes projection creation/operator state rather than the image itself.

### Strong `siTxtCreationType` correspondence

Autodesk's Softimage SDK defines the `CreateProjection` `Type` argument as the `siTxtCreationType` enum. Two independent anchors now line up with the BZ2 `+24` corpus:

1. **Code `2` is geometrically consistent with Planar XZ.** `ISDF_walker-grid1` is the recovered 13x13 floor/support grid: its vertices lie on an XZ plane at `Y = 0`, and its code-400 texture record carries `+24 = 2`. Autodesk's own SDK examples apply `siTxtPlanarXZ` to a grid.
2. **Autodesk's SDK source example passes numeric `4.0` to `CreateProjection` and comments it as `siTxtSpherical`.** BZ2 `+24 = 4` is concentrated on rounded/revolved objects and pilot body parts, which is semantically consistent with a spherical operator.

Together with the contiguous BZ2 code range `1..5`, this yields the following **high-confidence correspondence**, but only the independently anchored entries should be treated as more than sequence inference:

```text
BZ2 +24    likely Softimage creation type    evidence status
1          Planar XY                         inferred from enum sequence/corpus
2          Planar XZ                         geometry-correlated
3          Planar YZ                         inferred from enum sequence/corpus
4          Spherical                         official numeric anchor + corpus
5          Cylindrical                       inferred from enum sequence/corpus
```

This table is deliberately **not hard-coded into the production parser yet**. The raw value remains preserved as `projection_or_mapping_code_candidate` until either the complete authoritative enum definition is recovered or an end-to-end visual reconstruction independently confirms the remaining `1`, `3` and `5` assignments.

## Crop and wrapping state

The crop rectangle is preserved in source pixel coordinates. The `glare.pic` anchor independently confirms a `0..63` rectangle for a 64x64 image. Vertical-origin convention, repeat/wrap direction and adjacent flag fields still need exact source mapping before Blender use.

## Transform separation

The recovered transforms have distinct jobs and must remain distinct:

```text
HRC local S/R/T                       object placement
TXMP +24 projection creation type     projection operator (frontier)
TXMP +90 texture-matrix R/S/T         SI_Texture2D projection-definition transform
TXMP +6 U/V scale + offset            image-space placement
TXMP crop/repeat state                image windowing/wrapping
```

The intended coordinate chain remains:

```text
model / projection-support coordinate
    -> projection operator
    -> SI_Texture2D texture-matrix R/S/T
    -> SI_Texture2D U/V scale and offset
    -> crop / repeat / wrapping state
    -> image texture
```

The exact source-to-support transform direction is still unresolved.

## Probe utility

`bz2_txmp_projection_probe.py` remains useful as a regression/falsification tool. The fixed production matrix field is known to be `+90`, but scanning other archives can verify that the layout remains stable.

```bash
python scripts/bz2_txmp_projection_probe.py --self-test
```

```bash
python scripts/bz2_txmp_projection_probe.py \
  out/walker/scene.model_textures.json \
  out/tank/scene.model_textures.json \
  --offset 90 \
  --rotation-unit radians \
  --json-out out/showcases.txmp_projection_srt.json
```

## Blender integration gate

The `+6` image-space scale/offset fields and `+90` matrix block are ready for production preservation and later Blender use. The next safe integration step is **not** to guess UV0; it is to close the projection operator/support-space questions.

Before applying generated projected coordinates, validate:

1. The remaining `+24` creation-type assignments (`1`, `3`, `5`) or the complete authoritative enum table.
2. Whether model coordinates are transformed into or out of projection-support space before the projection operator.
3. Crop/repeat vertical-origin and wrapping behavior.
4. At least one non-trivial end-to-end visual case reproducing the original Softimage placement.

The eventual Blender implementation should remain projection-driven and should **not** synthesize source UV0 merely to make textures appear.

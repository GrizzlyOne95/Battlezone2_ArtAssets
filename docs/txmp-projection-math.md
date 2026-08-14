# TXMP projection math reversal

This note records the current state of the Battlezone 2 Softimage model-local texture projection reversal. The goal is to recover the source-authored texture placement without fabricating `TEXCOORD_0` for meshes that intentionally contain zero baked UVs.

The original 1998 Softimage files and render outputs used for this validation are **not committed to this repository**. Only decoder code, derived statistics, hashes and reproducible conclusions are retained here.

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

## Original Softimage render ground truth

The supplied archival tree contains the **original Softimage render outputs**, not merely source models and textures. They decode cleanly with the repository PIC decoder and therefore give the projection reversal a historical pixel target instead of requiring subjective comparison against screenshots.

Derived metadata is committed in:

```text
artifacts/validation/original_render_ground_truth_summary.json
```

Key recovered outputs include:

```text
NewTank/NewTank/RENDER_PICTURES/TANK.1.pic
    1200 x 2100 RGBA
    mixed RLE
    complete decode, zero trailing bytes

walker_final/RENDER_PICTURES/walker_final_highres.1.pic
    2048 x 3584 RGBA
    mixed RLE
    complete decode, zero trailing bytes

walker_final/RENDER_PICTURES/walker.1.pic
    1200 x 2100 RGBA
    mixed RLE
    complete decode, zero trailing bytes
```

This changes the validation strategy materially: projection support direction, U/V handedness and crop origin can now be measured against the actual authored render rather than selected by visual plausibility alone.

## Showcase scene validation

### ISDF walker

```text
108 MODELS
34 model -> TEXTURES2D relation-code-400 projection edges
34/34 resolved to source TXMP records
```

Projection picture families include `rusty`, `cavern`, `bump1` and `chrome3`.

The high-resolution walker floor provides an unusually clean code-2 fixture:

```text
model           walker_final-grid1.7-0
geometry        13 x 13 = 169 vertices
bounds X        -71.5595703125 .. +71.5595703125
bounds Y         0 .. 0
bounds Z        -71.5595703125 .. +71.5595703125
plane            XZ at Y=0
texture object   walker_final-t2d85.1-0
picture          rusty, 483 x 363
+24              2
+6               1, 1, 0, 0
crop             0..482, 0..362
+90              identity
```

Archived scene revision `ISDF-walker_final.20-0` points its render setup at the same `walker_final_highres` output stem and supplies a matching recovered perspective camera (`2048x3584`, FOV `1.180949`, aspect `0.5714286`). This gives the Planar-XZ hypothesis an end-to-end camera/support/image fixture.

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

Its `+90` matrix SRT is identity and its `+6` image-space state is `(1, 1, 0, 0)`. The original `TANK.1.pic` now supplies an independent second-scene render target once the walker planar support convention is chosen.

## Post-path `+24`: projection creation type frontier

A relation-aware corpus pass substantially narrows the `u16` at post-path offset `+24`.

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

Autodesk's Softimage SDK defines the `CreateProjection` `Type` argument as the `siTxtCreationType` enum. Three independent anchors now line up with the BZ2 `+24` corpus:

1. **Code `2` is geometrically consistent with Planar XZ.** `walker_final-grid1` is an exact XZ floor grid at `Y=0`, its model-local TXMP carries `+24=2`, and Autodesk SDK examples create an XZ planar projection on a grid.
2. **Code `4` has an official numeric Spherical anchor.** Autodesk's C++ SDK source example passes numeric `4.0` to `CreateProjection` and comments it as `siTxtSpherical`; BZ2 `+24=4` is concentrated on rounded/revolved objects and pilot body parts.
3. **Code `5` has exclusive auxiliary-field behavior.** Every code-400 `+24=5` edge has TXMP `+78=1` (`254/254`), while no code-400 edge of types `1`, `2`, `3` or `4` does. The same exclusivity holds in the generic archival TXMP corpus: all `117/117` code-5 records have `+78=1`. This strongly corroborates code 5 as a distinct seam/wrap-bearing projection class, consistent with Cylindrical, without claiming that `+78` itself is a wrap flag.

The current working table is therefore:

```text
BZ2 +24    likely Softimage creation type    evidence status
1          Planar XY                         inferred from enum sequence/corpus
2          Planar XZ                         geometry + original-render fixture
3          Planar YZ                         inferred from enum sequence/corpus
4          Spherical                         official numeric anchor + corpus
5          Cylindrical                       strong exclusive structural/corpus evidence
```

This table is deliberately **not hard-coded into the production parser yet**. The raw value remains preserved as `projection_or_mapping_code_candidate` until the complete authoritative enum definition is recovered or an end-to-end projection reconstruction independently confirms the inferred entries.

### Auxiliary words `+76` and `+78`

The production parser now preserves these two post-crop words as raw fields only:

```text
field_u16_be_76
field_u16_be_78
```

Across all 664 generic archival TXMP records:

```text
(+24, +76, +78)   records
(1, 0, 0)             139
(2, 0, 0)             162
(3, 0, 0)              34
(4, 0, 0)             177
(4, 1, 0)              13
(5, 0, 1)             117
(7, 0, 0)              14
(8, 0, 0)               8
```

The relation-code-400 projection set preserves the same type separation. Exact semantic labels for `+76` and `+78` remain intentionally unresolved.

## Crop and wrapping state

The crop rectangle at `+60..+66` is preserved in source pixel coordinates. Two independent image-size anchors now validate it:

```text
glare.pic   64 x 64    uncropped rectangle 0..63, 0..63
RUSTY.PIC  483 x 363   uncropped rectangle 0..482, 0..362
```

A 664-record corpus check also corrected an earlier field-name mistake: the three `u16` values at `+68/+70/+72` are **not independent repeat/wrap values**. In every checked record they equal crop `x1`, `y0`, `y1` respectively.

The parser therefore exposes:

```text
crop_rect_trailing_duplicate_raw
crop_rect_trailing_duplicate_status
```

and retains `crop_repeat_raw` only as an explicitly deprecated compatibility alias.

Autodesk's later Softimage SDK makes an important architectural distinction here: projection-definition wrapping, texture repeats, alternate tiling and image-clip cropping are separate effect families. That supports keeping these binary structures separate rather than assigning nearby words to repeat/wrap behavior by position alone.

Still unresolved are the exact TXMP locations/semantics of the legacy repeat, alternate, swap and wrapping fields, plus the image vertical-origin convention used when converting pixel crop bounds into Blender coordinates.

## Transform separation

The recovered structures have distinct jobs and must remain distinct:

```text
HRC local S/R/T                       object placement
TXMP +24 projection creation type     projection operator (frontier)
TXMP +90 texture-matrix R/S/T         SI_Texture2D projection-definition transform
TXMP +6 U/V scale + offset            image-space placement
TXMP +60..66 crop rectangle           source-image crop window
TXMP +76/+78 raw auxiliary words      projection-type-correlated, semantics unresolved
repeat/wrap/alternate state           location still unresolved
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

The exact source-to-support transform direction and U/V orientation are the main remaining geometric questions.

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

The `+6` image-space scale/offset fields and `+90` matrix block are ready for production preservation and later Blender use. The next safe integration step is **not** to guess UV0; it is to use the original render fixture to close support-space direction and orientation.

The first end-to-end target should be the walker code-2 floor because it has an exact planar support, identity `+6` and `+90` transforms, a full-size uncropped image and a recovered original camera/render. The validation should render all plausible Planar-XZ U/V handedness/origin candidates, mask foreground geometry/reflection-dominated pixels, and choose a convention only if one candidate wins by a stable objective metric. The tank floor should then be used as an independent second-scene check.

Before applying generated projected coordinates generally, validate:

1. Support-space direction and U/V handedness/origin on the original walker render.
2. The walker-derived convention against the original tank render.
3. The remaining inferred `+24` creation types or the complete authoritative enum table.
4. Actual repeat/wrap/alternate field locations and crop vertical-origin behavior.

The eventual Blender implementation should remain projection-driven and should **not** synthesize source UV0 merely to make textures appear.

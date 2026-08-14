# TXMP projection math reversal

This note records the current state of the Battlezone 2 Softimage model-local texture projection reversal. The goal is to recover the source-authored texture placement without fabricating `TEXCOORD_0` for meshes that intentionally contain zero baked UVs.

The original 1998 Softimage files used for this validation are **not committed to this repository**. Only decoder code, derived statistics, and reproducible conclusions are retained here.

## Confirmed TXMP image-space UV scale and offset

The four big-endian floats beginning at **post-path TXMP byte offset `+6`** are now source-correlated to the legacy `SI_Texture2D` image-space placement fields:

```text
+6    UScale
+10   VScale
+14   UOffset
+18   VOffset
```

This was previously preserved only as a generic four-float transform candidate.

The public Softimage dotXSI exporter source writes these four `SI_Texture2D` fields as the U/V scale pair followed by the U/V offset pair. Across the supplied BZ2 corpus, the binary `+6` quartet follows the same defaults and authored-placement behavior: `(1, 1, 0, 0)` is the normal identity state, while non-default records contain plausible texture zoom/placement values rather than object-space transforms.

The production decoder now exposes:

```text
si_texture2d_uv_scale
si_texture2d_uv_offset
```

The older `texture_2d_transform_candidate` aggregate is retained as a compatibility alias for generated sidecars produced while the field was still unresolved.

### `glare.pic` source anchor

The surviving readable `ivstas00.xsi` contains a `SI_Texture2D` entry for `glare.pic` with:

```text
image size = 64 x 64
crop       = 0..63, 0..63
U/V scale  = 1, 1
U/V offset = 0, 0
matrix     = identity
```

The supplied original `glare.pic` independently decodes as a 64×64 Softimage PIC image. Archival TXMP records referencing `glare` carry the same identity `+6` quartet and the same `0..63` crop rectangle, providing an additional field-layout anchor without requiring visual guessing.

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

The production decoder exposes it as:

```text
si_texture2d_matrix_rotation_xyz_radians
si_texture2d_matrix_scale_xyz
si_texture2d_matrix_translation_xyz
```

### Direct readable-source cross-check

The supplied archive contains a surviving readable `ivstas00.xsi` with `SI_Texture2D` blocks. One source texture matrix contains the rotation matrix equivalent of a 180-degree Y rotation:

```text
-1  0  0  0
 0  1  0  0
 0  0 -1  0
 0  0  0  1
```

The corresponding binary TXMP transform pattern decodes at `+90` to approximately:

```text
rotation = (0, -pi, 0)
scale = (1, 1, 1)
translation = (0, 0, 0)
```

That establishes both the field role and the rotation unit independently of the showcase renders.

## Corpus validation

The complete supplied package, including the nested `Archival.zip`, contains **15,150 TXMP records** that were inspected for the confirmed matrix block.

Derived corpus statistics:

```text
TXMP records inspected:       15,150
non-zero rotation records:     3,361
non-unit scale records:            1
non-zero translation records:      0
```

The rotation corpus repeatedly contains meaningful radian values such as `pi`, `-pi`, `pi/2`, `-pi/2`, plus authored non-cardinal angles. The single non-unit scale example also decodes cleanly at the same fixed offset.

The fixed `+90` position therefore is no longer a candidate chosen by numeric plausibility; it is a source-correlated field location.

The `+6` image-space quartet also varies meaningfully across the corpus. Examples include non-uniform scales, negative scales, and fractional offsets tied to specific stripe, glow, ceiling, tank and floor texture objects, matching the behavior expected from `UScale`, `VScale`, `UOffset`, and `VOffset`.

## Showcase scene validation

The nested archival package contains the exact two high-resolution showcase scenes used by the reconstruction pipeline.

### ISDF walker

The DSC graph contains:

```text
108 MODELS
34 model -> TEXTURES2D relation-code-400 projection edges
```

All **34/34** code-400 edges resolve to source TXMP projection records. The local projection families include `rusty`, `cavern`, `bump1`, and `chrome3` texture objects.

### High-resolution ISDF tank

The DSC graph contains:

```text
21 MODELS
1 model -> TEXTURES2D relation-code-400 projection edge
```

The single local projection is:

```text
tank2-grid1 -> tank2-t2d85 -> rusty
```

This is the broad floor/support grid recovered by the reconstruction pipeline. Its confirmed `+90` matrix SRT is identity and its confirmed `+6` image-space state is:

```text
UScale  = 1
VScale  = 1
UOffset = 0
VOffset = 0
```

## Post-path `+24`: mapping/projection enum frontier

The `u16` at post-path offset `+24` varies strongly by texture object and scene usage. Across the supplied corpus its observed values include `1` through `8`, with `4` common.

The new source-level evidence narrows this significantly without yet justifying a full enum table:

- the public dotXSI exporter source explicitly writes `SI_Texture2D.mappingType = 3` for **explicit UVs**;
- the readable `ivstas00.xsi` `glare.pic` entry uses source `mappingType = 1`;
- an archival `glare` TXMP exemplar carries `+24 = 1`, while other scenes reuse the same image with a different `+24` value.

That behavior is consistent with `+24` being per-texture mapping/projection state rather than an image-format property, but the repository still does **not** assign `1..8` to planar/cylindrical/spherical/etc. by guesswork.

The raw `+24` value therefore remains preserved as `projection_or_mapping_code_candidate` until an exact same-record source/binary correlation or an authoritative enum definition closes the table.

## Other fields that remain distinct

### Crop and wrapping state

The crop rectangle is already preserved in source pixel coordinates. The `glare.pic` anchor independently confirms the `0..63` rectangle for a 64×64 image. Vertical-origin convention, repeat/wrap direction and the remaining adjacent flag fields still need exact source mapping before Blender use.

### HRC/model transforms

HRC node S/R/T controls object placement in the reconstructed scene. The `+90` TXMP block controls the `SI_Texture2D` texture matrix, while `+6` controls image-space U/V scale and offset. These transforms must remain separate in the Blender path.

## Softimage projection architecture

A projected texture conceptually has more than one stage:

```text
model / projection-support coordinate
    -> projection operator
    -> SI_Texture2D texture-matrix R/S/T
    -> SI_Texture2D U/V scale and offset
    -> crop / repeat / wrapping state
    -> image texture
```

The exact source-to-support direction and mapping operator are the current frontier. Until those are proven, the reconstruction should continue preserving the fields rather than guessing a projection function.

## Probe utility

`bz2_txmp_projection_probe.py` remains useful as a regression/falsification tool. The fixed production matrix field is known to be `+90`, but scanning other archives can verify that the layout remains stable.

Run the source-independent self-test:

```bash
python scripts/bz2_txmp_projection_probe.py --self-test
```

Scan all possible nine-float windows in generated sidecars:

```bash
python scripts/bz2_txmp_projection_probe.py \
  out/walker/scene.model_textures.json \
  out/tank/scene.model_textures.json \
  --top 20 \
  --json-out out/showcases.txmp_projection_candidates.json
```

Decode the confirmed block explicitly:

```bash
python scripts/bz2_txmp_projection_probe.py \
  out/walker/scene.model_textures.json \
  out/tank/scene.model_textures.json \
  --offset 90 \
  --rotation-unit radians \
  --json-out out/showcases.txmp_projection_srt.json
```

The probe uses big-endian floats by default. Little-endian decoding remains available only as a falsification/regression check.

## Blender integration gate

The `+6` image-space scale/offset fields and `+90` matrix block are now ready for production preservation and later Blender use. Actual projected texture coordinates should still wait until the following are proven:

1. The meaning of each relevant mapping/projection code at `+24`.
2. Whether source coordinates are transformed into or out of projection-support space before applying the projection operator.
3. Crop/repeat vertical-origin and wrapping behavior.
4. At least one non-trivial visual correlation where applying the recovered operator reproduces the original Softimage placement.

The eventual Blender implementation should remain projection-driven and should **not** synthesize source UV0 merely to make textures appear.

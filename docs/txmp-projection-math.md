# TXMP projection math reversal

This note records the current state of the Battlezone 2 Softimage model-local texture projection reversal. The goal is to recover the source-authored texture placement without fabricating `TEXCOORD_0` for meshes that intentionally contain zero baked UVs.

The original 1998 Softimage files used for this validation are **not committed to this repository**. Only decoder code, derived statistics, and reproducible conclusions are retained here.

## Confirmed TXMP texture-matrix SRT

A user-supplied source archive made it possible to validate the binary layout against the full available corpus and surviving readable dotXSI data.

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

The block is the compact R/S/T representation of the source `SI_Texture2D` texture matrix. It is **not** the HRC/model transform.

The production decoder now exposes it as:

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

The corresponding binary TXMP records decode at `+90` to approximately:

```text
rotation = (0, -pi, 0)
scale = (1, 1, 1)
translation = (0, 0, 0)
```

That establishes both the field role and the rotation unit independently of the showcase renders.

## Corpus validation

The complete supplied package, including the nested `Archival.zip`, contains **15,150 TXMP records** that were inspected for the confirmed block.

Derived corpus statistics:

```text
TXMP records inspected:       15,150
non-zero rotation records:     3,361
non-unit scale records:            1
non-zero translation records:      0
```

The rotation corpus repeatedly contains meaningful radian values such as `pi`, `-pi`, `pi/2`, `-pi/2`, plus authored non-cardinal angles. The single non-unit scale example also decodes cleanly at the same fixed offset.

The fixed `+90` position therefore is no longer a candidate chosen by numeric plausibility; it is a source-correlated field location.

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

This is the broad floor/support grid recovered by the reconstruction pipeline. Its confirmed `+90` matrix SRT is identity, while its separate post-path `+6` transform candidate is `(1, 1, 0, 0)`.

## Fields that remain distinct

Several TXMP structures must not be collapsed together just because they are all texture-related.

### Post-path `+6`: four-float 2D transform candidate

The four big-endian floats beginning at `+6` remain a separate 2D/image-space transform candidate. They show useful non-default variation in the walker corpus and are **not** the same transform as the confirmed `SI_Texture2D` matrix SRT at `+90`.

Exact component/direction semantics are still under validation.

### Post-path `+24`: unresolved mapping/projection code

The `u16` at post-path offset `+24` varies strongly by texture family. Across the supplied corpus its observed values include `1` through `8`, with `4` the most common.

The source evidence does **not** yet justify assigning those numbers to planar/cylindrical/spherical/etc. projection functions. In particular, readable `SI_Texture2D` records show that this binary field is not trivially identical to the first integer printed in the dotXSI block.

Do not hard-code enum meanings until another independent correlation establishes them.

### HRC/model transforms

HRC node S/R/T controls object placement in the reconstructed scene. The `+90` TXMP block controls the `SI_Texture2D` texture matrix. These are separate transforms and must remain separate in the Blender path.

## Softimage projection architecture

A projected texture conceptually has more than one stage:

```text
model / projection-support coordinate
    -> projection operator
    -> SI_Texture2D texture-matrix R/S/T
    -> TXMP 2D transform / crop / repeat state
    -> image texture
```

The exact source-to-support direction and mapping operator are the current frontier. Until those are proven, the reconstruction should continue preserving the fields rather than guessing a projection function.

## Probe utility

`bz2_txmp_projection_probe.py` remains useful as a regression/falsification tool. The fixed production field is now known to be `+90`, but scanning other archives can verify that the layout remains stable.

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

The `+90` matrix block is now ready for production preservation and later Blender use. Actual projected texture coordinates should still wait until the following are proven:

1. The meaning of each relevant mapping/projection code at `+24`.
2. Whether source coordinates are transformed into or out of projection-support space before applying the projection operator.
3. The exact meaning and application order of the separate `+6` four-float transform.
4. Crop/repeat vertical-origin and wrapping behavior.
5. At least one non-trivial visual correlation where applying the recovered operator reproduces the original Softimage placement.

The eventual Blender implementation should remain projection-driven and should **not** synthesize source UV0 merely to make textures appear.

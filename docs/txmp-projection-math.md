# TXMP projection math reversal

This note separates confirmed Softimage behavior from current BZ2 TXMP binary hypotheses.  The goal is to recover model-local texture coordinates without fabricating `TEXCOORD_0` for source meshes that intentionally contain zero baked UVs.

## Confirmed architecture

Softimage has two independent transform layers relevant to projected textures:

1. **Texture support transform** — the support is a 3D scene object whose position, rotation, and scale affect where the projection lands on the model.
2. **Texture projection definition transform** — the projection can be scaled, rotated, and translated in UVW space on that support.

Autodesk's `Texture.GetTransformValues` SDK example names the projection-definition parameters explicitly:

- `projrotu`, `projrotv`, `projrotw`
- `projsclu`, `projsclv`, `projsclw`
- `projtrsu`, `projtrsv`, `projtrsw`

The documented default projection-definition values are rotation `(0, 0, 0)`, scale `(1, 1, 1)`, and translation `(0, 0, 0)`.

The existing BZ2 DSC/TXMP work has also established that:

- relation code `400` is model-local projection state and is not interchangeable with material-level code `401`;
- high-resolution source meshes can have all-zero source UV0 while still carrying valid model-local texture projections;
- the four big-endian floats at post-path TXMP offset `+6` form a separate 2D texture-transform candidate and must **not** be overwritten by the nine-float projection hypothesis;
- the `u16` at post-path offset `+24` varies by texture family but its exact mapping/projection enum semantics are not yet proven;
- crop/repeat fields later in the TXMP payload are already preserved independently.

## Current nine-float hypothesis

Current binary/source correlation indicates another TXMP block containing nine floats in this field order:

```text
rotation X
rotation Y
rotation Z
scale X
scale Y
scale Z
translation X
translation Y
translation Z
```

For point transformation, the working interpretation is:

```text
Rxyz -> Sxyz -> Txyz
```

Using column vectors, that becomes:

```text
M = T @ S @ Rz @ Ry @ Rx
```

The exact post-path byte offset is intentionally not frozen in the production decoder yet.  It should be promoted only after the same offset correlates across the walker, high-resolution tank, and additional readable `SI_Texture2D` / TXMP records.

## Corpus probe

`bz2_model_texture_projection.py` already preserves the first 167 bytes after the NUL-terminated TXMP picture path as `txmp_tail_hex` in `scene.model_textures.json`.  This means the transform can be investigated from reconstruction output without repeatedly reopening the original archive.

Run the source-independent matrix self-test:

```bash
python scripts/bz2_txmp_projection_probe.py --self-test
```

Rank every possible nine-float window across one scene:

```bash
python scripts/bz2_txmp_projection_probe.py \
  out/scene.model_textures.json \
  --top 20 \
  --json-out out/scene.txmp_projection_candidates.json
```

Rank offsets across both showcase scenes at once:

```bash
python scripts/bz2_txmp_projection_probe.py \
  out/walker/scene.model_textures.json \
  out/tank/scene.model_textures.json \
  --top 20 \
  --json-out out/showcases.txmp_projection_candidates.json
```

Once source correlation identifies the winning byte offset, decode it explicitly and emit an R/S/T matrix for every code-400 record:

```bash
python scripts/bz2_txmp_projection_probe.py \
  out/walker/scene.model_textures.json \
  out/tank/scene.model_textures.json \
  --offset <confirmed-byte-offset> \
  --json-out out/showcases.txmp_projection_srt.json
```

The probe defaults to big-endian floats because the already-decoded structural TXMP fields use that byte order.  `--endian little` remains available as a falsification check.  Matrix construction defaults to degrees and also exposes `--rotation-unit radians` so the unit assumption can be tested rather than baked in silently.

## Promotion criteria

Do not wire this block into Blender merely because one offset looks numerically plausible.  Promote it into `bz2_model_texture_projection.py` and `blender_finish_reconstruction.py` only when all of these hold:

1. The same offset decodes plausibly across the walker and tank code-400 corpus.
2. Default readable Softimage records decode near `R=(0,0,0)`, `S=(1,1,1)`, `T=(0,0,0)`.
3. At least one deliberately transformed source projection matches a non-default decoded record axis-for-axis.
4. Applying the candidate transform moves a diagnostic texture in the same direction/orientation as the original Softimage render.
5. The support-object transform and projection-definition UVW transform are kept distinct if both are present in the source record.
6. Mapping-mode semantics are implemented only for enum values that have independent source evidence.

## Blender integration target

The eventual Blender path should remain projection-driven:

```text
model/support-space coordinate
    -> inverse/forward support transform as established by source tests
    -> projection function (planar/cylindrical/spherical/etc.)
    -> projection-definition UVW R/S/T
    -> existing TXMP 2D transform/crop/repeat state
    -> image texture
```

It should **not** synthesize source UV0 and should not silently route model-local code-400 projections through the material code-401 UV path.

The one-command reconstruction pipeline already emits `scene.model_textures.json`; the remaining integration is to pass that sidecar into the Blender finishing stage after the offset, transform direction, and mapping-mode meanings are validated.

# Softimage FxDirector scene reconstruction

Full DSC multi-root assembly exposed several outer HRC class-2 roots in the exact tank/walker scenes. They are not ordinary polygon faces. Their binary payloads identify them as **FxDirector** renderer/effect controller objects.

## CUSB settings block

A bounded FxDirector HRC contains a `CUSB` marker followed by a big-endian u32 byte length. The user-data payload uses 40-byte string slots.

Most settings fit in one slot:

```text
Flare_On 1
Flare_Scale 0.250000
Volume_On 0
Glow_On 0
```

Long settings such as `Flare_Preset` span several adjacent 40-byte slots. The decoder therefore reads through the NUL terminator first, then advances to the next 40-byte boundary. Treating every 40 bytes as an independent string would truncate flare-preset paths.

Across the supplied direct + archive HRC corpus:

- 81 outer class-2 HRCs;
- 78 class-2 subtype-1 records;
- 60 bounded CUSB FxDirector blocks;
- all 60 have sizes divisible by 40;
- observed CUSB sizes are 2120, 2160, 2200 and 2240 bytes.

Authored feature counts across those 60 blocks include:

- 38 with `Volume_On`;
- 16 with `Volume_Shard_On`;
- 22 with `Flare_On`;
- 3 with `Glow_On`;
- none with `Star_On` or `Projector_On` in this recovered subset.

The exporter preserves every recovered key/value rather than reducing the object to those headline switches.

## DSC association

FxDirector placement is not inferred from naming. The reference scenes use DSC `LIGHTS -> MODELS` relation code **20000** to associate a light/light-interest element with the class-2 FxDirector model.

Spotlights add one wrinkle: code 20000 can originate from the **interest object** rather than the actual spotlight. The already validated `LIGHTS -> LIGHTS` relation code 2110 maps actual spotlight -> interest object, so the FxDirector layer reverses that mapping when needed and records both names.

`bz2_fx_director_scene.py` attaches the decoded FxDirector record to:

- the corresponding DSC model node in the complete multi-root graph;
- the effective real light node, when that light has already been emitted by the camera/light scene layer.

No Blender visual effect is generated yet. The source renderer controls remain explicit metadata until an equivalent reconstruction is intentional and testable against the original PIC render.

## High-resolution ISDF tank

The exact tank scene contains two FxDirector roots:

- `tank2-fx1.1-0` -> `tank2-light9.1-0`
- `tank2-fx2.1-0` -> `tank2-light10.1-0`

Both specify:

- `Flare_On 1`
- `Volume_On 0`
- `Glow_On 0`
- `Star_On 0`
- `Flare_Scale 0.25`
- `Flare_Brightness 1.0`
- Mental Ray preset `REAL_LIGHT_short_rays/FLARE_no_reflection`

The original `TANK.1.pic` contains bright source-light/lens effects on the vehicle, so retaining these controls is directly relevant to Blender render matching.

## ISDF walker scene 20

The walker contains two volume FxDirector roots:

- `walker_final-fx4.13-0`: code-20000 source `spot2_int1`, resolved through 2110 to actual `spot2`
- `walker_final-fx3.13-0`: code-20000 source `spot3_int1`, resolved through 2110 to actual `spot3`

Both use:

- `Volume_On 1`
- `Flare_On 0`
- `Volume_Maximum_Length 200`
- `Volume_Brightness 2`

`fx3` retains a classic bright-reflection flare preset even though flare rendering is disabled. The decoder preserves inactive authored settings too; the active switches determine which source behavior should be reconstructed.

## Blender boundary

The immediate Blender representation should keep FxDirector nodes as named empties/controllers with their complete source properties and light association. A later renderer-facing pass can reconstruct flare sprites/compositor glare or volumetric cones only when the source behavior can be calibrated against the corresponding PIC frames.

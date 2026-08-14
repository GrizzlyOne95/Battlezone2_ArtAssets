# Model-local Softimage texture projections (DSC relation code 400)

The first material/texture pass recovered material-level `MATERIALS -> TEXTURES2D` relations (code 401). Full scene reconstruction requires a second, independent texture channel: `MODELS -> TEXTURES2D` relation code **400**.

## Code 400 is model-instance state

Across the supplied direct + embedded-archive DSC corpus:

- 37 scenes contain code 400;
- 598 models carry code-400 texture state;
- 686 code-400 edges exist;
- 52 models carry more than one code-400 texture object.

Every code-400 model also has at least one material, but code 400 is not simply a substitute for a missing material texture:

- 544 code-400 models have no code-401 texture on their first material;
- 54 code-400 models **do** already have a code-401 texture on their first material.

The later Carey walker revision makes the distinction explicit. A typical limb can use a shared first-material `wmiddle` texture through code 401, a stripe/glow material on another slot through code 401, and a separate model-local `rusty` texture object through code 400 at the same time.

Therefore the reconstruction pipeline preserves code 400 on the **DSC model node**, not on the global material definition.

## Why source TEXCOORD_0 is insufficient

For the exact walker scene 20, 30 code-400 class-4 nodes were checked directly. Every first-material polygon corner on all 30 stores `(0, 0)` source UVs. Representative Carey meshes likewise store all-zero corner UVs even after the later scene introduces a material-level `wmiddle` texture.

That is consistent with the authored scene relying on Softimage texture projections rather than baked mesh UV coordinates. The exporter therefore does not bind model-local code-400 textures through glTF `TEXCOORD_0`.

`bz2_model_texture_projection.py` instead attaches the unresolved projection record to the matching glTF/HRC node (when present) and writes a complete sidecar. This keeps enough source state for a Blender projection-support reconstruction without producing a knowingly incorrect preview.

## TXMP structure now preserved

A model-local TEXTURES2D object is a binary `TXMP` record. The following fields are structurally stable enough to retain:

- source picture path;
- post-path u32/u16 scope fields;
- four big-endian float32 values beginning at post-path offset 6;
- u16 values at offsets 22 and 24;
- float32 values at offsets 26 and 30;
- crop-enable field at offset 58;
- source pixel crop rectangle at offsets 60/62/64/66;
- the already identified bump/alpha-overlay candidate fields from the texture-layer work.

The four values at offset 6 behave like a 2D texture transform: default `rusty` records use `(1, 1, 0, 0)`, while stripe/glow records contain part-specific scale/flip/offset-like values. The surviving text-XSI `SI_Texture2D` template also contains explicit 2D texture transform fields. Nevertheless the binary component order remains labelled **candidate** until a complete binary/text counterpart is available.

The crop rectangle has stronger direct evidence. For an uncropped `rusty.pic` at 483x363, the binary rectangle is `[0, 482, 0, 362]`. Cropped records contain strict subrectangles; for example a 500x424 `blueglow.1.pic` record contains `[0, 499, 215, 423]`. The rectangle is preserved in source pixel coordinates, while vertical-origin semantics remain intentionally unresolved.

## Exact reference scenes

### ISDF walker scene 20

- 34 models / 34 code-400 edges;
- 25 `rusty` projections;
- 5 `cavern` projections;
- 2 `bump1` projections;
- 2 `chrome3` projections;
- five cropped projection records;
- all source pictures resolve;
- 32 projection-bearing models already resolve to nodes in the selected primary HRC glTF;
- two models require broader multi-HRC DSC assembly.

Two of the resolved projection-bearing nodes (`revol13`, `revol13_1`) are class-10 NURBS nodes. Projection state therefore belongs to the scene/model abstraction, not only class-4 polygon meshes.

### High-resolution ISDF tank

The exact tank scene has one code-400 `rusty` projection on `tank2-grid1`. `grid1` is a separate DSC model rather than a child of the selected `NewIVTankBody` HRC, which provides another concrete reason that the end-state exporter must assemble the entire DSC dependency graph instead of treating one HRC as the complete scene.

## Next reconstruction step

The remaining projection problem is the support/mapping function itself. The binary numeric mapping code and support transform need to be matched to Softimage's planar/cylindrical/spherical/cubic/camera/etc. projection semantics before Blender nodes should drive these images into a shader. Until then, code-400 images and parameters are preserved but intentionally disconnected from surface color.

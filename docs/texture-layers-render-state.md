# Softimage texture layers and render-state reconstruction

This stage closes two static-scene fidelity gaps that became visible only after assembled geometry, source materials, cameras, lights, and PIC renders were all available together.

## Ordered TEXTURES2D relations

A DSC material can have more than one `MATERIALS -> TEXTURES2D` relation with relation code `401`. These relations are ordered. The first material exporter used a single-value dictionary, so later relations overwrote earlier ones.

That is visibly wrong on the exact ISDF walker scene. Eight gunmetal materials each carry two texture objects in this order:

1. a `stripes.1` texture object;
2. a `blueglow.1` texture object.

The old path retained only the second object and therefore treated the blue-glow overlay as the base-color texture while discarding the stripes texture.

`bz2_texture_layers_gltf.py` now preserves every code-401 edge in source order. The first/default layer is bound as the portable glTF base-color texture; subsequent layers are retained in material extras and a `*.texture_layers.json` sidecar for Blender reconstruction.

## Historical TXMP source paths

Binary `.txt` texture objects are `TXMP` records. Their source-picture string is not confined to one path convention. The dump contains server paths, local drive paths, and old workstation/project roots, including:

- `//Server/Battlezone/modelsdirectory/...`
- `E:/WALKER/walkerstuff/...`
- `E:/NewTank/...`
- `D:/Softimage/SOFT3D/...`
- `D:/users/...`
- `//cgshare1/CG_PROD/...`

The resolver therefore does not require the original machine path to exist. It preserves the raw path, then resolves by authoritative logical suffix when `modelsdirectory` is present, otherwise by the source `PICTURES/<name>` tail with the current scene family preferred, and finally by basename as a last resort.

This resolves every referenced picture in the exact walker scene 20 despite its `E:/WALKER/...` paths.

## TXMP role-candidate fields

Two stable little-endian u16 fields occur in the post-path TXMP payload. Offsets below are measured from the first byte after the NUL terminating the source-picture path.

- payload offset `87`: value `1` occurs on the eight archival `bump1` texture objects;
- payload offset `89`: value `1` occurs on 54 archival texture objects dominated by `blueglow.1`, tank decals, and stripe overlays.

All 54 offset-89/value-1 objects resolve to PIC images with non-opaque alpha, and every one has alpha below 0.5 somewhere in the image. This is strong evidence for a special alpha-bearing overlay class, but it does **not** by itself prove emissive/additive blending. The exporter therefore labels these records `alpha_overlay_candidate` rather than `emissive`.

The exact walker eight two-layer materials all have the same structural pattern:

- `stripes.1`: field 87 = 2, field 89 = 2, base/default candidate;
- `blueglow.1`: field 87 = 2, field 89 = 1, alpha-overlay candidate.

## Exact target results

### ISDF walker scene 20

- 26 materials with code-401 texture relations;
- 8 multi-layer materials;
- 9 alpha-overlay-candidate layers total;
- 26 base-color textures restored;
- 0 unresolved source pictures;
- 0 missing glTF materials;
- resulting scene still loads as 108 geometry instances with finite bounds.

### High-resolution ISDF tank

- 12 materials with code-401 texture relations;
- no multi-layer material in this exact scene revision;
- 12 base-color textures restored;
- 0 unresolved source pictures;
- 0 missing glTF materials;
- resulting scene still loads as 22 geometry instances with finite bounds.

## Blender reconstruction

`blender_finish_reconstruction.py` reuses the already validated camera/light Blender importer, then applies the optional texture-layer and render-state sidecars. It creates a named node frame for each reconstructed material, uses the recovered source UVs, restores the base image, and mixes mode-1 alpha-overlay candidates over the base using the overlay image alpha.

This is intentionally a conservative preview reconstruction. It does **not** turn `blueglow` into emission yet. The raw TXMP fields and source names are written into Blender custom properties so later work can replace the provisional alpha mix with the proven original Softimage behavior without losing provenance.

## SETUP_SOFT render state

`bz2_sts_render_state.py` extracts renderer-independent facts and preserves the original Mental Ray controls by their source names instead of guessing Cycles/Eevee equivalents.

Both exact reference scenes use:

- `RENDERING_TYPE MENTAL_RAY`;
- active Mental Ray tracer and shadows;
- reflection, refraction, and shadow switches enabled;
- `AMBIENCE 0.3 0.3 0.3`;
- fog disabled;
- reflected ray depth 1, refracted depth 2, shadow depth 2.

The walker scene 20 additionally references `Bionic_Lens1` and `Bionic_Lens2`. Those lens shader names are preserved but are not translated into arbitrary Blender compositing effects.

The Blender finishing helper stores the Mental Ray settings, source ambience, switches, and lens shader references as scene custom properties and applies only the authoritative output resolution. It deliberately does not force a Blender render engine or treat Softimage ambience as a World background color.

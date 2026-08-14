# DSC camera/light and Blender reconstruction

This stage moves the recovered BZ2 assets from model-only conversion toward a Blender reconstruction of the authored Softimage scene.

## Proven DSC relation semantics

The exporter now uses these relation codes explicitly rather than treating every same-chapter relation as hierarchy:

| Relation | Code | Meaning |
|---|---:|---|
| `MODELS -> MODELS` | `110` | model parent/child |
| `CAMERAS -> CAMERAS` | `1110` | camera -> interest object |
| `LIGHTS -> LIGHTS` | `2110` | spotlight -> interest object |
| `LIGHTS -> MODELS` | `2200` | light parented to model |

This fixes the old production scene exporter bug where every `MODELS -> MODELS` relation was accepted as a parent edge, including procedural/self relations such as codes 251/260.

## Camera binary fields

The existing binary camera decoder previously called tagged field 4 an Euler rotation. Original scene geometry and PIC render framing show that interpretation is wrong.

The validated camera fields are:

- tag 3: camera position XYZ;
- tag 4: camera **interest/target position XYZ**;
- tag 7: near clip;
- tag 8: far clip;
- tag 9: focal-length field;
- tag 10: f-stop field;
- tag 11: focus-distance field;
- tag 12: vertical FOV in radians.

The glTF camera orientation is reconstructed by aiming local `-Z` from tag-3 position toward tag-4 interest, with local `+Y` as the up axis.

### High-resolution ISDF tank

`adconcept/SCENES/hi_res-ISDF_tank.1-0.dsc` resolves one real camera:

- position: `(8.3056049, 7.1631584, 17.1660347)`
- interest: `(0.0221499, 4.0971603, 0.8231997)`
- vertical FOV: `0.6904763` radians.

Using that transform on the reconstructed tank reproduces the original `TANK.1.pic` three-quarter viewpoint and screen occupancy closely enough to serve as independent semantic validation.

There is another camera with the same basename under `demo/`; therefore scene-prefix lookup is mandatory. The `adconcept` source is authoritative for the `adconcept` DSC.

### ISDF walker scene 20

`walker_final/SCENES/ISDF-walker_final.20-0.dsc` resolves:

- camera position: `(13.40835, 10.11527, 25.03217)`
- interest: `(1.442784, 8.571286, 2.122872)`
- vertical FOV: `1.180949` radians.

The exact-version source HRC `walker_final-null1.16-0.hrc` has 102 hierarchy nodes / 70 polygon meshes. Its HRC hierarchy agrees with the DSC code-110 graph for **102/102 comparable parent edges with zero mismatches**. The recovered camera projection also agrees closely with `walker_final_highres.1.pic`.

## Light binary fields

Tagged light field 8 was previously treated as a normalized direction. It is instead an interest/target position.

Recovered fields:

- tag 3: source RGB;
- tag 4: intensity;
- tag 5: range-like field;
- tag 6: cone-scale-like field;
- tag 7: light position;
- tag 8: light interest/target;
- tag 9: cone-angle-like field;
- tag 10: cone-spread-like field.

The archived walker makes the target interpretation obvious: one spot is positioned around `(67.9, 38.4, 89.5)` while its field-8 value is `(0,0,0)`, i.e. it is aimed at the scene/model origin.

`CAMERAS` and `LIGHTS` chapters contain separate interest pseudo-elements. Relation 1110/2110 collapses those pairs to one Blender/glTF camera or light instead of importing the interest object as a second physical camera/light.

Current exact-scene counts:

- high-resolution tank: **1 camera / 9 real lights**;
- walker scene 20: **1 camera / 6 real lights**.

The conversion of Softimage cone angle/spread into glTF spot inner/outer cone is deliberately conservative and remains provisional. Original raw values are stored in extras for later renderer calibration.

## SETUP_SOFT render metadata

`.sts` files are plain-text scene/setup state rather than hidden HRC pose data. They provide useful render authority.

### Tank

`adconcept/SETUP_SOFT/hi_res-ISDF_tank.1-0.sts`

- output target: `.../RENDER_PICTURES/TANK`
- resolution: **1200 × 2100**
- render frame: `1 1 1`
- perspective FOV includes the same `0.6904763` used by the recovered camera.

### Walker scene 20

`walker_final/SETUP_SOFT/ISDF-walker_final.20-0.sts`

- output target: `walker_final_highres`
- resolution: **2048 × 3584**
- render frame: `1 1 1`
- perspective FOV: `1.180949`.

This version-awareness is important: later Carey walker scenes target `walker_final_highres2`, which is absent from the supplied archive. Comparing a later Carey HRC against the earlier `walker_final_highres.1.pic` creates a false pose mismatch.

## Blender reconstruction helper

`scripts/blender_reconstruct_scene.py` is intended to run from Blender after the glTF scene is generated:

```bash
blender --background --python scripts/blender_reconstruct_scene.py -- \
  recovered_scene.gltf \
  recovered_scene.scene.json \
  recovered_scene.blend
```

It:

- imports the recovered glTF hierarchy/materials/textures/cameras/lights;
- selects the recovered source camera;
- applies the original STS render resolution;
- applies source start/end/current frame metadata;
- copies camera/light provenance and interest data into Blender custom properties;
- saves a native `.blend`.

It intentionally does **not** force Cycles/Eevee, a Blender color transform, world lighting, or speculative shader nodes yet. Those choices should be driven by side-by-side comparison with the original PIC renders rather than baked in prematurely.

## Validation boundary

Both augmented test glTFs still load through an independent scene reader after camera/light insertion:

- tank: 22 materialized geometry instances, 1 camera, 9 lights, finite bounds;
- exact walker scene 20: 70 geometry instances, 1 camera, 6 lights, finite bounds.

The next fidelity work is renderer-facing: exact `.mtr` self-illumination/reflection/transparency semantics, scene environment/floor/background, and then Blender render comparisons against the original Softimage PICs.

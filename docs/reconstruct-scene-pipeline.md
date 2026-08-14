# One-command Softimage scene reconstruction

`bz2_reconstruct_scene.py` turns the validated reverse-engineering stages into one reproducible static-scene reconstruction command.

The goal is no longer merely to extract geometry. The output bundle is intended to be a practical handoff into Blender while retaining enough source metadata to continue improving fidelity without redoing the binary reversal.

## Usage

```bash
python scripts/bz2_reconstruct_scene.py \
    path/to/scene.dsc \
    path/to/modelsdirectory-or-Archival.zip \
    <scene-prefix> \
    artifacts/reconstructed/<scene-name>
```

For an embedded historical scene, `scene-prefix` is the logical folder that contains `MODELS`, `MATERIALS`, `TEXTURES2D`, `CAMERAS`, etc. inside the source archive.

## Pipeline order

The driver deliberately runs the stages in this order:

1. **DSC multi-root assembly** — every `MODELS ... ROOT` HRC is instantiated and all internal model nodes are mapped back to DSC indices.
2. **ROOT specialized geometry** — currently proven class-1 rectangular floor/support grids are emitted.
3. **Complete-scene material binding** — class-4 material slots, inheritance, floor object materials and safe untextured NURBS materials.
4. **Ordered code-401 texture layers** — restores base/overlay ordering and historical absolute source-picture paths.
5. **Corrected MTR semantics** — ambient/diffuse/specular/shininess/transparency/reflectivity/IOR.
6. **DSC cameras/lights** — recovered position/interest cameras, punctual lights and hierarchy.
7. **Model-local code-400 projections** — preserves object-instance Softimage texture/projection state and source images.
8. **FxDirector** — preserves flare/volume/glow controller settings and their effective lights.
9. **SETUP_SOFT render state** — Mental Ray settings, output dimensions, ambience and lens-shader provenance.

Material refinement intentionally occurs before camera/light augmentation. This avoids material extension bookkeeping from accidentally replacing `KHR_lights_punctual` after the lights have been emitted.

## Bundle output

The destination contains at least:

```text
scene.gltf
scene.bin
textures/
scene.multiroot.json
scene.special_geometry.json
scene.materials.json
scene.texture_layers.json
scene.mtr.json
scene.scene.json
scene.model_textures.json
scene.fx.json
scene.render_state.json
reconstruction.json
blender_command.txt
reports/
```

`reconstruction.json` is the top-level manifest. Individual stage reports remain available because the source reversal is still evolving and each layer should remain independently regression-testable.

## Blender handoff

The pipeline writes a ready-to-run Blender command to `blender_command.txt`. It invokes `blender_finish_reconstruction.py`, which:

- imports the final glTF hierarchy;
- selects the recovered source camera;
- applies authoritative render dimensions;
- reconstructs the proven base + alpha-overlay texture stack;
- retains Softimage/Mental Ray provenance as Blender custom properties;
- saves a native `scene.blend`.

Renderer-specific behavior is intentionally not fabricated. Model-local projection supports, blue-glow emission semantics, Mental Ray lens shaders, reflection environment response and FxDirector flare/volume appearance remain source metadata until their Blender equivalents can be calibrated against the original PIC renders.

## End-to-end reference validation

### ISDF walker scene 20

The validated full stack produces:

- 108 authored DSC model nodes before cameras/lights;
- 115 final nodes after one camera and six real lights;
- 78 geometry meshes;
- 116 final material primitives;
- 51 refined source materials;
- 34 recovered glTF image layers;
- eight multi-layer materials;
- 34 / 34 model-local code-400 projections attached;
- two FxDirector volume controllers resolved;
- zero material, texture-picture, projection-picture or FxDirector resolution failures.

The final glTF retains `KHR_materials_specular`, `KHR_materials_transmission`, `KHR_materials_ior`, and `KHR_lights_punctual` together. An independent loader resolves all 116 geometry instances with finite bounds.

The render-state sidecar preserves the original Mental Ray 2048x3584 `walker_final_highres` target and the two `Bionic_Lens` shaders.

### High-resolution ISDF tank

The same pipeline produces:

- 21 authored model nodes before cameras/lights;
- 31 final nodes after one camera and nine real lights;
- 18 geometry meshes, including the recovered floor grid;
- 25 final material primitives;
- 27 refined source materials;
- 16 recovered glTF image entries;
- the tank's model-local projection attached to the real grid node;
- both short-rays FxDirector flare controllers resolved;
- zero stage-resolution failures.

An independent loader resolves all 25 geometry instances with finite bounds. The render-state sidecar preserves the Mental Ray 1200x2100 `TANK` target.

## Current fidelity boundary

At this point the static scene graph itself is substantially reconstructed: complete roots, polygon/NURBS geometry, object hierarchy, transforms, floor/support geometry, material assignment, source images, camera, lights and renderer metadata.

The highest-value remaining visual work is no longer scene discovery. It is reproducing Softimage's **projection support/mapping math and renderer-facing shader behavior** closely enough that a Blender render can be compared meaningfully against the original PIC frame.

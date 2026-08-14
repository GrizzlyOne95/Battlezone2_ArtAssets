# Complete-scene material binding across DSC ROOT HRCs

The first material milestone bound source materials to one selected HRC. Full DSC reconstruction now assembles every model marked `ROOT`, so source material assignment must be repeated across the entire merged scene graph.

`bz2_dsc_multiroot_material_gltf.py` performs that scene-wide pass without reintroducing the old name-resolution ambiguity.

## Authoritative node identity

The multi-root assembler writes the DSC model index onto every mapped glTF node as `bz2_dsc_model_index`, while also retaining the ROOT HRC name and source byte offset.

Class-4 material binding therefore uses three independent source identities:

- ROOT HRC instance;
- HRC record byte offset;
- DSC model index.

No global suffix/name heuristic is needed.

## Class-4 material slots

Each class-4 polygon retains the already proven material-slot index in the upper 16 bits of its metadata word. The corresponding DSC model supplies an ordered `MODELS -> MATERIALS` relation-code-300 list.

For every class-4 node the scene-wide binder:

1. reopens the authoritative ROOT HRC;
2. finds the exact source record by byte offset;
3. decodes the original polygons/UVs/normals/material slots;
4. resolves direct code-300 materials by the node's DSC model index;
5. if no direct list exists, walks the reconstructed glTF/DSC parent graph to the nearest material-bearing ancestor;
6. rebuilds that node as per-material glTF primitives.

This retains the two known exact-walker inheritance cases (`cube8`, `cube10`) while also covering the additional standalone ROOT meshes that were absent from the old single-HRC export.

## ROOT class-1 grid material

The recovered floor/support grids do not contain class-4 per-polygon material metadata. They are ordinary DSC models with a direct material list, so the complete-scene binder assigns the first object material to the grid primitive.

- walker `grid1` -> `walker_final-mat8.1-0`
- tank `grid1` -> `tank2-mat8_1.1-0`

The grid's texture remains projection-driven through DSC code 400; assigning the object MTR does not fabricate UV texturing.

## Parametric material safety

Reconstructed NURBS surfaces currently expose normalized parameter-space UVs for geometry preservation. Those coordinates are **not** authoritative Softimage texture projections.

The scene-wide binder therefore assigns an object-level material to a class-9/class-10 mesh only when:

- exactly one material applies; and
- that material has no DSC `MATERIALS -> TEXTURES2D` code-401 relationship.

The rule is based on the DSC relationship itself, not whether a texture URI happened to resolve. This avoids a historical absolute source path falsely making a textured NURBS material look untextured.

All six parametric meshes in exact walker scene 20 satisfy the safe untextured rule.

## Exact walker scene 20

After full multi-root assembly and class-1 floor recovery:

- 71 / 71 class-4 meshes rebound;
- zero class-4 decode failures;
- zero material-slot errors;
- 109 class-4 material primitives;
- two inherited-material nodes (`cube8`, `cube10`);
- one materialized class-1 floor grid;
- six safely materialized untextured NURBS objects;
- 51 source materials;
- 78 final geometry meshes;
- 116 final glTF primitives.

An independent glTF loader resolves all 116 geometry primitives with finite bounds including the recovered floor plane.

## Exact high-resolution tank

- 17 / 17 class-4 meshes rebound;
- zero decode failures;
- zero material-slot errors;
- 24 class-4 material primitives;
- no inherited-material nodes;
- one materialized class-1 floor grid;
- 27 source materials;
- 18 final geometry meshes;
- 25 final glTF primitives.

The two class-4 ROOTs added by full scene assembly are simple slot-0 objects:

- `Main_tank-gun.1-0` -> one source material;
- `tank2-bmerge5_default_7.1-0` -> one source material.

The full result again loads with finite bounds.

## Pipeline position

This pass intentionally restores the **material assignment topology** first. Existing non-destructive stages should then be reapplied in this order:

1. ordered code-401 texture layers / historical source-picture resolution;
2. corrected MTR ambient/diffuse/specular/shininess/transparency/reflectivity/IOR semantics;
3. camera/light scene reconstruction;
4. code-400 model-local projection metadata;
5. FxDirector/render-state metadata;
6. Blender finishing/import.

Keeping those stages independent makes it possible to improve renderer fidelity without destabilizing the now validated complete scene/model/material graph.

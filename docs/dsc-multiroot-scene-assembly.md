# Full DSC scene assembly from ROOT HRCs

The earlier assembled-model milestones exported one selected HRC at a time. That is sufficient for many standalone assets, but it is not a complete Softimage DSC scene model.

The exact ISDF tank and walker scenes demonstrate a stronger structural rule: the DSC `MODELS` chapter can contain several entries marked `ROOT`, and **each ROOT entry has its own scene-local HRC**. Most non-root model entries are not separate HRC files at all; they are the internal named nodes serialized inside those ROOT HRC trees.

## Scene assembly rule

`bz2_dsc_multiroot_gltf.py` therefore treats DSC ROOT flags as the scene-instance boundary:

1. parse the DSC `MODELS` chapter while retaining its `ROOT` flags;
2. resolve every ROOT entry to `<scene family>/MODELS/<model>.hrc`;
3. export each root HRC with the existing class-4 + parametric HRC pipeline;
4. merge all root buffers/nodes/meshes into one glTF scene;
5. map every internal HRC node back to its DSC model entry inside that root's code-110 subtree;
6. verify every DSC `MODELS -> MODELS` code-110 edge against the merged HRC hierarchy.

No extra scene wrapper transform is multiplied on top of the HRC root. When DSC ENVIRONMENT contains an explicit SRT for a ROOT model, that SRT is the authoritative scene-instance matrix and replaces the HRC outer matrix exactly once.

In the exact reference assets, recoverable HRC outer transforms agree with the corresponding DSC root SRT to ordinary float precision. Primitive/face/spline roots often do not expose an HRC outer SRT at all; for those objects the DSC environment supplies the missing instance transform.

## Exact ISDF walker scene 20

`walker_final/SCENES/ISDF-walker_final.20-0.dsc` contains 108 model entries and seven ROOT models:

- `walker_final-bmerge19.1-0`
- `walker_final-circle2.4-0`
- `walker_final-fx3.13-0`
- `walker_final-fx4.13-0`
- `walker_final-grid1.7-0`
- `walker_final-null1.16-0`
- `walker_final-spline2.6-0`

All seven resolve to scene-local HRC files.

The merged result is exact structurally:

- 108 / 108 DSC models mapped;
- zero ambiguous node mappings;
- 101 / 101 code-110 parent edges match;
- 71 class-4 polygon meshes;
- six reconstructed parametric meshes;
- 77 total geometry objects;
- 108 final scene nodes.

The previously selected `null1.16-0.hrc` remains the large master hierarchy, but it is only one of seven scene roots. `bmerge19` contributes another real polygon mesh, while the other roots preserve support/effect/spline state even where their render geometry is not yet decoded.

When the existing scene-fidelity and model-projection layers are applied to this complete graph, the original camera and six real lights still resolve, and all **34/34** code-400 model-local texture projections now attach to actual scene nodes. The prior primary-HRC-only output could resolve only 32/34.

## Exact high-resolution ISDF tank

`adconcept/SCENES/hi_res-ISDF_tank.1-0.dsc` contains 21 model entries and six ROOT HRCs:

- `Main_tank-gun.1-0`
- `tank2-bmerge5_default_7.1-0`
- `tank2-fx1.1-0`
- `tank2-fx2.1-0`
- `tank2-grid1.1-0`
- `tank2-NewIVTankBody.1-0`

Again, all six resolve to scene-local HRC files and every non-root model maps into one of those trees:

- 21 / 21 DSC models mapped;
- 15 / 15 code-110 parent edges match;
- 17 class-4 polygon meshes;
- 17 total geometry objects;
- zero hierarchy mismatches.

The original tank-body-only path contained 15 class-4 meshes. Full scene assembly adds the standalone `gun` and `bmerge5_default_7` polygon roots and also restores `grid1` as a real scene node. This is particularly important because `grid1` owns the tank's model-local code-400 texture projection.

After camera/light and model-projection augmentation, the full tank retains one recovered camera, nine real lights, and resolves its previously absent `grid1` projection node.

## Why this matters for Blender

This changes the target from "import a model HRC" to "reconstruct the authored Softimage scene graph."

A Blender reconstruction should now receive every DSC scene root, including invisible/support objects whose significance is transform, texture-projection, effects, animation, or parenting rather than a class-4 render mesh. That gives later projection, animation, visibility, and renderer reconstruction a complete object graph to attach to instead of synthetic placeholders.

## Remaining scene-geometry formats

Some ROOT HRCs are class 1, 2, or 6 and currently produce named transform/support nodes rather than render geometry. The reference scenes already show that class-2 FX faces and class-1 projection grids can be authored scene components even when they are not ordinary polygon meshes. Their preservation in the multi-root graph lets those payload types be reversed independently without another scene-assembly rewrite.

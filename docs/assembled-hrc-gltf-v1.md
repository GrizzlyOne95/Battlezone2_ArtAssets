# Assembled HRC glTF v1

`bz2_hrc_gltf.py` is the first production-oriented path from one binary Softimage HRC to an assembled open-format model.

## Included in this milestone

- internal HRC preorder hierarchy;
- class-4 polygon meshes;
- source vertex positions;
- source per-corner UVs;
- source normals, with generated fallback normals only for the proven all-NaN missing-normal sentinel;
- recovered local scale/rotation/translation;
- later archived material-slot SRT envelopes missed by the original class-4 transform probe;
- constrained tessellation for `0xFFFFFFFF` multi-contour/hole polygons;
- glTF 2.0 node/mesh/accessor/buffer output.

The temporary material is deliberately unbound and double-sided. Original MTR/TXT/PIC material/texture binding belongs to the DSC/source-material integration stage.

Class-9/class-10 parametric records are kept as hierarchy nodes but do not emit geometry in this milestone. They will be attached to the same scene tree rather than flattened separately.

## Geometry-only manual reference targets

The manually converted Blender files supplied by the project owner are used only as geometry reference data.

### Archived high-resolution ISDF tank soft body

Source:

`Archival.zip/Tank/Tank/MODELS/TankBaseFinal-NewIVTankSoft.1-0.hrc`

Expected assembled output:

- 11 hierarchy nodes;
- 10 class-4 nodes;
- 10 glTF meshes;
- zero unresolved class-4 local transforms.

### Archived high-resolution ISDF tank body

Source:

`Archival.zip/adconcept/MODELS/tank2-NewIVTankBody.1-0.hrc`

Expected assembled output:

- 16 hierarchy nodes;
- 15 class-4 nodes;
- 15 glTF meshes;
- zero unresolved class-4 local transforms.

This target exposed a later Softimage envelope form: class-4 SRT can be followed by material-slot tags other than slot `1`. Generalizing that anchor recovers `Antenae` and `cyl9`; the exact SRT byte sequences also occur in later archived revisions.

### Archived high-resolution ISDF walker

Source:

`Archival.zip/walker_final/MODELS/walker_final_carey-null1.1-0.hrc`

Expected assembled output:

- 103 hierarchy nodes;
- 71 class-4 nodes;
- 71 glTF meshes;
- zero unresolved class-4 local transforms;
- 56 multi-contour source polygons tessellated.

The remaining unresolved non-class4 transform records in this asset are leaf construction/spline/NURBS nodes. None is an ancestor of a polygon mesh, so they do not affect polygon-mesh placement. In the manual Blender derivative, the corresponding class-1 construction records are objects with no mesh datablock, while actual class-4 pieces have mesh data.

## Validation

Locally generated tank and walker glTF outputs were loaded through an independent glTF/scene reader after export. The tank resolves as 15 geometry instances and the walker as 71 geometry instances with finite scene bounds and valid external binary buffer references.

This remains an incremental milestone, not a claim that complete BZ2 scene reconstruction is finished. The next geometry step is class-9/class-10 NURBS emission into this same hierarchy; the next scene step is original material/texture binding and then DSC assembly.

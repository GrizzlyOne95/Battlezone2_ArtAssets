# Manual Blender geometry ground truth

Six manually recovered Blender files supplied by the project owner are useful as geometry-only reference data. They are **not** treated as authoritative scene reconstructions: the owner explicitly notes that the manual process recovered geometry, not the original materials, textures, scene setup, or other Softimage metadata.

The `.blend` files therefore validate only geometry identity, mesh segmentation, and topology counts. HRC/DSC/MTR/TXT/PIC and the other original source files remain authoritative for hierarchy, transforms, materials, textures, lights, cameras, animation, and scene relationships.

## Reference files

| Blender reference | Meshes | Vertices | Blender polygons | Best original-source match |
|---|---:|---:|---:|---|
| `bz2tank.blend` | 10 | 11,294 | 21,787 | `Archival.zip/Tank/Tank/MODELS/TankBaseFinal-NewIVTankSoft.1-0.hrc` |
| `bz2tank2.blend` | 13 | 5,336 | 5,332 | `Archival.zip/adconcept/MODELS/tank2-NewIVTankBody.1-0.hrc` and later equivalent revisions |
| `bz2walker.blend` | 71 | 28,689 | 29,457 | `Archival.zip/walker_final/MODELS/walker_final_carey-null1.1-0.hrc` |
| `old_warrior.blend` | 11 | 320 | 341 | `movieAssets/movie_stuff/MODELS/WARRIOR-DUMMYROOT.1-0.hrc` |
| `voyagerlowpoly.blend` | 28 | 3,437 | 3,114 | `movieAssets/movie_hires/MODELS/lowresvger-*` family; contains manually tessellated NURBS geometry |
| `plutobase.blend` | 4 | 10,143 | 19,606 | source naming was not preserved well enough for an exact mapping yet |

Counts were read directly from Blender's embedded SDNA schema; Blender itself is not required for this inventory.

## Polygon decoder cross-validation

### Fury Warrior

All 11 manually recovered mesh objects have exact HRC count parity:

- `main_body`: 120 vertices / 135 polygons in both;
- `body_morph`: 94 / 92 in both;
- four `POLYP_*` meshes: 18 / 20 each in both;
- `c_screen`: 14 / 10 in both;
- four hardpoint meshes: 5 / 6 each in both.

This is an exact mesh-by-mesh topology match for the complete geometry subset in the manual file.

### High-resolution ISDF tank

The `tank2-NewIVTankBody` source matches 13 named Blender meshes. Twelve of those thirteen have exact vertex and polygon parity against the archived HRC, including `Antenae`, `bmerge14`, both fins, `cyl9`, `cyl10`, `gun_frame`, all four spheres, and `turret`.

`bmerge5_default_2` is the sole mismatch: the manual Blender file contains 857 vertices / 804 polygons while the closest HRC revisions contain 790 / 742. This should be treated as a revision/manual-conversion difference until its provenance is isolated; it is not evidence against the class-4 decoder because the other twelve named meshes match exactly.

### High-resolution ISDF walker

The manual file contains 71 mesh datablocks, and all 71 correspond to class-4 nodes in the archived source HRC.

Ordinary single-contour source polygons match Blender exactly. The systematic mismatches occur only on meshes that contain the newly decoded `0xFFFFFFFF` contour separators. For example:

- `extru17`: 320 vertices / 242 polygons in HRC and Blender;
- `extru12`: 344 vertices in both, but 260 source polygons become 430 Blender polygons;
- `bool4`: 524 vertices in both, but 353 source polygons become 593 Blender polygons.

The affected source records contain multi-contour polygons, while the unaffected records do not. This independently validates the interpretation of `0xFFFFFFFF` as an in-polygon contour/hole separator and confirms that the production exporter needs hole-aware tessellation rather than rejecting those records.

## NURBS ground truth

`voyagerlowpoly.blend` is particularly valuable because several original NURBS objects were manually converted to polygon meshes. Preserved names include `nurbs7`, `nurbs53`, `nurbs55`, `nurbs57`, `nurbs58`, `nurbs106`, `nurbs133`, and `nurbs140` through `nurbs144`.

Examples of the resulting manual tessellation:

- `nurbs106`: 12 vertices / 8 polygons;
- `nurbs53`, `55`, `57`, `58`, `140`: 27 / 24;
- `nurbs133`, `141`, `142`, `143`: 145 / 112;
- `nurbs144`: 14 / 8;
- `nurbs7`: 384 / 384.

These counts should be used as comparison data for the automated class-10 NURBS tessellator, not as a requirement that the open-format derivative use exactly the same tessellation density. The recovered rational control points, weights, knot vectors, trim curves, and parameter ranges remain the archival source of truth.

## Consequence

The manual files substantially raise confidence in the recovered class-4 format. They also identify two concrete next validation targets:

1. reproduce the manually converted Voyager NURBS shapes from the original rational records and compare geometry/bounds;
2. implement hole-aware tessellation for class-4 multi-contour polygons and compare the result against the manually converted walker meshes.

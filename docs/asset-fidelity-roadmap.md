# Asset-fidelity reconstruction path

The reconstruction target is now the **assets themselves**: complete model geometry, hierarchy, local/scene transforms, source UVs, projected UVs, texture placement/tiling, materials and source images. Pixel-matching historical floors, lights, lens effects and Mental Ray output is useful validation evidence but is not a gate for ordinary asset extraction.

## Geometry, hierarchy and transforms

The existing reconstruction stack already treats these as production data rather than render approximations:

- every DSC `MODELS ... ROOT` HRC is assembled, rather than assuming one master model;
- the HRC preorder hierarchy and local S/R/T are retained;
- DSC `MODELS -> MODELS` relation code 110 is used to validate the reconstructed parent graph;
- DSC `ENVIRONMENT` SRT overrides the ROOT scene-instance transform when present;
- class-4 polygon meshes retain source polygon-corner normals/UVs and material-slot metadata;
- reconstruction-ready NURBS curves/surfaces are tessellated into the same hierarchy, with parameter-space UVs clearly distinguished from source texture projection.

## UV policy

There are now three deliberately separate UV cases.

### 1. Authored polygon UVs

Class-4 HRC stores UVs per polygon corner. These values continue to be emitted verbatim as glTF `TEXCOORD_0`. The new UV-provenance stage records whether each primitive contains meaningful source UVs or an all-zero coordinate set.

### 2. Projection-driven polygon UVs

High-resolution Softimage assets frequently contain all-zero polygon UVs because the texture placement was authored as a texture projection instead of a frozen UV unwrap.

For these assets, Blender now creates **additional named UV maps** instead of overwriting the imported source UV layer. The working projection table is:

```text
1  Planar XY
2  Planar XZ
3  Planar YZ
4  Spherical
5  Cylindrical
```

The supplied relation-code-400 corpus contains 403 resolved model-local projections. All 403 use only these five codes and all 403 have identity `+90` matrix SRT. This means the model-local asset path can move forward without making the unresolved non-identity matrix direction a blocker.

Generated UVs apply the confirmed `SI_Texture2D` U/V scale and offset (`TXMP +6`) plus the confirmed source-pixel crop rectangle. Angular maps receive polygon-local seam correction so interpolation does not cross the long way around the U seam.

The source UV layer remains intact for provenance and comparison.

### 3. NURBS parameter UVs

The current NURBS tessellator emits normalized surface parameter coordinates. Those are useful topology coordinates but are **not** presented as recovered Softimage texture projection. They remain explicitly labeled as parameter-space UVs until textured NURBS binding is reconstructed.

## Texture placement and tiling

Material-level DSC code-401 texture records now decode the same common TXMP state as model-local code-400 records:

- `UScale`, `VScale`, `UOffset`, `VOffset` at `+6`;
- raw projection/operator code at `+24`;
- crop rectangle at `+60..+66`;
- raw auxiliary words at `+76/+78`;
- texture-matrix R/S/T at `+90`.

For portable glTF, a non-identity `+6` transform on the bound base texture is written through `KHR_texture_transform`.

For Blender, supported identity-matrix projection layers receive dedicated generated UV maps. If a material-level layer has an unresolved projection code or non-identity `+90` matrix, the pipeline does not discard its placement: it falls back to the imported source UV while still applying the confirmed U/V scale, offset and crop. This makes texture tiling/placement progressively useful without converting an unresolved transform direction into fake source data.

## Model-local textures versus material layers

DSC relation code 400 and code 401 are intentionally kept separate:

- **code 400:** model-local projected texture state;
- **code 401:** ordered material texture layers.

The supplied scenes often use both on the same model, and the texture objects are commonly different. Blender therefore exposes mapped model-local texture nodes per object and keeps the existing ordered material-layer stack.

A model-local base texture is automatically connected only when the material does not already have a Base Color texture stack. When both model-local and material-level textures contribute, both are preserved and mapped, but their final cross-scope blend order is not invented until that relationship is better proven.

## Current priorities

Work should proceed in this order:

1. expand projected UV coverage across ordinary asset models and validate generated Blender UV maps;
2. recover non-identity `+90` direction for the 133 material-level code-401 records that need it;
3. identify code-401 projection types 7 and 8;
4. reconstruct the model-local/material-layer composition rules;
5. recover remaining repeat/alternate/swap state;
6. bind textured NURBS surfaces to their actual projection state.

Historical lighting, floor reflection matching, FxDirector rendering, lens shaders and Mental Ray pixel equivalence remain secondary unless they expose an asset-format field needed by one of the items above.

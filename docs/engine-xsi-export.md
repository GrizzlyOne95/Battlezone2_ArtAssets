# Engine-ready dotXSI export

`scripts/bz2_xsi_export.py` turns a reconstructed scene bundle into a model that Battlezone II (1.3) and Battlezone: Combat Commander load directly: a text dotXSI 3.2 file (`xsi 0101txt 0032`) plus its textures. It runs automatically as the final stage of `bz2_full_extract.py` (disable with `--no-xsi`) and can be re-run on existing bundles:

```powershell
python .\scripts\bz2_xsi_export.py .\artifacts\reconstructed\<bundle> [more bundles...] --keep-going
```

Output per bundle:

```text
<bundle>/engine/<scene>.xsi        one root frame, BZ2 conventions
<bundle>/engine/<picture>.tga      one file per distinct source picture
<bundle>/engine/xsi_export.json    per-frame and per-primitive provenance
```

## Ground truth: the shipped Stasis Truck

The archive contains exactly one real game-format model, `ISDF_vehicles/PICTURES/ivstas00.xsi` (ISDF Stasis Truck, exported by Softimage for BZ2). Exporting the reconstructed `ISDF_STASISTRUCK/SCENES/version4-Stasis_Truck_t.2-0.dsc` and comparing frame by frame:

| Frame | world matrix Δ | vertex Δ | UV Δ | material |
|---|---|---|---|---|
| main_body(_1), flames, lights, links, nacelles, cockpit | 0 | 0 | 0 | identical diffuse/alpha, hardness, specular, emissive, ambient, shading |
| hp_special_1 | 1.5 | — | — | genuine revision difference (v4 moved the hardpoint) |
| hp_* hardpoints | 0 | 0 | 1.5 | game file has all-zero UVs; see below |

This establishes that the reconstruction is already in Softimage-native axes and units (no conversion is applied), that model names map to frame names by dropping the `prefix-` and `.N-0` parts, and that authored UVs are bottom-left Softimage space.

The hardpoints use a spherical code-4 material with all-zero authored UVs. The historical exporter wrote those zeros verbatim; this exporter writes the generated spherical projection instead (same rule as the Blender stage). Hardpoints are not rendered in-engine, and for visible projection-mapped meshes the generated UVs reproduce what Softimage rendered.

## Engine contract

- **One root frame.** Additional DSC roots (e.g. separately modelled hardpoints) are attached beneath the primary root — the root with the most geometry — keeping their exact world placement, as in the shipped file. Cameras, lights and mesh-less subtrees are omitted.
- **Rigid frames.** BZ2 ignores frame scale. Every world matrix is split `W = G · M` (polar decomposition); the rigid `G` becomes the frame transform and the residual scale/shear/mirror `M` is baked into that frame's vertices and (inverse-transpose) normals. Mirrored frames get reversed winding.
- **Materials.** `SI_Material` diffuse/alpha, hardness, specular, emissive and ambient come from the decoded `.mtr`. Shading type comes from the MTR shading-model word (`1 → 0` constant, `4 → 2` phong, anchored on the game file); unanchored codes 3/5 (~1.4 % of materials) are written as phong and flagged in the glTF extras.
- **One texture per material.** The bound base layer of the ordered TEXTURES2D stack is exported; overlay/bump layers stay in the glTF/Blender reconstruction. Textures are written as `<picture>.tga` (RGB, or RGBA when the picture has alpha) and referenced by bare filename.
- **One UV set per mesh**, in Softimage bottom-left space, holding the base layer's *effective* coordinates, decided exactly as `blender_apply_bz2_asset_uvs.py` does:
  1. special material modes 7/8 → source UVs;
  2. usable authored CurrentUV → live TXMP effects (texture-matrix rotation, URepeat/VRepeat, +6 scale/offset, crop);
  3. all-zero or NURBS parameter UVs with a supported projection (codes 1–5, identity or supported rotation) → generated projection in native object space, fitted to the frame's mesh;
  4. otherwise → repeat/+6/crop composed onto the source UVs;
  5. untextured materials on a node with a model-local code-400 base projection → that projection's texture and generated UVs.
- Geometry is triangulated (glTF primitives), welded on the 6-decimal values the writer emits, and fully degenerate triangles are dropped. Line/point primitives (NURBS curves) are skipped and listed in the report.
- Every written file is re-parsed with the reference `bz2xsi` reader as a structural self-check.

## Corrections made while building this stage

- **Upside-down textures in glTF.** Every earlier stage stored raw Softimage UVs in `TEXCOORD_0`, but glTF's UV origin is top-left, so every textured glTF (and every Blender import of it) sampled textures vertically flipped. `bz2_gltf_uv_convention.py` now runs as the final reconstruction stage: `TEXCOORD_n → (u, 1 − v)` and `KHR_texture_transform` offsets become `1 − scale_v − offset_v`. JSON sidecars stay in Softimage space, and `asset.extras.bz2_uv_convention = "gltf_top_left_v1"` marks converted files (the XSI exporter handles both).
- **Blender projection axes.** Blender's glTF importer stores `(x, y, z)` as `(x, −z, y)`; the finisher generated planar/spherical/cylindrical projections on those Z-up coordinates. It now converts back to native Y-up object space first.
- **Blender finisher could not start.** `blender_finish_reconstruction.py` never ran under `blender --python` because its sibling modules were not importable; the script directory is now added to `sys.path`. Verified end-to-end with Blender 5.2.
- **Constant shading.** Constant-shaded materials (MTR shading code 1) now also get `KHR_materials_unlit` in the glTF.

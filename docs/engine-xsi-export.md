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

## Texture fidelity fixes from reviewing exports against original renders

The archive contains Softimage's own renders of several showcase scenes (`walker_final/RENDER_PICTURES/walker_final_lowres.1.pic`, `NewTank/NewTank/RENDER_PICTURES/TANK.1.pic`, `adconcept/RENDER_PICTURES/*`). Comparing exports against them exposed three reconstruction faults, now fixed for glTF, Blender and XSI where applicable.

### Picture version: exact reference first, then crop size

Artists re-rendered many pictures under the same name, so a TXMP crop rectangle (stored in the pixels of the picture it was authored on) can disagree with the picture now in the archive. Two cases, both anchored on original renders:

- **The referenced file exists at its own path** (e.g. `//Server/.../adconcept/PICTURES/tank`): that is the file Softimage loaded at render time. `NewTank/RENDER_PICTURES/TANK.1.pic` shows the current 1000×1513 `tank.pic` on a hull whose crop still says 1000×1325. Recorded as `picture_resolution: exact_reference` plus `picture_crop_mismatch`.
- **The referenced location is absent** (e.g. `//SERVER/.../ISDF/PICTURES/…`, which is not in the archive) and a basename search is required: the crop chooses among same-named copies — the path-resolved copy if it fits (±1 px), else an exact-size copy in the scene's store, else (historical ZIP scenes only) in the primary tree. `adconcept-tankstuff` crops of 590×167, 204×434 and 302×400 match the originals in `ISDF_vehicles/PICTURES/` exactly, where the other copies are 1000×283, 1000×1235 and 378×473. Recorded as `picture_resolution: crop_size_match`.

**A stale full-frame crop selects the whole current picture** (`bz2_projection_uv.effective_crop`, used by every UV path). A stale crop is one that starts at 0,0 but no longer matches the picture's size. Crops that don't start at 0,0 are real windows, clamped to the picture, with rows counted from the bottom. Measured with the render harness:
- the final walker (`tankturret1`: crop 590×167 on the 1000×283 picture) went from 0.30 to 0.49 edge alignment;
- the NewTank nose deck error went from 0.65 to 0.52.

Clamping the stale crop, or counting rows from the top, scored worse in both.

### Texture scale/offset direction

TXMP `+6` (UScale, VScale, UOffset, VOffset) was identified earlier from an identity sample, which cannot distinguish direction. Softimage scales and moves the *texture*, the counterpart of URepeat shrinking it, so lookup coordinates are `(uv·repeat − offset) / scale`. Anchor: the final NewTank hull layers select picture windows such as u 0.306–0.694 (centred) and u 0.627–1.000 / v 0.475–1.000 (ending exactly at the picture edge) under this direction and reproduce the deck of `TANK.1.pic`; the former `uv·scale + offset` tiled the whole picture about 2.7× across the deck. The window's V is measured from the picture's top row (PIC scanline order): in Softimage's bottom-up UV terms `v' = 1 − ((1 − v) − offset_v) / scale_v`. Identity windows are unchanged (so the validated Stasis UVs are unaffected) and symmetric windows give the same range either way. Anchor: the NewTank nose plate `bmerge5_default_5_1` spans exactly the window's U range (0.30–0.70 vs 0.306–0.694) over the front 27 % of the hull; only the top-down V places its window (0.741–1.0 from the top) on the emblem strip of `tank.pic` instead of the engine pods, and it also better matches the neighbouring hull's validated lookup. The crop rectangle's vertical direction has no anchor yet and is unchanged.

glTF `KHR_texture_transform`, the Blender stage and the XSI exporter all use the corrected direction.

### Single-tile UV normalization

Texture-matrix rotations (e.g. the 180° Y rotation on the NewTank hull) produce coordinates such as u −0.895…0: the same texels as 0.105…1 under wrap addressing, but nothing under clamp. The exporter shifts a primitive's UVs by whole tiles when they fit in one tile per axis; genuinely tiled layers are untouched.

### Branch texture inheritance (code 400)

A texture applied to a model in Softimage branch mode covers its whole hierarchy, but the DSC serializes the code-400 relation only on that model — the same pattern as the proven nearest-ancestor code-300 material inheritance. In the final walker, 40 of 42 untextured gun-metal meshes sit under a model owning `rusty.pic`, and the original render shows that grain on them. Mesh descendants without their own code-400 relation now inherit the nearest ancestor's projections (`binding: inherited_branch_texture`), fitted to their own geometry.

### Softimage texture blending

The shipped `ivstas00.xsi` `SI_Texture2D` tail (`blendingType 3; blending 1; ambient 0.75; diffuse 1; specular 0 …`) lines up with TXMP `+86` and the `+26` float block `[ambient, diffuse, specular, ?, ?, blending, ?, ?]`. The `+86` word, previously labelled a role candidate, is the blending type:

| +86 | Softimage blending | corpus |
|---|---|---|
| 3 | no mask — texture replaces the diffuse colour | ~9,300 |
| 2 | intensity mask — texture shows where bright | ~230 |
| 1 | alpha mask | ~40 |

A diffuse factor of 0 (e.g. `cavern`/`chrome3` on the walker's cockpit glass, modes 7/8) means the texture is a reflection map that does not drive surface colour.

The engines take one plain texture, so the XSI exporter bakes masked or scaled textures against the material's diffuse colour: `colour = lerp(material, texel × diffuse, mask × blending)`, with mask = luminance × alpha (type 2) or alpha (type 1). The walker's intensity-masked `rusty` becomes blue-grey gun-metal with bright scratches, as in the original render. Reflection-only layers are not baked; those surfaces keep their material colour. Baked files are named `<picture>_<hash>.tga` and listed with their parameters in `xsi_export.json`.

### Multi-layer bake

When a surface has more than one colour-contributing texture — an own or inherited model-local (code-400) texture plus ordered material (code-401) layers — the exporter bakes them into one texture per primitive (`scripts/bz2_texture_bake.py`):

1. stack, bottom to top: material diffuse colour, model-local textures, material layers in authored order (the cross-scope order is not serialized; this order matches the original walker render);
2. a fresh atlas: triangles are grouped into edge-connected charts split at >45° facing changes, flattened, shelf-packed with 4 px padding; atlas size (128–1024) follows the densest source layer;
3. each atlas texel samples every layer through that layer's own interpolated UVs (projection-generated or authored) and composites with the blending rules above; edges are dilated to avoid seams.

Baked files are `<frame>_<primitive>_bake.tga`; `xsi_export.json` lists each one's layer stack. `--no-bake` restores single-layer export. In the final walker, 26 parts are baked (rusty + stripes + blue glow, rusty + pipes, rusty + cavern…), giving the blue-grey gun-metal with hazard stripes seen in `walker_final_lowres.1.pic`.

### Measured render comparison

`scripts/bz2_render_compare.py` turns the renders into a regression measure. It pairs each bundle with the frame its STS `OUTPUT_FILE` wrote (`<group>/RENDER_PICTURES/<name>.<frame>.pic`, including inside `Archival.zip`), rasterizes the engine XSI through the scene's recovered camera with Softimage-style diffuse lighting (STS ambience plus each recovered light's colour × N·L), and reports:

- `edge_alignment`: gradient correlation. It validates camera and geometry first. The reference alpha cannot be used as a silhouette because mental ray's reflective floor is opaque.
- `gain_fit_error`: the relative RMS error after fitting one lighting gain per part, which measures texture colour and placement.
- `chroma_error`: brightness-independent colour error.

It also gives per-part scores and a reference | ours | error PNG.

```bash
python scripts/bz2_render_compare.py --all artifacts/reconstructed --jobs 8 --images out/compare --json out/compare.json
python scripts/bz2_render_compare.py artifacts/reconstructed/<bundle> --top-parts 12
```

Findings so far:

- **Softimage `fov_radians` is the vertical angle.** On `NewTank/TANK.1`, edge correlation peaks at the recovered camera with zero pixel offset for the vertical axis only.
- **Lighting explains colour that textures don't.** The adconcept tower is grey material under an orange (1, 0.5, 0) light. Adding the recovered lights took its error from 0.49 to 0.17 (edge alignment 0.84), and `Power_Ups/special` reaches 0.93 edge alignment.
- **Generated projection code 3 (planar YZ) maps U along Z and V along Y.** The previous `(y, z)` turned the `pluto.1` corridor walls' `cementwall` bands 90°. With `(z, y)`, edge alignment rose from 0.43 to 0.67 and error fell from 0.57 to 0.42. Code 2's `(x, z)` was confirmed by the same test: every alternative scored worse, down to 0.39.
- **Code 3 is confirmed a second time** on NewTank's gun (`bmerge14`, `turret.pic`): `(z, y)` gives a part error of 0.54, while the alternatives score 0.85–0.98.
- Corpus result (`artifacts/validation/render_compare_2026-09-26.json`): 19 of 114 bundles align with their reference render. The final NewTank went from 0.505 to 0.675 edge alignment and from 0.682 to 0.549 error.
- Of 114 bundles whose STS output file exists, only 19 actually align with it. The rest reuse an output name from another scene version (edge alignment ≈ 0), so only aligned pairs are evidence.

### Live planar projections

For planar layers (codes 1–3), the projection is regenerated live instead of using the UVs stored in the class-4 HRC (`bz2_projection_uv.prefers_live_projection`). The stored UVs are a snapshot. On NewTank's merged hull and fins they no longer match what Softimage rendered: going live took the hull's part error from 0.75 to 0.53 and the fin's from 0.99 to 0.55, restoring the grey side panels with red lights and the IS-47 markings. The other 20 aligned scenes were unchanged. Spherical and cylindrical layers keep their stored UVs, because regenerating them broke the Pluto walker (body error 0.38 → 0.92). The stored UVs remain in `scene.gltf`.

### Nodes hidden in the source render

Softimage render visibility is not decodable from the DSC, HRC, MTR or STS. `data/render_hidden_nodes.json` is a curated list of models that are demonstrably hidden in their scene's original render. The engine export omits them with reason `hidden_in_source_render`. The only entries so far are the NewTank proxy gun boxes (`Main_tank-gun`): a 48-triangle root left over from an older scene, enclosing the real twin-barrel gun. Add entries only with harness evidence.

### glTF twin

Every engine export also writes `<scene>.gltf` next to the `.xsi`. It is the same flattened model: the frame hierarchy, rigid matrices, meshes, and one baked texture per material as PNG, with V flipped to glTF's top-left convention and Softimage-native Y-up axes. Blender's glTF importer renders it through the recovered TANK.1 camera with 0.9999 coverage IoU against the harness render. glTF, Blender and FBX workflows therefore get exactly what the engine gets, while `scene.gltf` stays the source-fidelity reconstruction.

### Still open

- **Reflection maps** (special modes 7/8, diffuse factor 0, e.g. the walker visor's orange `cavern` reflection) are not reproduced. The engines' own environment/reflection material setup is the natural target.
- **Glow/luminous effects beyond texture colour** are not reproduced. This includes the walker's blue foot glow: every intensity-mask definition tested (luminance × alpha, luminance, mean RGB, max RGB) scored within noise against `walker.1`.
- **Procedural 3D textures** (`TEXTURES3D`, relation 501: `cloudy`, `clouds`, `stars`) are not evaluated. They occur in 46 of 1139 scenes, all cinematic (outros, wormhole, loading/splash screens), never in a unit or building model.
- **Poses from animated frames**: `walka.0` (box-cover walker) and `walker.1` (a later Carey revision) differ in pose, not texture.
- **Pictures absent from the archive**: 46 of 88 missing names are now filled from the shipped game's textures (`docs/retail-ground-truth.md`). The remaining 42 are Softimage library pictures or unshipped prototype art.
- **Stored UVs vs the texture matrix**: the shipped `.msh` models show that stored CurrentUVs already include the `SI_Texture2D` rotation, which is therefore no longer applied a second time (`docs/retail-ground-truth.md`).

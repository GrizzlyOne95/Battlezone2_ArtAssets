# Archive qualification research log — 2026-08-17

This log preserves reverse-engineering conclusions and qualification work derived from the original `bz2_art.7z` corpus. Raw source assets and generated reconstruction bundles are intentionally not committed; proven decoder behavior, regression tests, reproducible census tools, archive fingerprints, and normalized validation results are.

## Source identity and persistence policy

- Archive: `bz2_art.7z`
- Size: `515,756,693` bytes
- SHA-256: `d5afa754837b1a3d1217f558d1e3d110d951c0e753e6fafb15d7726e3eff96bd`
- Extracted `modelsdirectory`: `57,546` files / `1,471,037,637` bytes
- DSC scenes: `1,180` (`1,139` primary + `41` isolated historical scenes from `Archival.zip`)

The raw 1.47-GB source tree is intentionally excluded from Git. Google Drive folder `Battlezone2_ArtAssets_Source` was prepared as a possible source-asset home, but the connected Drive upload surface cannot ingest arbitrary local container paths; no claim is made that the raw tree was uploaded there.

Every non-reproducible result from the local qualification session is instead preserved in Git as production code, regression tests, reusable census tooling, normalized JSON validation artifacts, or this log. Generated glTF/bin/PNG/sidecar bundles, profiling logs, and temporary extracted ZIP trees remain uncommitted because they are reproducible and often contain sandbox-local absolute paths.

## Archive-backed production fixes

### Nested ZIP classification

Embedded ZIPs are historical scene roots only when they contain a DSC beneath a `SCENES` path. Ordinary picture/render ZIPs such as `ISDF_vehicles/PICTURES/isdf.zip` and `wormhole_seq/RENDER_PICTURES/wormhole_renders.zip` are recorded as `ignored_non_scene_archive`.

### Explicitly unbound class-4 geometry

Slot-zero-only class-4 geometry with no direct or inherited DSC code-300 material relation retains the explicit unbound placeholder. Partial or nonzero unresolved authored material mappings remain validation failures.

### Class-4 local SRT recovery

Across `34,308` class-4 nodes, archive-backed envelope work reduced unresolved local SRTs from `3,122 -> 1,179 -> 698` without substituting identity transforms.

A separate real-corpus bug in `MIRE_BUILDINGS/MODELS/data_transfer-terrain__h.4-0.hrc` showed why material-slot markers must be enumerated with overlapping matches. Bytes inside the final negative translation float of `obj3_1_2` resemble a short slot record; a consuming regex skipped the genuine marker one byte later. A zero-width lookahead recovers the serialized SRT: scale `(1,1,1)`, zero rotation, translation approximately `(4.0275e-07, 0, -8.0000763)`. A regression test preserves this exact failure class.

### Missing source pictures

All `14,486` primary `TEXTURES2D` TXMP records decode. `2,370` references point to picture basenames whose bytes are absent from the supplied corpus. These remain explicit source-completeness warnings; source paths and projection state are preserved and no image is guessed or cross-bound.

### Rolling batch checkpoints

`bz2_full_extract.py` checkpoints `batch_reconstruction.json` after every processed scene, including failures. Long `--all --keep-going` runs therefore retain exact progress when an external sandbox/CI/user interruption stops the process.

## HRC hierarchy reversal

### Global zero-run baseline ambiguity

HRC preorder depth is encoded relative to a file-specific even zero-run baseline. Multiple baselines can produce mathematically valid preorder walks; choosing the smallest valid baseline can flatten genuine parent/child chains.

DSC MODELS-to-MODELS relation code 110 independently serializes the model-parent graph. Scene reconstruction therefore scores each already-valid HRC baseline against uniquely mapped code-110 edges and changes the standalone default only when one alternative is a unique strict improvement. Code 110 is never used to post-hoc reparent individual nodes.

Full-corpus result: `1,180` scenes / `3,815` declared roots / `3,496` unique root HRC members, with `123` roots where DSC uniquely selects a better baseline.

### Class-0 subtype distinction

The final hierarchy residuals showed that only class-0/subtype-0 records are transform/null hierarchy nodes. Primary-HRC census across `7,665` HRCs found `33,769` class-0/subtype-0 records but only `13` class-0/nonzero signatures total: subtype 1 x4, subtype 18 x4, subtype 20 x4, subtype 80 x1.

Those 13 records (`cls0`, `Face`, and `t`) are internal/helper payload signatures. Their generic immediate-nine-float interpretations are structural/subnormal garbage. Filtering them from the model tree naturally resolves the Rocket Tank cantilever parents and the movie-soldier `smerge44 -> torus9` chain.

After DSC-backed baseline selection plus the class-0/subtype-0 rule, the full split-range census reports **zero mapped parent mismatches, zero unresolved HRC parents, and zero remaining hierarchy-violation roots across all 1,180 scenes / 3,815 roots**. No post-hoc reparenting is required.

## DSC ENVIRONMENT root SRT grammar

A full archive census exposed a scene-instance transform bug independent of HRC hierarchy. Every declared root has an ENVIRONMENT SRT, but the parser used to require an `MPRFLG` token after the nine SRT floats.

Archive counts:

- `3,815` declared root SRTs total
- old parser recovered `3,376`
- **439 root SRTs across 118 scenes were skipped**
- `331` of the skipped SRTs were non-identity

The corrected grammar parses the first nine numeric values following `SRT` up to the semicolon; `MPRFLG` is an optional trailing field, not part of the transform itself.

Two exact source cross-checks are especially strong: the class-1 Rocket Tank and movie-soldier ROOTs serialize the same nine-float local SRT in both HRC binary payload and DSC ENVIRONMENT text, including their lines without `MPRFLG`.

After this correction the two historical reference reconstructions remain count-stable while every declared root gets an explicit DSC scene-instance transform:

- Walker `ISDF-walker_final.20-0.dsc`: 7/7 roots with ENVIRONMENT SRT; `115` nodes / `78` meshes / `116` primitives / `52` materials / `34` images.
- high-res tank `hi_res-ISDF_tank.1-0.dsc`: 6/6 roots with ENVIRONMENT SRT; `31` nodes / `18` meshes / `25` primitives / `28` materials / `15` images.

## Class-1 patch binary reversal

The original ROOT-grid work decoded only a short `kind/u_count/v_count + XYZ lattice` prefix. A combined outer+nested census now validates the complete common class-1 envelope across **183 records** (`29` outer + `154` nested), with zero structural decode failures.

Surface-type-code inventory:

- code `2`: 167 records
- code `0`: 13 records
- code `3`: 3 records

Common envelope after the `N = u_count*v_count` XYZ float32 control lattice:

1. `u16 u_closed`, `u16 v_closed`
2. `f32 u_tension`, `f32 v_tension`
3. `u16 u_step`, `u16 v_step`
4. `u16 u_curve`, `u16 v_curve`
5. eight reserved zero bytes
6. `u32 approx_type`, `u16 view_dep`
7. `f32 spatial`, `f32 curv_u`, `f32 curv_v`
8. `u16 rec_min`, `u16 rec_max`, **`u16 recursion`**
9. `N` tagged-point `u16` flags
10. one zero `u16` tagged-point terminator
11. nine big-endian float32 local-SRT values

The 154 nested records were essential for disambiguating the final field widths: all 154 place a plausible SRT at `+54 + 2*N`; none do two bytes earlier. The first outer-only interpretation briefly treated `recursion` as u32, but the expanded census disproved that and the committed tool/artifact were corrected immediately.

Sixteen records contain nonzero tagged-point flags; the tag terminator is zero in all 183 records.

## Class-1 surface type 3: open uniform cubic B-spline

All three code-3 records are `5x4`, open/open, tension `0.5/0.5`, Step `3/3`. Their source structure, historical Softimage B-spline behavior, and later Autodesk surface-type numbering all point in the same direction. Production now evaluates **only this proven open code-3 profile** as a tensor-product uniform cubic B-spline using the serialized U/V Step values. Closed code-3 directions remain unsupported because no such archive example exists.

For each dimension the standard four-control-point uniform cubic B-spline basis is used. A `5x4` lattice has `2x1` cubic spans; Step 3 therefore produces a `7x4` sampled lattice (`28` vertices, `36` triangles).

The decisive target is:

`movieAssets/movie_hires/SCENES/SOLDIER_S2g-ALL_V33_Skel.1-0.dsc`

with ROOT `ALL_V12-sphere1.1-0`. Before the evaluator this scene reached the special-geometry stage and stopped on the unsupported class-1/subtype-3 ROOT. With the type-3 evaluator it completes every Python reconstruction stage:

- hierarchy: 0 parent mismatches / 0 unmapped parents
- special ROOT geometry: no unsupported class-1 root
- final: **797 nodes / 201 meshes / 201 primitives / 143 materials / 1 image**
- `65` explicit missing-picture source warnings
- materials, texture layers, MTR, cameras/lights, model projections, UV provenance, and FxDirector all complete

The generated B-spline patch has `28` vertices, `36` triangles, and zero zero-area triangles. Exact validation is preserved in `artifacts/validation/class1_bspline_root_validation_2026-08-17.json`.

### Class-1 type 2 remains deliberately conservative

Code 2 strongly corresponds to Cardinal surfaces and many source records contain closed directions plus Step 10 tessellation, but production still uses the historical direct control-cage approximation for type 2. That approximation does **not** honor source closure or Cardinal interpolation. It is retained explicitly rather than silently replacing it with an unproven endpoint/periodic rule.

The next class-1 fidelity task is to validate Cardinal open-boundary/phantom-control behavior and periodic closed-direction wrapping across several source profiles before changing type-2 output.

## Reference and stratified reconstruction evidence

- Historical final Walker: `Archival.zip::walker_final/SCENES/ISDF-walker_final.20-0.dsc` — 115 / 78 / 116 / 52 / 34 (nodes / meshes / primitives / materials / images).
- Historical high-resolution tank: `Archival.zip::adconcept/SCENES/hi_res-ISDF_tank.1-0.dsc` — 31 / 18 / 25 / 28 / 15.
- 22-scene cross-family reduced-tessellation sample: **22/22 successful**, with 26 explicit missing-picture warnings.
- movie high-resolution Rocket Tank after hierarchy/class-0/SRT fixes: **64 nodes / 20 meshes / 20 primitives / 6 materials / 3 images**.
- movie soldier after type-3 B-spline ROOT support: **797 nodes / 201 meshes / 201 primitives / 143 materials / 1 image**.

## Durable artifacts, tools, and tests

Validation artifacts:

- `artifacts/validation/full_archive_qualification_2026-08-17.json`
- `artifacts/validation/hierarchy_baseline_census_2026-08-17.json`
- `artifacts/validation/class0_subtype_census_2026-08-17.json`
- `artifacts/validation/environment_srt_census_2026-08-17.json`
- `artifacts/validation/class1_patch_census_2026-08-17.json`
- `artifacts/validation/class1_bspline_root_validation_2026-08-17.json`

Reusable tooling includes `scripts/bz2_hierarchy_census.py` and `scripts/bz2_class1_patch_census.py`. Regression tests cover the overlapping class-4 SRT anchor, class-0 helper exclusion, DSC-backed hierarchy baseline selection, ENVIRONMENT SRT lines with and without MPRFLG, rolling batch checkpoints, class-1 patch/tag/SRT layout, and type-3 B-spline sampling.

At production source commit `93b16cd68e2083c2ebe1295dd0ef5da5705b013d`, GitHub Pipeline Tests compiled the Python tree and passed **21/21 tests**; the runtime bundle artifact uploaded successfully.

## Deliberately uncommitted scratch outputs

Local profiling logs, generated glTF/bin/PNG/sidecar bundles, temporary historical-ZIP extractions, and one-off census intermediates are derived/reproducible outputs and are intentionally excluded from Git. Their unique conclusions have been normalized into the durable artifacts/tools above. The raw source archive/tree likewise remains outside Git.

## Current format frontier

Highest-value remaining work now includes:

1. exact class-1 code-2/Cardinal interpolation and closed-boundary semantics;
2. the remaining `698` unresolved class-4 local-SRT envelopes;
3. material-level non-identity code-401 texture-matrix direction/composition and code-400/code-401 interaction;
4. alternate/swap/wrap semantics and exact special modes 7/8;
5. textured NURBS projection binding;
6. renderer-specific Mental Ray/FxDirector behavior;
7. Blender finishing on a machine with Blender installed.

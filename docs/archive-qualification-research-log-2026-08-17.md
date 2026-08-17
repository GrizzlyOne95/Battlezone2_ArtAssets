# Archive qualification research log — 2026-08-17

This log preserves reverse-engineering conclusions and qualification work derived from the original `bz2_art.7z` corpus. Raw source assets and generated reconstruction bundles are intentionally not committed; proven decoder behavior, regression tests, reproducible census tools, archive fingerprints, and normalized validation results are.

## Source identity and persistence policy

- Archive: `bz2_art.7z`
- Size: `515,756,693` bytes
- SHA-256: `d5afa754837b1a3d1217f558d1e3d110d951c0e753e6fafb15d7726e3eff96bd`
- Extracted `modelsdirectory`: `57,546` files / `1,471,037,637` bytes
- Primary HRCs: `7,665`
- DSC scenes: `1,180` (`1,139` primary + `41` isolated historical scenes from `Archival.zip`)

The raw source tree is intentionally excluded from Git. Google Drive folder `Battlezone2_ArtAssets_Source` was prepared as a possible source-asset home, but the connected Drive upload surface cannot ingest arbitrary local container paths; no claim is made that the raw tree was uploaded there.

Every non-reproducible result from the local qualification session is preserved in Git as production code, regression tests, reusable census tooling, normalized JSON validation artifacts, or this log. Generated glTF/bin/PNG/sidecar bundles, profiling logs, and temporary ZIP extractions remain uncommitted because they are reproducible and often contain sandbox-local paths.

## Archive-backed production fixes

### Full extraction/orchestration

`scripts/bz2_full_extract.py` is the archive-to-scene entry point. It supports extracted directories, ZIP/7z sources, safe persistent source caching, historical embedded-ZIP isolation, non-scene ZIP classification, selector/list/all modes, clean per-scene output, `--keep-going`, explicit source-completeness warnings, optional Blender finishing, and `batch_reconstruction.json`.

Long runs checkpoint `batch_reconstruction.json` after every processed scene, including failures, so an external timeout or cancellation does not discard the batch history.

### Explicitly unbound class-4 geometry

Slot-zero-only class-4 geometry with no direct or inherited DSC code-300 material relation retains the explicit unbound placeholder. Partial or nonzero unresolved authored mappings remain validation failures.

### Missing source pictures

All `14,486` primary `TEXTURES2D` TXMP records decode. `2,370` references point to picture basenames whose bytes are absent from the supplied corpus. They remain explicit source-completeness warnings; source paths/projection state are preserved and no picture is guessed or cross-bound.

## HRC hierarchy reversal

HRC preorder depth is encoded relative to a file-specific even zero-run baseline. Several baselines can be mathematically valid and the smallest can flatten genuine chains. DSC MODELS-to-MODELS relation code 110 independently serializes model-parent relations, so reconstruction scores only already-valid HRC baselines against uniquely mapped code-110 edges and changes the standalone default only when one alternative is a unique strict improvement. Code 110 is never used to post-hoc reparent individual nodes.

Corpus result: `1,180` scenes / `3,815` declared roots / `3,496` unique root HRC members, with `123` roots where DSC uniquely selects a better baseline.

Only class-0/subtype-0 records are transform/null hierarchy nodes. Primary-HRC census found `33,769` class-0/subtype-0 records and only `13` class-0/nonzero helper signatures (subtype 1 x4, 18 x4, 20 x4, 80 x1). Filtering those helper records naturally resolves the Rocket Tank cantilevers and movie-soldier `smerge44 -> torus9` chain.

After baseline selection plus the class-0/subtype-0 rule, the full census reports **0 mapped parent mismatches, 0 unresolved HRC parents, and 0 hierarchy-violation roots**. No post-hoc reparenting is required.

## DSC ENVIRONMENT root SRT grammar

Every declared root has an ENVIRONMENT SRT, but the original parser required an `MPRFLG` token after the nine SRT floats. That skipped `439` root SRTs across `118` scenes; `331` were non-identity. The corrected grammar parses the first nine numeric values following `SRT`; `MPRFLG` is optional trailing syntax.

Exact cross-checks include the class-1 Rocket Tank and movie-soldier roots, whose nine-float HRC-local SRTs match DSC ENVIRONMENT text even on lines without `MPRFLG`.

Historical reference counts remain stable while every declared root receives an explicit scene-instance SRT:

- Walker `ISDF-walker_final.20-0.dsc`: `115` nodes / `78` meshes / `116` primitives / `52` materials / `34` images; 7/7 root SRTs.
- high-res tank `hi_res-ISDF_tank.1-0.dsc`: `31` / `18` / `25` / `28` / `15`; 6/6 root SRTs.

## Class-1 patch binary reversal

A combined outer+nested census validates a common class-1 envelope across **183 records** (`29` outer + `154` nested), with zero structural decode failures. Surface-type-code counts are code 0: `13`, code 2: `167`, code 3: `3`.

After the `N = u_count*v_count` XYZ float32 control lattice, the common envelope contains U/V closure, U/V tension, U/V Step, U/V curve flags, approximation settings, `u16 recursion`, `N` tagged-point `u16` flags, a zero tag terminator, then the nine-float local SRT. The 154 nested records were essential to prove the recursion/tag boundary; all place the SRT at `+54 + 2*N` and none do two bytes earlier.

### Surface type 3 — open uniform cubic B-spline

All three code-3 records are `5x4`, open/open, tension `0.5/0.5`, Step `3/3`. Production evaluates this corpus-proven open profile as a tensor-product uniform cubic B-spline. Closed code-3 directions remain unsupported because the archive has no example.

The movie-soldier target `movieAssets/movie_hires/SCENES/SOLDIER_S2g-ALL_V33_Skel.1-0.dsc` now completes every Python stage: **797 nodes / 201 meshes / 201 primitives / 143 materials / 1 image**, with 65 explicit missing-picture warnings. Its class-1 ROOT is sampled from 5x4 controls to 7x4 vertices (`28` vertices / `36` triangles), with zero degenerate triangles.

### Surface type 2 — zero-tension Cardinal

All **167/167** code-2 records in the archive have U/V tension `0.0`. Their nine source profiles cover open/open, open/closed, and closed/closed surfaces at Step 3 or 10. Production now evaluates exactly those profiles as zero-tension cubic Cardinal (Catmull-Rom form):

- open direction: `N-3` spans, four-control window `i..i+3`, interpolating control `i+1 -> i+2`; first/last controls are tangent controls; sample count `(N-3)*Step+1`;
- closed direction: `N` periodic spans, control window `i-1,i,i+1,i+2` modulo N; sample count `N*Step`; the seam is topologically closed without a duplicate seam sample.

All 167 type-2 records evaluate finite; all 27 outer type-2 roots produce zero degenerate triangles. Nonzero type-2 tension remains unsupported because no source record establishes the historical tension convention.

Representative end-to-end scenes remain stable after Cardinal promotion:

- movie Rocket Tank: `64 / 20 / 20 / 6 / 3` (nodes / meshes / primitives / materials / images)
- historical final Walker: `115 / 78 / 116 / 52 / 34`
- historical high-res tank: `31 / 18 / 25 / 28 / 15`

Exact corpus/evaluator evidence is preserved in `artifacts/validation/class1_cardinal_validation_2026-08-17.json`.

## Class-4 local SRT reversal — complete for the supplied primary corpus

Class-4 local-SRT recovery is now **34,308 / 34,308 resolved, 0 unresolved, 0 identity substitutions**.

The historical progress numbers need careful interpretation:

- initial unresolved: `3,122`
- after zero-padded-tail work: `1,179`
- after variants 5/6, the tree probe alone reported `698`
- the already-published exporter fallback independently resolved `694` of those `698` through non-unit material-slot anchors
- therefore the true production remainder was only `4`, not 698
- the final shared-probe consolidation and four evidence-backed envelopes resolve all four

The shared tree probe now enumerates overlapping material-slot signatures for authored slots 1..255. Zero-width lookahead is necessary because bytes in the last SRT float can resemble a shorter slot signature and hide the genuine marker from a consuming regex.

Final class-4 SRT-source counts across the 34,308 nodes:

- `pre_mesh_material_block`: 20,157
- `pre_mesh_material_slot_block`: 2,477
- `pre_mesh_standard_tail`: 8,681
- `pre_mesh_standard_tail_zero_padded`: 1,939
- `pre_mesh_short_tail`: 565
- `pre_mesh_standard_tail_variant_6`: 399
- `pre_mesh_standard_tail_variant_5`: 81
- `pre_mesh_short_tail_zero_padded`: 4
- `pre_mesh_mire_grid_extended_tail_zero_padded`: 2
- `pre_mesh_standard_tail_zero_unit`: 1
- `pre_mesh_standard_tail_variant_6_zero_padded`: 1
- `pre_custom_attribute_cusa`: 1

The final four residuals are independently corroborated:

1. `ISDF_vehicles/MODELS/Artillery-DUMMYROOT.3-0.hrc`, `RIGHT_GUN`: translation `(-1.397568, 1.378649, -2.727489)` behind a standard-tail variant whose usual unit field is zero. `LEFT_GUN` in the same HRC is already decoded at `(+1.397568, 1.378649, -2.727489)`, making the transform an exact X mirror.
2. `MIRE_BUILDINGS/MODELS/mbruin4-grid3.3-0.hrc`, `grid3`: identity SRT before the extended Mire-grid tail; version `.5` has the identical envelope/SRT and versions `.1/.2` carry the ordinary zero-padded standard envelope with the same identity.
3. `MIRE_BUILDINGS/MODELS/mbruin4-grid3.5-0.hrc`, `grid3`: same validated extended envelope and identity SRT.
4. `movieAssets/animatics/MODELS/scene3_shot12-explode1.1-0.hrc`, `explode1`: SRT `(scale 0.996..., translation Y 0.6020126)` exactly 36 bytes before the validated CUSA preamble. It exactly matches model 32 in `pluto-scene3_shot12.3-0.dsc`. All 40 corpus CUSA blocks place a plausible SRT at this same boundary; 39 are class-2 effect records and this is the sole class-4 case.

Validation after the final rules:

- complete primary class-4 census: **34,308/34,308** resolved
- Artillery `DEFEND-Artillery.3-0.dsc`: **26 nodes / 24 meshes / 24 primitives / 1 material / 0 images**, no reconstruction failure
- animatics `pluto-scene3_shot12.3-0.dsc`: **54 / 42 / 73 / 44 / 47**, no reconstruction failure
- Mire grid `.3` and `.5` direct HRC exports: zero unresolved class-4 SRTs

The authoritative machine-readable record is `artifacts/validation/class4_srt_complete_validation_2026-08-17.json`.

## Reference / stratified reconstruction evidence

- Historical final Walker: `115 / 78 / 116 / 52 / 34`.
- Historical high-res tank: `31 / 18 / 25 / 28 / 15`.
- 22-scene cross-family reduced-tessellation sample: **22/22 successful**, 26 missing-picture warnings.
- movie Rocket Tank: **64 / 20 / 20 / 6 / 3**.
- movie soldier: **797 / 201 / 201 / 143 / 1**.
- Artillery final-four SRT target: **26 / 24 / 24 / 1 / 0**.
- animatics CUSA target: **54 / 42 / 73 / 44 / 47**.

## Durable artifacts, tools, and CI

Validation artifacts include:

- `artifacts/validation/full_archive_qualification_2026-08-17.json`
- `artifacts/validation/hierarchy_baseline_census_2026-08-17.json`
- `artifacts/validation/class0_subtype_census_2026-08-17.json`
- `artifacts/validation/environment_srt_census_2026-08-17.json`
- `artifacts/validation/class1_patch_census_2026-08-17.json`
- `artifacts/validation/class1_bspline_root_validation_2026-08-17.json`
- `artifacts/validation/class1_cardinal_validation_2026-08-17.json`
- `artifacts/validation/class4_srt_complete_validation_2026-08-17.json`

Reusable tools include `scripts/bz2_hierarchy_census.py` and `scripts/bz2_class1_patch_census.py`. The production decoders themselves now carry the corpus-backed class-1 and class-4 behavior, with source-independent regression coverage.

At source commit `8139d9bb0e083d450ada78dfef7b8544c27a0cb7`, GitHub Pipeline Tests run `32063562299` completed successfully:

- Python compilation: pass
- regression suite: **26/26 pass**
- runtime bundle upload: pass
- runtime artifact ID: `9299016304`
- runtime artifact SHA-256: `02ea216391aae4a8547e57ff11167016e555d737719b748d3de2cb29b46db70e`

## Deliberately uncommitted scratch outputs

Local profiling logs, generated glTF/bin/PNG/sidecar bundles, temporary historical-ZIP extractions, and one-off intermediates are derived/reproducible outputs and are intentionally excluded from Git. Their unique conclusions are normalized into the durable artifacts/tools above. The raw source archive/tree likewise remains outside Git.

## Current format frontier

Highest-value remaining work now includes:

1. material-level non-identity code-401 texture-matrix direction/composition and exact code-400/code-401 interaction;
2. alternate/swap/wrap texture semantics and exact special modes 7/8;
3. textured NURBS projection binding;
4. class-1 profiles outside the supplied corpus, including nonzero type-2 tension and closed type-3 behavior, if encountered;
5. renderer-specific Mental Ray/FxDirector behavior;
6. Blender finishing on a machine with Blender installed.

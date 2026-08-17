# Archive qualification research log — 2026-08-17

This log preserves reverse-engineering conclusions and qualification work derived from the original `bz2_art.7z` corpus. Raw source assets and generated reconstruction bundles are intentionally not committed; proven decoder behavior, regression tests, tools, fingerprints, and normalized validation results are.

## Source identity

- Archive: `bz2_art.7z`
- Size: `515,756,693` bytes
- SHA-256: `d5afa754837b1a3d1217f558d1e3d110d951c0e753e6fafb15d7726e3eff96bd`
- Extracted `modelsdirectory`: `57,546` files / `1,471,037,637` bytes
- Discovered DSC scenes: `1,180` (`1,139` primary + `41` isolated historical scenes from `Archival.zip`)

## Persistence policy

The raw source dump is not suitable for Git. Generated glTF/PNG/sidecar reconstruction bundles are reproducible and remain ignored. Every non-reproducible conclusion from the qualification session is instead preserved as one or more of:

- production decoder/orchestration code;
- regression tests;
- reusable corpus-census tooling;
- normalized JSON validation artifacts;
- this research log.

Google Drive was prepared as a potential raw-asset destination, but the connected Drive upload surface cannot ingest arbitrary local container paths. No claim is made that the 57,546-file source tree was uploaded there.

## Archive-backed production fixes

### Nested ZIP classification

The source tree contains ordinary picture/render ZIPs in addition to historical scene archives. Embedded ZIPs are now treated as historical scene roots only when they actually contain a DSC beneath a `SCENES` path. Known non-scene archives include `ISDF_vehicles/PICTURES/isdf.zip` and `wormhole_seq/RENDER_PICTURES/wormhole_renders.zip`.

### Explicitly unbound class-4 geometry

Slot-zero-only class-4 geometry with no direct or inherited DSC code-300 material relation is preserved with the explicit unbound placeholder rather than rejected. Partial or nonzero unresolved authored material mappings remain failures.

### Class-4 SRT envelopes

Corpus analysis expanded local-SRT recovery to zero-padded standard/short tails and two high-frequency tail variants. Across 34,308 class-4 nodes, unresolved local SRTs fell from 3,122 to 1,179 and then to 698 without identity substitution.

A separate real-corpus failure showed that the material-slot anchor regex must enumerate overlapping matches. In `MIRE_BUILDINGS/MODELS/data_transfer-terrain__h.4-0.hrc`, the low bytes of the final negative translation float resemble a short slot record and a consuming regex skipped the genuine marker one byte later. Zero-width lookahead recovers the serialized SRT for `obj3_1_2`: scale `(1,1,1)`, zero rotation, translation approximately `(4.0275e-07, 0, -8.0000763)`. A synthetic regression test preserves this exact failure mode.

### Missing source pictures

All 14,486 primary `TEXTURES2D` TXMP records decode. 2,370 references point to historical picture basenames whose bytes are absent from the supplied corpus. Those are source-completeness warnings, not decoder failures; paths/projection state are preserved and no image is guessed or cross-bound.

### Rolling batch checkpoints

Long `--all --keep-going` corpus runs can exceed external execution windows. `bz2_full_extract.py` now checkpoints `batch_reconstruction.json` after every processed scene, including failures, so an interrupted run retains exact progress and diagnostics.

## HRC hierarchy reversal

### Global zero-run baseline ambiguity

HRC preorder depth is encoded relative to a file-specific even zero-run baseline. Several baselines can be mathematically valid for the same record stream; choosing the smallest valid baseline can flatten real chains.

DSC MODELS-to-MODELS relation code 110 independently serializes model-parent relationships. The correct reconstruction rule is therefore to score each already-valid HRC baseline against uniquely mapped code-110 edges and select a non-default baseline only when it is one unique strict improvement. Code 110 is not used to post-hoc reparent nodes.

Corpus result: across all 1,180 scenes / 3,815 declared roots / 3,496 unique root HRC members, code 110 uniquely selects a better baseline 123 times.

### Class-0 subtype distinction

The final two hierarchy residuals revealed that the newer tree probe had generalized class 0 too far. The older header classifier already labels only class-0/subtype-0 as `transform_node`.

Primary-HRC census:

- 7,665 HRCs
- 76,578 discovered records before the correction
- 33,769 class-0/subtype-0 records
- only 13 class-0/nonzero records total: subtype 1 x4, subtype 18 x4, subtype 20 x4, subtype 80 x1

Those 13 are internal/helper payload signatures, not model transform nodes. Their generic immediate nine-float interpretations are structural/subnormal garbage.

Known examples:

- movie Rocket Tank: four `cls0` subtype-1 records; filtering them naturally makes `LCANTILEVER_4` and `LCANTILEVER_3` children of `lfin_1` and `lfin_2` as DSC specifies;
- movie soldier: `Face` subtype-80 between `smerge44` and `torus9`; filtering it naturally makes `torus9` a child of `smerge44`;
- two soft-soldier HRCs: eight `t` signatures with subtypes 18/20 embedded among class-10 records.

After excluding class-0/nonzero records from the hierarchy stream, a full split-range census (scenes 1–1050 and 1051–1180) reports zero remaining hierarchy violations. No post-hoc reparenting is required.

The Rocket Tank then completes full reduced-tessellation Python reconstruction: 64 nodes / 20 meshes / 20 primitives / 6 materials / 3 images. The movie soldier also clears every hierarchy constraint and progresses to a separate unsupported class-1/subtype-3 ROOT geometry case (`ALL_V12-sphere1.1-0`).

## Reference reconstruction evidence

- Historical final Walker: `Archival.zip::walker_final/SCENES/ISDF-walker_final.20-0.dsc` — 115 nodes / 78 meshes / 116 primitives / 52 materials / 34 images.
- Historical high-resolution ISDF tank: `Archival.zip::adconcept/SCENES/hi_res-ISDF_tank.1-0.dsc` — 31 nodes / 18 meshes / 25 primitives / 28 materials / 15 images.
- 22-scene stratified cross-family sample: 22/22 Python reconstructions successful after the earlier archive-backed fixes, with 26 explicit missing-picture source warnings.

## Durable artifacts and tools

- `artifacts/validation/full_archive_qualification_2026-08-17.json`
- `artifacts/validation/hierarchy_baseline_census_2026-08-17.json`
- `artifacts/validation/class0_subtype_census_2026-08-17.json`
- `scripts/bz2_hierarchy_census.py`
- HRC/tree/material/full-driver regression tests under `tests/`

## Deliberately uncommitted scratch outputs

Local qualification produced profiling logs, temporary extracted historical ZIP trees, intermediate glTF/bin files, reconstructed PNGs, and scene bundles. These are derived/reproducible outputs, often containing absolute sandbox paths, and are intentionally excluded from Git. Their findings have been normalized into the artifacts above before cleanup or sandbox loss.

## Next format frontier

With zero-run baseline ambiguity and class-0/nonzero hierarchy helpers resolved, the movie soldier scene exposes a distinct special-geometry gap: outer/ROOT class-1 subtype 3 (`ALL_V12-sphere1.1-0`) is currently rejected because the special ROOT stage only emits proven class-1 primitive-kind-2 lattices. This should be reverse-engineered independently rather than weakened into an identity/placeholder geometry guess.

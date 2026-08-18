# Full extraction pipeline

`bz2_full_extract.py` is the production-oriented wrapper around the BZ2 Softimage reconstruction stack. It accepts the original source archive or an extracted tree, discovers scenes and their source scope, runs the proven reconstruction stages, records failures/warnings, and can optionally finish the result in Blender.

The driver does not replace the format decoders. Its purpose is to make the complete archive-to-scene path safe, repeatable, and auditable.

## Source preparation

The driver accepts:

- an extracted tree or `modelsdirectory`;
- ZIP input;
- the original `.7z` archive.

For directories it locates the effective `modelsdirectory`. ZIP extraction rejects path traversal. `.7z` extraction uses `py7zr` when installed or falls back to `7z`, `7zz`, or `7za`; normal Windows 7-Zip install paths are also checked.

`--cache-dir` enables a persistent top-level extraction cache. The cache marker records source path, size, `mtime_ns`, and extraction method. A non-empty cache directory is cleared only when it already contains this tool's ownership marker, so an arbitrary directory can never be silently destroyed.

## Scene discovery and source isolation

Scenes are discovered from:

```text
**/SCENES/*.dsc
```

The scene prefix is inferred from the path before `SCENES`, removing a fragile manual argument while preserving prefix-scoped HRC/MTR/TXMP/PIC lookup.

Historical ZIPs embedded in the source tree are isolated rather than flattened into the primary source. Only ZIPs that actually contain DSCs beneath a `SCENES` path are treated as historical scene archives; ordinary picture/render ZIPs are recorded as `ignored_non_scene_archive`.

Historical scenes use qualified selectors such as:

```text
Archival.zip::walker_final/SCENES/ISDF-walker_final.20-0.dsc
```

A short basename is accepted only when unique. Ambiguous revisions fail selection and print their qualified alternatives instead of cross-binding similarly named assets.

Use `--no-embedded-zips` when only the primary tree should be searched.

## Selection modes

- `--list-scenes` performs source preparation/discovery only.
- repeated `--scene` selects exact or uniquely resolvable scenes.
- repeated `--match` performs case-insensitive substring matching, or glob matching when the expression contains wildcard syntax.
- `--all` selects every discovered DSC.

## Reconstruction stages

For each selected scene the driver invokes `bz2_reconstruct_scene.reconstruct()`, which currently composes:

1. complete DSC multi-root assembly;
2. polygon and supported rational NURBS geometry;
3. specialized ROOT geometry;
4. complete-scene source material binding;
5. ordered material texture layers;
6. corrected MTR semantics;
7. cameras and lights;
8. model-local code-400 texture projections;
9. UV provenance;
10. FxDirector metadata;
11. SETUP_SOFT/Mental Ray render state;
12. final Blender handoff sidecars.

### HRC hierarchy disambiguation

Some binary HRCs admit more than one mathematically valid zero-run hierarchy baseline. Standalone HRC probing remains conservative and keeps its existing default.

When a DSC scene is available, relation code 110 independently serializes the model-parent graph. Multi-root scene assembly scores each valid HRC baseline against those DSC parent edges and changes the baseline only when there is one unique, strictly better DSC-backed candidate. Equivalent/ambiguous scores keep the standalone default.

This prevents the earlier failure mode where choosing the shallowest valid HRC tree could flatten genuine parent/child chains.

### Explicitly unbound source meshes

A class-4 mesh that uses only slot 0 and has no direct or inherited DSC code-300 material relation is preserved as explicitly unbound source geometry. The placeholder is retained and recorded rather than inventing a material. Nonzero unresolved slots and partial authored mappings remain validation failures.

### Missing source pictures

A TXMP/material relation can be structurally valid even when the external picture bytes referenced by the historical source path are absent from the archive.

Missing picture files therefore produce explicit source-completeness warnings rather than aborting an otherwise valid reconstruction. The raw source path and recovered TXMP/projection state remain in the sidecars; the pipeline does not guess, substitute, or cross-bind an image from another source group/revision.

Structural texture failures, such as an unresolved glTF material relationship, remain fatal.

## Clean output and render-state totality

Each requested scene maps to a deterministic sanitized output directory. That generated directory is removed before reconstruction by default so stale sidecars cannot make a failed rebuild look complete. `--preserve-output` opts out for deliberate debugging.

When a DSC contains no resolved `SETUP_SOFT`, the full driver creates:

```json
{
  "schema": "bz2-render-state-placeholder-v1",
  "status": "not_authored",
  "note": "DSC scene contains no resolved SETUP_SOFT record"
}
```

This keeps the Blender input contract total while distinguishing an unauthored render setup from a missing pipeline output.

## Batch behavior

Without `--keep-going`, processing stops after the first failed scene. With `--keep-going`, failures are recorded and later scenes continue; the command still exits nonzero if any scene failed.

`batch_reconstruction.json` records source provenance, selector/prefix, output path, elapsed time, reconstruction counts, source-warning details, optional Blender status, and any failure type/message.

## Blender mode

`--blender` resolves Blender from `PATH`; `--blender <path>` uses an explicit executable.

The Blender finisher imports the glTF, restores recovered camera/light state, rebuilds the currently proven ordered texture stack, adds supported projection UV maps without overwriting authored UVs, retains source projection/material metadata, preserves render-state provenance, and saves `scene.blend`.

A nonzero Blender result converts that scene to a batch failure.

## Source-independent CI

GitHub Actions compiles `scripts/` and `tests/` under Python 3.12 and runs the unittest suite. Current coverage includes archive/source routing, selection/ambiguity, cache ownership, ZIP traversal protection, output cleanup, render-state placeholders, explicit unbound materials, class-4 SRT tail variants, missing-picture warning propagation, and DSC-backed HRC hierarchy-baseline selection.

## Real `bz2_art.7z` qualification — August 17, 2026

The original archive was supplied and exercised directly against this branch. The derived machine-readable record is committed as:

```text
artifacts/validation/full_archive_qualification_2026-08-17.json
```

### Archive/discovery preflight

- source archive: 515,756,693 bytes;
- extracted `modelsdirectory`: 57,546 files, approximately 1.6 GB;
- discovered scenes: **1,180** total;
  - 1,139 primary scenes;
  - 41 isolated historical scenes from `Archival.zip`;
- **1,180/1,180 DSCs parsed successfully**;
- no scene lacked a declared root;
- no ambiguous root match was found during the structural preflight.

The primary source has one known completeness boundary: 14 `ISDF_outro` scenes contain 100 root-model references for which the matching HRC is not present in that source group. Those references are deliberately not rebound to similarly named assets from other groups/revisions.

### Class-4 SRT census

Across 7,665 HRCs there are 34,308 class-4 nodes.

Archive-backed decoder fixes reduced unresolved class-4 local SRTs from:

```text
3,122 -> 1,179 -> 698
```

The first reduction came from recognizing standard/short post-mesh tails followed only by zero padding. Additional exact tail variants recovered another 481 transforms. These changes recover serialized source SRT values; they do not replace missing transforms with identity.

### Texture-source census

All **14,486/14,486** primary `TEXTURES2D` TXMP records decoded successfully. **2,370** reference picture basenames whose image bytes are absent from the supplied corpus. Those are now represented as source warnings, not decoder failures.

### Exact reference scenes

The historical final Walker reconstructs end-to-end:

```text
Archival.zip::walker_final/SCENES/ISDF-walker_final.20-0.dsc
115 nodes / 78 meshes / 116 primitives / 52 materials / 34 images
```

The historical high-resolution ISDF tank also reconstructs end-to-end:

```text
Archival.zip::adconcept/SCENES/hi_res-ISDF_tank.1-0.dsc
31 nodes / 18 meshes / 25 primitives / 28 materials / 15 images
```

### Stratified full reconstruction

A 22-scene cross-section was run through the complete Python reconstruction path at reduced NURBS tessellation (`curve_steps=8`, `surface_steps_u=6`, `surface_steps_v=6`). It covers ISDF/Scion vehicles, creatures, buildings, power-ups, multiplayer props, HUD/interface assets, planets, movie assets, outro cinematics, wormhole sequences, foliage, and sky domes.

Result:

```text
22 / 22 successful
0 reconstruction failures
26 source-picture warnings
```

The source-picture warnings are concentrated in five scenes and correspond to historical picture files absent from the archive; no substitute image data was invented.

Three initially failing hierarchy cases were specifically repaired by DSC-backed baseline selection:

- `Power_Ups/SCENES/spawnpoint_network-pspwn_1.1-0.dsc` — baseline 24 -> 26;
- `Multiplayer/SCENES/loot-streetlight.7-0.dsc` — baseline 24 -> 26;
- `movieAssets/movie_terrain/SCENES/pluto-scene3_shot7_2.6-0.dsc` — two roots, baseline 20 -> 22.

All three then completed the remaining reconstruction stages successfully.

### Qualification limits

This qualification does **not** claim that all 1,180 scenes have received full glTF reconstruction. Every DSC received structural/source preflight, while the full reconstruction pass was stratified across 22 representative scenes plus the exact Walker/tank references.

Blender is not installed in the qualification sandbox, so `.blend` finishing was not exercised here. The portable glTF/sidecar path is qualified; Blender finishing remains a local-user release check.

## Recommended local commands

List exact selectors once and reuse the persistent extraction cache:

```powershell
python .\scripts\bz2_full_extract.py .\bz2_art.7z `
  --cache-dir .\.bz2-source-cache `
  --list-scenes
```

Run a selected scene through Blender:

```powershell
python .\scripts\bz2_full_extract.py .\bz2_art.7z `
  --cache-dir .\.bz2-source-cache `
  --scene "<selector copied from --list-scenes>" `
  --blender
```

For a local full-corpus attempt:

```powershell
python .\scripts\bz2_full_extract.py .\bz2_art.7z `
  --cache-dir .\.bz2-source-cache `
  --all `
  --keep-going `
  --output .\artifacts\reconstructed
```

## Remaining fidelity frontier

The pipeline is operational without claiming every original Softimage/Mental Ray behavior is solved. The largest remaining fidelity items are:

- material-level non-identity texture-matrix direction/composition;
- exact interaction between simultaneous code-400 and code-401 texture projections;
- authoritative alternate/swap/wrap behavior where non-default;
- exact equivalents for special material projection/environment modes 7 and 8;
- textured NURBS projection binding;
- the remaining 698 unresolved class-4 local-SRT envelopes;
- renderer-specific Mental Ray, lens-shader, reflection and FxDirector effects.

These should continue as additive fidelity stages rather than blocking already understood geometry/material/texture extraction.

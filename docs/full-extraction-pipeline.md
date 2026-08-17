# Full extraction pipeline

`bz2_full_extract.py` is the production-oriented wrapper around the scene reconstruction stack. Its job is not to replace the individual format decoders; it makes the existing proven stages safe and repeatable from the original archive through a portable glTF bundle and, optionally, a native Blender scene.

## Goals

The full driver is designed around several failure modes that are easy to miss when running the lower-level scripts manually:

- the raw archive may have one or more wrapper directories around `modelsdirectory`;
- a DSC needs the correct scene-local prefix for HRC/MTR/TXMP/PIC lookup;
- historical ZIPs inside the dump can contain same-named assets from different revisions;
- a batch should record one bad scene without losing the results from every good scene;
- stale output from an earlier run must not make a partial reconstruction appear complete;
- scenes do not necessarily author `SETUP_SOFT`, but Blender finishing still needs a total input contract;
- repeatedly unpacking the source archive is expensive, but a cache must never silently destroy unrelated files.

## Source preparation

The driver accepts a directory, ZIP, or 7z source.

For directories it searches for the effective `modelsdirectory` root. If the supplied directory is itself `modelsdirectory`, it uses it directly. If no directory with that name exists but the supplied tree contains `SCENES/*.dsc`, that tree is accepted as the source root.

ZIP input uses Python's standard `zipfile` module. Every member is checked before extraction and the run is rejected if a member would escape the destination directory.

7z input uses `py7zr` when available. Otherwise it resolves an external `7z`, `7zz`, or `7za` executable; Windows' normal 7-Zip install paths are checked as a fallback. `--7zip` overrides automatic discovery.

## Persistent source cache

`--cache-dir` avoids re-extracting the top-level source archive for every run.

The cache marker stores:

- absolute source path;
- source size;
- source `mtime_ns`;
- extraction method.

The source is re-extracted when the signature changes or `--refresh-cache` is supplied.

Safety rule: a non-empty cache directory is cleared only if it already contains this tool's ownership marker. An arbitrary non-empty directory passed to `--cache-dir` is rejected rather than deleted.

## Scene discovery and prefix inference

The driver searches each prepared source root for:

```text
**/SCENES/*.dsc
```

For a scene such as:

```text
walker_final/SCENES/ISDF-walker_final.20-0.dsc
```

the inferred source prefix is:

```text
walker_final
```

For deeper historical trees the complete path before `SCENES` is retained, for example:

```text
Archival/NewTank/NewTank
```

This removes a fragile manual argument from the normal reconstruction path while preserving the prefix-scoped lookup behavior used by the lower-level decoders.

## Embedded historical ZIP isolation

The original archive contains nested ZIPs with historical/revision assets. They are not simply unpacked into the primary tree.

Each embedded ZIP is instead extracted to a separate temporary source root and its DSCs are reconstructed against that root. This prevents a scene from resolving a same-basename source object from the primary tree or another archived revision.

An embedded scene therefore receives a qualified selector:

```text
relative/archive.zip::relative/scene/SCENES/example.1-0.dsc
```

A short basename is accepted only when unique. If multiple revisions contain the same DSC basename, the driver fails selection and prints the qualified alternatives instead of choosing one arbitrarily.

Use `--no-embedded-zips` to restrict discovery to the primary tree.

## Selection modes

`--scene` selects an exact/unique scene and may be repeated.

`--match` performs case-insensitive substring matching, or glob matching when the expression contains `*`, `?`, or `[]`.

`--all` selects every discovered DSC.

`--list-scenes` performs source preparation and discovery only, returning each selector, relative path, inferred prefix, and source label.

## Reconstruction stages

For each selected DSC the driver invokes `bz2_reconstruct_scene.reconstruct()`. That pipeline currently composes the already validated stages for:

1. complete DSC multi-root assembly;
2. polygon and supported rational NURBS geometry;
3. specialized ROOT geometry;
4. complete-scene source material binding;
5. ordered material texture layers;
6. corrected MTR semantics;
7. cameras and lights;
8. model-local texture projections;
9. UV provenance;
10. FxDirector metadata;
11. SETUP_SOFT/Mental Ray render state;
12. the final Blender handoff sidecars.

The asset-fidelity layer then provides the currently proven projection operators and texture repeat/placement/crop behavior without overwriting authored polygon UVs.

## Clean output rule

Every requested scene maps to a deterministic sanitized output directory under the batch root.

Before reconstruction that generated scene directory is removed by default. This is intentional: a failed stage must not leave an old `scene.gltf`, sidecar, or `scene.blend` that looks like current output.

`--preserve-output` disables this behavior for deliberate debugging only.

## Render-state totality

The original one-scene pipeline writes `scene.render_state.json` only when it resolves a `SETUP_SOFT` record. The Blender finishing script, however, consumes a render-state sidecar as part of its normal argument contract.

The full driver closes that gap. When a scene has no resolved render setup it writes a small explicit placeholder:

```json
{
  "schema": "bz2-render-state-placeholder-v1",
  "status": "not_authored",
  "note": "DSC scene contains no resolved SETUP_SOFT record"
}
```

This distinguishes "no render setup authored/resolved" from "pipeline forgot to create a required file" and keeps the archive-to-Blender path structurally complete.

## Failure behavior

Without `--keep-going`, the batch stops after the first failed selected scene.

With `--keep-going`, each failed scene is recorded and later scenes continue. The process still returns a nonzero exit code if the final batch contains any failure.

`batch_reconstruction.json` records:

- requested and processed scene counts;
- success and failure counts;
- total elapsed time;
- each scene selector and source root;
- inferred prefix;
- output directory;
- elapsed scene time;
- reconstruction counts for successful scenes;
- error type/message for failed scenes;
- optional Blender invocation result.

This makes a corpus run auditable rather than dependent on terminal scrollback.

## Blender mode

`--blender` resolves Blender from `PATH`; `--blender <path>` uses an explicit executable.

After a successful portable reconstruction the driver runs `blender_finish_reconstruction.py`, which:

- imports the final glTF;
- restores the recovered source camera and lights;
- rebuilds the currently proven ordered texture stack;
- applies additive projection UV maps where supported;
- keeps source UVs intact;
- retains source projection and material metadata as Blender custom properties;
- preserves render-state provenance;
- saves `scene.blend`.

A nonzero Blender result converts that scene to a batch failure.

## Validation layers

There are two deliberately separate validation layers.

### Source-independent orchestration tests

`tests/test_full_extract.py` tests the driver without proprietary assets. Coverage includes discovery, prefix inference, selection, ambiguity handling, embedded ZIP isolation, cache ownership/signature behavior, ZIP traversal rejection, clean-output behavior, per-scene source routing, and render-state placeholder generation.

These tests run in GitHub Actions along with Python bytecode compilation.

### Real corpus validation

Format fidelity still requires the original BZ2 source corpus. The individual reconstruction stages already contain derived fixtures and corpus evidence committed throughout the reconstruction stack, but a final archive-to-batch release check should be run locally against `bz2_art.7z` because the raw archive is intentionally excluded from Git.

A useful final qualification sequence is:

```powershell
python .\scripts\bz2_full_extract.py .\bz2_art.7z --list-scenes --cache-dir .\.bz2-source-cache
python .\scripts\bz2_full_extract.py .\bz2_art.7z --scene "ISDF-walker_final.20-0.dsc" --cache-dir .\.bz2-source-cache --blender
python .\scripts\bz2_full_extract.py .\bz2_art.7z --match "tank" --keep-going --cache-dir .\.bz2-source-cache
```

Then expand to `--all --keep-going` once the representative walker/tank scenes are clean.

## Remaining fidelity frontier

The full pipeline is intended to make extraction/reconstruction operational even while a few source-renderer semantics remain under active reversal. The largest known fidelity items are currently:

- material-level non-identity texture-matrix direction/composition;
- exact interaction between simultaneous model-local and material-level texture projections;
- authoritative alternate/swap/wrap behavior where non-default;
- exact equivalents for special material projection/environment modes 7 and 8;
- textured NURBS projection binding;
- renderer-specific Mental Ray, lens-shader, reflection and FxDirector effects.

Those should continue as additive fidelity stages rather than blocking reliable geometry/material/texture extraction for already understood assets.

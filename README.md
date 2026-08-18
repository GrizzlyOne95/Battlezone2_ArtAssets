# Battlezone 2 Art Assets

Preservation and reconstruction tooling for the original Battlezone II Softimage-era art sources.

The repository intentionally excludes the raw source dump. It contains reverse-engineering notes, Python decoders/exporters, Blender reconstruction helpers, regression fixtures, derived validation data, and selected modern exports.

## Full reconstruction pipeline

The primary entry point is now:

```powershell
python .\scripts\bz2_full_extract.py <source> [selection options]
```

`<source>` may be:

- an extracted `modelsdirectory` tree;
- a directory containing `modelsdirectory`;
- a ZIP archive;
- the original `bz2_art.7z` archive.

For `.7z`, the driver uses `py7zr` when installed, otherwise a local 7-Zip executable (`7z`, `7zz`, or `7za`). On Windows it also checks the normal `Program Files\7-Zip\7z.exe` locations. An explicit executable can be supplied with `--7zip`.

The driver discovers DSC scenes, infers each scene prefix automatically, isolates historical scenes found inside embedded ZIP archives, runs the validated reconstruction stack, and writes a batch manifest with success/failure information for every requested scene.

### Inspect the archive first

```powershell
python .\scripts\bz2_full_extract.py .\bz2_art.7z --list-scenes --cache-dir .\.bz2-source-cache
```

Using a cache is recommended for repeated work with the large source archive. The cache is signature-checked against the source archive and carries an ownership marker so the tool will not silently delete an unrelated non-empty directory.

### Reconstruct one scene

A full relative path is the safest selector:

```powershell
python .\scripts\bz2_full_extract.py .\bz2_art.7z `
  --scene "walker_final/SCENES/ISDF-walker_final.20-0.dsc" `
  --cache-dir .\.bz2-source-cache
```

A unique DSC basename also works:

```powershell
python .\scripts\bz2_full_extract.py .\bz2_art.7z `
  --scene "ISDF-walker_final.20-0.dsc" `
  --cache-dir .\.bz2-source-cache
```

If the basename exists in more than one source/revision, the driver refuses to guess and prints the qualified selectors.

### Select groups or all scenes

```powershell
python .\scripts\bz2_full_extract.py .\bz2_art.7z --match "walker" --keep-going --cache-dir .\.bz2-source-cache
```

```powershell
python .\scripts\bz2_full_extract.py .\bz2_art.7z --all --keep-going --cache-dir .\.bz2-source-cache
```

`--match` accepts a case-insensitive substring or glob. `--keep-going` is useful for corpus runs because one unsupported historical scene will be recorded as a failure without stopping the remainder of the batch.

### Historical ZIP revisions

ZIP archives embedded inside `modelsdirectory` are extracted into isolated temporary source roots. This prevents a scene from accidentally resolving same-named HRC/MTR/TXMP/PIC files from a different historical revision.

Their selectors are qualified as:

```text
relative/archive.zip::relative/scene/SCENES/example.1-0.dsc
```

Use `--no-embedded-zips` when only the primary extracted tree should be scanned.

### Finish in Blender

If Blender is on `PATH`:

```powershell
python .\scripts\bz2_full_extract.py .\bz2_art.7z `
  --scene "ISDF-walker_final.20-0.dsc" `
  --cache-dir .\.bz2-source-cache `
  --blender
```

Or pass the executable explicitly:

```powershell
--blender "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
```

The Blender finishing stage imports the reconstructed glTF, restores the recovered camera/light setup, builds the proven texture-layer stack, generates additive projection UV maps where supported, preserves source UVs, applies known repeat/scale/offset/crop state, stores unresolved Softimage/Mental Ray state as custom properties, and saves `scene.blend`.

Scenes without an authored/resolved `SETUP_SOFT` record now receive an explicit `scene.render_state.json` placeholder, so the Blender handoff does not fail merely because render setup metadata was absent.

## Per-scene output

By default reconstructed scenes are written beneath:

```text
artifacts/reconstructed/
```

A scene bundle contains, as applicable:

```text
scene.gltf
scene.bin
textures/
scene.scene.json
scene.texture_layers.json
scene.model_textures.json
scene.uv_provenance.json
scene.fx.json
scene.render_state.json
reconstruction.json
blender_command.txt
reports/
scene.blend                 # when --blender is used
```

The batch root additionally contains:

```text
batch_reconstruction.json
```

Generated per-scene output is cleaned before reconstruction by default so stale sidecars cannot make a failed run look complete. Use `--preserve-output` only when intentionally retaining prior output.

## What the reconstruction stack currently restores

The current stacked pipeline includes:

1. DSC multi-root scene assembly and hierarchy validation;
2. class-4 polygon geometry, including multi-contour polygons;
3. class-9/class-10 rational NURBS geometry where the topology is proven;
4. specialized ROOT class-1 grid geometry;
5. original material-slot binding and inheritance;
6. corrected Softimage MTR scalar/material semantics;
7. ordered material-level TEXTURES2D layers;
8. original PIC texture conversion;
9. cameras, lights and interest-object reconstruction;
10. model-local code-400 texture projections;
11. source UV/projection provenance;
12. recovered repeat, scale, offset and crop state;
13. projected UV generation for the proven planar/spherical/cylindrical operator set;
14. FxDirector scene-control metadata;
15. SETUP_SOFT / Mental Ray render-state preservation;
16. Blender reconstruction and asset-fidelity finishing.

The source reconstruction remains deliberately conservative where Softimage behavior has not yet been proven. In particular, special material projection modes, some non-identity texture matrices, exact environment/reflection behavior, some NURBS projection binding, and renderer-specific Mental Ray/FxDirector effects remain explicit metadata rather than guessed equivalents.

See `docs/full-extraction-pipeline.md` and `docs/asset-fidelity-roadmap.md` for the current boundaries and next fidelity work.

## Lower-level tools

The one-scene reconstruction API remains available when the source is already prepared and the exact prefix is known:

```powershell
python .\scripts\bz2_reconstruct_scene.py `
  ".\modelsdirectory\SomeAsset\SCENES\example.1-0.dsc" `
  ".\modelsdirectory" `
  "SomeAsset" `
  ".\artifacts\reconstructed\example"
```

Earlier extraction/research entry points remain useful for targeted reverse engineering:

- `scripts/bz2_extract.py`
- `scripts/bz2_reconstruct_scene.py`
- `scripts/bz2_txmp_corpus_census.py`
- `scripts/blender_finish_reconstruction.py`
- `scripts/blender_import_bz2.py`

## Tests

Source-independent orchestration tests can be run without the proprietary source archive:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

GitHub Actions also compiles the Python tree and runs these tests on pushes to `main`, `agent/**` branches, and pull requests.

A true release-quality corpus validation still requires a local copy of the original `bz2_art.7z`; raw source assets are intentionally not committed to this repository.

## Included derived output roots

- `artifacts/extracts/images/`
- `artifacts/extracts/hrc_mesh_like/`
- `artifacts/extracts/scene_exports/`
- `artifacts/extracts/xsi/`
- `artifacts/validation/`

## Excluded on purpose

- raw dump content under `modelsdirectory/`;
- `bz2_art.7z` and extraction caches;
- original `.dsc`, `.hrc`, `.mtr`, `.ani`, `.pic`, `.xsi`, and related source files;
- generated full reconstruction bundles under `artifacts/reconstructed/`;
- bulky intermediate reports and temporary data.

See `EXTRACTION_NOTES.md` for the broader format/reversal history.

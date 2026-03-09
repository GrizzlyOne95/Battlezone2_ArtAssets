# BZ2 art dump extraction notes

This workspace is not a normal source repo. It is a Softimage-era asset dump with a mix of:

- Plain text `dotXSI` scenes (`.xsi`)
- Plain text scene descriptors and setup files (`.dsc`, `.sts`, `.shd`, `.chn`)
- Binary Softimage/BZ2 object chunks (`.hrc`, `.mtr`, `.ani`, `.cam`, `.lig`, `.cls`, `.t3D`, `.txt`, `.pic`)

## What is currently automated

Use:

```powershell
python scripts\bz2_extract.py full
```

Outputs go under `artifacts/`.

`full` is incremental for the heavy stages: it reuses existing `artifacts/reports/images.json`
and `artifacts/reports/xsi_exports.json` by default instead of reconverting every `.pic`
and re-exporting every text `.xsi`. If you need to force those heavy passes again, use:

```powershell
python scripts\bz2_extract.py full --refresh-heavy
```

To export a reconstructed binary `.dsc` scene test bundle as OBJ/MTL with linked PNG textures,
use:

```powershell
python scripts\bz2_extract.py scene-export modelsdirectory/MIRE/SCENES/MIRE-puff_plant.2-0.dsc
```

That writes a scene bundle under `artifacts/extracts/scene_exports/<scene-name>/` with:

- one combined OBJ scene
- one MTL file
- copied PNG textures
- `scene.json` manifest listing exported and still-missing scene objects

Scene export directories are keyed by relative source scene path, not only basename, so
variants with the same scene stem do not overwrite each other.

For Blender-side inspection of the recovered assets, use:

```powershell
blender --python scripts\blender_import_bz2.py -- --xsi-all
blender --python scripts\blender_import_bz2.py -- --scene modelsdirectory/Archival/adconcept/SCENES/adconcept-mirescene.1-0.dsc
blender --python scripts\blender_import_bz2.py -- --scene modelsdirectory/Archival/adconcept/SCENES/adconcept-mirescene.1-0.dsc --picture-planes
```

That script imports the real recovered glTF exports for the text `.xsi` assets
and builds collection/placeholder structure for `.dsc` scenes so scene
organization can be inspected in Blender even when binary `.hrc` geometry is
still unresolved.

The current pass does several practical things:

1. Inventories the dump and writes extension and folder counts.
2. Parses every `.dsc` scene file into JSON so scene membership and root nodes are searchable.
3. Resolves `.dsc` scene dependencies into expected asset paths where possible.
4. Parses `.dsc` relation graphs so model-to-material and material-to-texture links can be reconstructed.
5. Converts every recoverable text `.xsi` scene into `.obj` + `.mtl` + `.gltf` + `.bin` + `.json`.
6. Converts standalone source images and archived render images into `.png`.
7. Parses `.exp` files, `.ani` animation model references, and `.txt` texture-map objects into searchable JSON reports.
8. Classifies `.hrc` payload headers and extracts a stable float window from `.mtr` files for further reverse engineering.
9. Decodes most class-4 `mesh_like` `.hrc` polygon lists, including per-corner normals and UVs, and exports them to OBJ for Blender inspection.

It also fingerprints unresolved binary families and extracts texture-map source paths from `.txt` files where possible.

Current extracted totals:

- `60,512` files inventoried.
- `1,180` `.dsc` scene descriptors parsed.
- `15,150` `.txt` texture-map objects parsed.
- `6` recoverable text `.xsi` scenes exported.
- `6,532` standalone images and `647` archive images converted to `.png`.
- `7` image files still fail decode or crash the OpenImageIO reader and are logged in `artifacts/reports/images.json`.

## Current format status

- `.xsi`: usable now through the community parser in `tools/io_scene_bz2xsi`.
- `.pic`: mostly readable with OpenImageIO and convertible to `.png`; a small number are malformed or crash the decoder, so image conversion now runs each file in an isolated worker process.
- `.dsc`: text, straightforward to parse for chapter membership and scene roots.
- `.dsc`: text, and now partially reconstructed beyond membership. `RELATIONS` blocks are parsed so scene-level model/material/texture hookups are available in `artifacts/reports/scene_dependencies.json`.
- `.txt`: not image pixels; appears to be a binary texture-map object with `TXMP` markers and source picture paths.
- `.hrc`, `.mtr`, `.ani`, `.cam`, `.lig`, `.cls`, `.t3D`: still unresolved binary chunks. They now have aggregated signatures in `artifacts/reports/binary_signatures.json`.
- `.hrc`: now has a useful header-level classifier in `artifacts/reports/hrc_headers.json`. That report separates transform-like nodes, mesh-like nodes, spline-like nodes, and other payload families. For `transform_node` entries, the first stable float block now appears to map to inferred scale, Euler rotation, and translation hints.
- `.mtr`: now has a partial float-window decoder in `artifacts/reports/binary_materials.json`. The stable float block is confirmed; the `likely_fields` names are still inferred rather than fully proven.
- class-4 `mesh_like` `.hrc`: now has a substantially better geometry report in `artifacts/reports/hrc_mesh_like.json`. The vertex block is confirmed, most files now parse a counted polygon list, and `5,165` binary meshes currently export to OBJ under `artifacts/extracts/hrc_mesh_like`. Those OBJ files now carry recovered `vt` UV coordinates and `vn` normals when present. The Blender helper prefers those decoded OBJ files so imported binary meshes retain that mapping data.

For scene import in Blender, the helper now reads the `.dsc` relation graph from `scene_dependencies.json` and applies recovered scene texture links to decoded binary HRC meshes when the source picture PNG is already available. A concrete example is `modelsdirectory/MIRE/SCENES/MIRE-puff_plant.2-0.dsc`, which now resolves model-to-material-to-texture chains such as:

- `puff_plant-leafset_1.1-0 -> puff_plant-mat50.1-0 -> puff_plant-t2d17.1-0 -> MIRE/PICTURES/GELTREE`
- `puff_plant-leafset_2.1-0 -> puff_plant-mat52.1-0 -> puff_plant-t2d19.1-0 -> MIRE/PICTURES/BLEAF`
- `puff_plant-red_cover__2.1-0 -> puff_plant-mat49.1-0 -> puff_plant-t2d18.1-0 -> MIRE/PICTURES/branches`

For `modelsdirectory/ISDF_SCAVENGER/SCENES/UTILITY-Scavenger_t.8-0.dsc` and
`version2-Scavenger_t.4-0.dsc`, scene export now also injects inferred surrogate
components for missing named parts by splitting connected components from the decoded
`Scavenger_t-obj2.1-0` mesh:

- `Scavenger_t-mudflap.1-0` uses `obj2` component `2`
- `Scavenger_t-obj3_2_1.2-0` uses `obj2` component `3`
- `Scavenger_t-cockpit.1-0` uses `obj2` component `8`

Those surrogates are called out in each exported `scene.json` manifest under
`inferred_override` and `inferred_component_rank`.

## Practical next step

The highest-value next reverse engineering target is `.hrc`, because `.dsc` scene files reference those model chunks heavily and they appear to contain the actual geometry for most of the dump. After that, `.mtr` and `.ani` are the key follow-on targets for material assignment and animation recovery.

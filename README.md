# Battlezone 2 Art Assets

Derived Battlezone 2 prerelease art exports for modern inspection and reuse.

This repository intentionally excludes the raw Softimage-era source dump. It keeps:

- extraction notes and documentation
- Python extraction and Blender helper scripts
- the community `io_scene_bz2xsi` tool snapshot under `tools/`
- converted image assets as `.png`
- decoded mesh exports as `.obj`
- scene exports as `.obj` / `.mtl` and on-demand `.gltf` / `.bin` with textures, decoded binary material parameters, cameras, lights, and unresolved NURBS-like placeholders when recovered

## Included output roots

- `artifacts/extracts/images/`
- `artifacts/extracts/hrc_mesh_like/`
- `artifacts/extracts/scene_exports/`
- `artifacts/extracts/xsi/`

## Excluded on purpose

- raw dump content under `modelsdirectory/`
- original `.dsc`, `.hrc`, `.mtr`, `.ani`, `.pic`, `.xsi`, and related source files
- bulky intermediate reports under `artifacts/reports/`
- generated `.gltf` / `.bin` exports, which stay untracked by default

## Main scripts

- `scripts/bz2_extract.py`
- `scripts/blender_import_bz2.py`

Useful report commands:

- `python scripts\bz2_extract.py full`
- `python scripts\bz2_extract.py scene-export modelsdirectory/MIRE/SCENES/demo01-waterfall.8-0.dsc`
- `python scripts\bz2_extract.py nurbs-usage`

See `EXTRACTION_NOTES.md` for workflow and reconstruction notes.

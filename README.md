# Battlezone 2 Art Assets

Derived Battlezone 2 prerelease art exports for modern inspection and reuse.

This repository intentionally excludes the raw Softimage-era source dump. It keeps:

- extraction notes and documentation
- Python extraction and Blender helper scripts
- the community `io_scene_bz2xsi` tool snapshot under `tools/`
- converted image assets as `.png`
- decoded mesh and scene exports as `.obj` / `.mtl` with texture files

## Included output roots

- `artifacts/extracts/images/`
- `artifacts/extracts/hrc_mesh_like/`
- `artifacts/extracts/scene_exports/`
- `artifacts/extracts/xsi/`

## Excluded on purpose

- raw dump content under `modelsdirectory/`
- original `.dsc`, `.hrc`, `.mtr`, `.ani`, `.pic`, `.xsi`, and related source files
- bulky intermediate reports under `artifacts/reports/`
- non-OBJ exports such as `.gltf` / `.bin`

## Main scripts

- `scripts/bz2_extract.py`
- `scripts/blender_import_bz2.py`

See `EXTRACTION_NOTES.md` for workflow and reconstruction notes.

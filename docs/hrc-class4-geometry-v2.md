# HRC class-4 geometry v2

Validation against the supplied `bz2_art.7z` archive closes the remaining structural geometry gaps for Softimage|3D class-4 polygon meshes.

## Proven class-4 payload

Outer `HRCH` class-4 roots and nested `00 01 <name>` class-4 records use the same polygon-mesh payload after the class/subtype fields:

1. float32 parameter field;
2. u32 vertex count;
3. `vertex_count` vertex records, 14 bytes each (`XYZ float32` + u16 tail);
4. u32 polygon count;
5. for each polygon: u16 corner-record count, 28-byte corner records, then u32 polygon metadata.

Each ordinary corner record contains:

- u32 vertex index;
- XYZ float32 normal;
- UV float32 pair;
- u32 color/attribute field.

## Corrections to the older exporter

Three earlier assumptions were too restrictive.

### Polygon size is not capped at 32 corners

The corner count is an unsigned 16-bit field. The corpus contains **46 class-4 records with at least one polygon over 32 corner records**, so `MAX_POLYGON_CORNERS = 32` rejects valid source geometry.

### All-NaN normals are a missing-normal sentinel

The corpus contains **57 corner records** whose complete normal triplet is NaN. Mixed finite/NaN normals remain invalid, but an all-NaN triplet is a deliberate missing-normal representation and must not reject the mesh.

### `0xFFFFFFFF` is a contour separator

The 62 movie-era records that previously appeared to contain out-of-range vertex indices are valid counted-polygon records. A corner record whose vertex index is `0xFFFFFFFF` separates contours inside one polygon.

The corpus contains:

- **80** contour separator records;
- **75** multi-contour polygons;
- up to **3 contours in one polygon**.

For example, a Voyager polygon with a declared 31 corner records contains ten vertices, a `0xFFFFFFFF` separator, then twenty more vertices. This is consistent with an outer contour plus an inner contour/hole rather than a corrupt vertex reference.

Multi-contour polygons are now structurally decoded, but production tessellation must use a hole-aware triangulator. They must not be flattened with a simple triangle fan.

## Full-corpus result

Running `scripts/bz2_hrc_mesh_validate_v2.py` over all direct HRC files in the archive gives:

- **7,665 HRC files** scanned;
- **34,308 class-4 records** found;
- **5,188 / 5,188 outer class-4 roots decoded**;
- **29,120 / 29,120 nested class-4 records decoded**;
- **34,308 / 34,308 total class-4 records decoded**;
- **1,646,823 vertices**;
- **1,910,192 polygons**;
- **148 transform-only class-4 records**;
- **zero structural decode failures**.

The validator reports 2,776,854 simple fan triangles only for single-contour polygons. It intentionally excludes the 75 multi-contour polygons from that triangle total until hole-aware triangulation is integrated.

## Exporter consequence

The remaining blocker for complete vehicle/building HRC export is no longer locating or decoding nested polygon geometry. The production scene path now has enough structural information to combine:

1. the complete class-4 geometry payload described here;
2. the already validated per-node class-4 SRT/hierarchy reconstruction;
3. DSC `MODELS -> MODELS` parent relation code 110;
4. material/texture relations;
5. class-9/class-10 parametric geometry.

The next production step is to emit each nested HRC class-4 node into the glTF hierarchy using its recovered local transform, while adding hole-aware triangulation for the small legacy multi-contour subset.

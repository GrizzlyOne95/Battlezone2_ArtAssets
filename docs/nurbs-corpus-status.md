# BZ2 SI3D NURBS corpus status

Validated against the complete `bz2_art.7z` source dump, including HRC members stored inside `modelsdirectory/Archival.zip`.

## Complete HRC corpus

- Direct HRC files: **7,665**
- Embedded HRC members: **256**
- Logical HRC sources scanned: **7,921**
- HRC sources with parametric data/candidates: **978**
- Decoded SI3D parametric records: **1,987**
  - NURBS curves: **730**
  - NURBS surfaces: **1,257**
- Reconstructed/exported records: **1,981**
- Deliberately unsupported records: **6**
- Trimmed surfaces: **15**
- Recovered trim loops: **46**
- Structurally rejected tag-like candidates: **19**

The six unsupported records are the same three doubly-closed soldier surfaces in two source versions: `skin33`, `skin43`, and `skin56` in `ALL_Skeleton_V13-null38.1-0.hrc` and `.2-0.hrc`. They are retained as unsupported rather than guessed because their V closure flag conflicts with an open/clamped-looking stored knot layout.

## Embedded Archival.zip

`modelsdirectory/Archival.zip` adds:

- **256** HRC files
- **41** DSC scene files
- **79** decoded parametric records
  - 4 curves
  - 75 surfaces
- **79/79** reconstruct successfully

## Reproducible extraction

Run the ordinary direct-HRC pass:

```powershell
python scripts\bz2_nurbs_extract.py modelsdirectory
```

Then process HRC members inside embedded ZIP archives into the same output tree:

```powershell
python scripts\bz2_nurbs_zip_extract.py modelsdirectory
```

The second command materializes ZIP members only in a temporary directory and writes normal open OBJ derivatives plus a separate archive report.

## Fidelity boundary

The decoded rational control points, weights, knot vectors, closure flags, parameter ranges, and UV trim curves are the preservation source of truth. OBJ output is currently a compatibility/validation derivative. Trimmed OBJ surfaces use UV face-centroid clipping, so exact trim-boundary tessellation remains future work.

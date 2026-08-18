# Texture matrix reversal — 2026-08-17

Source: supplied `bz2_art.7z`, SHA-256 `d5afa754837b1a3d1217f558d1e3d110d951c0e753e6fafb15d7726e3eff96bd`.

Reproducible evidence:

- `scripts/bz2_txmp_corpus_census.py`
- `scripts/bz2_ascii_xsi_texture_matrix_probe.py`
- `artifacts/validation/texture_matrix_census_2026-08-17.json`
- `artifacts/validation/ascii_xsi_texture_matrix_evidence_2026-08-17.json`

## Primary-corpus facts

- 14,486 TXMP records across 1,139 primary DSC scenes.
- DSC code 400: 283 edges; 229 identity +90 matrices and 54 non-identity.
- DSC code 401: 9,985 edges; 8,101 identity +90 matrices and 1,884 non-identity.
- Code 401 non-identity by projection code: 1=86, 2=275, 3=79, 4=1,363, 5=8, 6=72, 7=0, 8=1.
- Code 400 non-identity by projection code: 1=1, 2=2, 3=2, 4=42, 5=7, 6=0, 8=0.

These primary-corpus counts supersede the earlier historical-subset estimate of 133 non-identity code-401 edges.

## ASCII source evidence

The extracted archive contains only one ASCII XSI file with `SI_Texture2D` blocks: `ISDF_vehicles/PICTURES/ivstas00.xsi`. The reusable probe finds 11 blocks. Ten reference `ivstas00.pic`; nine of the 11 total matrices are identity and exactly two are non-identity:

- `Frame frm-rlink` / `Mesh rlink`, source line 3513;
- `Frame frm-rnacelle` / `Mesh rnacelle`, source line 3691.

Both serialize exactly:

```text
-1  0  0  0
 0  1  0  0
 0  0 -1  0
 0  0  0  1
```

which is `diag(-1, 1, -1, 1)`, the ordinary 4x4 matrix for a 180-degree Y-axis rotation.

This directly confirms that source-era `SI_Texture2D` objects carry authored 4x4 transform state alongside texture/projection fields. `ivstas00.xsi` also contains explicit `SI_MeshTextureCoords`, which gives a future geometry/UV comparison route even though the only non-identity source matrix is self-inverse.

## Old binary Stasis Truck correspondence

The old binary source family under `ISDF_vehicles` contains TXMP records pointing to the same `ivstas00` picture path. Eight are non-identity, all projection code 4, unit scale and zero translation:

- `Stasis_Truck_t-t2d2` revisions 1/2: Y rotation approximately `-pi`;
- `Stasis_Truck_t-t2d3` revisions 1/2: Y rotation approximately `-pi`;
- `Stasis_Truck_t-t2d9` revisions 1/2: Y rotation approximately `+pi`;
- `Stasis_Truck_t-t2d10` revisions 1/2: Y rotation approximately `+pi`.

The ASCII `diag(-1,1,-1,1)` matrices and binary `+/-pi` Y rotations therefore independently describe the same authored transform family. This substantially strengthens the interpretation of TXMP +90 as compact `SI_Texture2D` rotation/scale/translation state.

The matching old DSC revision is not present, so an exact old `t2dN` member -> ASCII `rlink`/`rnacelle` assignment is not currently provable from relation numbering alone.

## Stasis revision split

Do **not** use the later dedicated Stasis Truck scene to assign the old transformed TXMP object numbers.

`ISDF_STASISTRUCK/SCENES/version2-Stasis_Truck_t.2-0.dsc` does contain model relations for the same named parts (`rlink`, `rnacelle`, `llink`, `lnacelle`), but all 11 TXMP texture matrices in that later/dedicated revision are identity. Its relation numbering is therefore evidence for a different source revision and must not be projected backward onto the old `ISDF_vehicles` t2d2/t2d3/t2d9/t2d10 objects.

## Evidence from `GrizzlyOne95/io_scene_bz2xsi`

The Blender XSI add-on is useful for the **general XSI matrix storage convention**, but not as a texture-transform implementation.

Its import bridge converts XSI matrices with:

```python
Matrix(xsi_matrix.to_list()).transposed()
```

and its exporter performs the inverse bridge by storing Blender's local matrix after `.transposed()`. Therefore legacy XSI matrix rows are transposed relative to Blender `mathutils.Matrix` convention. Any future TXMP-to-Blender projection transform must respect this orientation boundary.

However, the add-on's `SI_Texture2D` material reader reads only the texture filename and then intentionally skips the rest of the block. Repository history was checked back to initial upload commit `7ffc09910a35065219714314ca9a8531b70590c3`; the initial parser already has the same filename-only behavior. There is no older hidden texture-matrix application algorithm to recover from this repository.

Consequently the add-on establishes a **matrix-orientation constraint**, not direct/inverse texture-transform semantics or code-400/code-401 composition order.

## Current evidence boundary

The following are now well supported:

- TXMP +90 is real authored texture-matrix S/R/T state, not padding;
- rotation values are radians;
- the old Stasis binary +/-pi-Y family corresponds to source-era ASCII 180-degree Y matrices;
- XSI matrix storage must be transposed when bridged to Blender's matrix convention;
- later Stasis t2d numbering cannot be used to infer old-revision transformed-object ownership.

The following remain unresolved and must **not** be guessed into production UVs:

- exact old-revision t2d object -> `ivstas00.xsi` mesh/material mapping;
- direct versus inverse texture-matrix application;
- general Euler construction order for asymmetric XYZ rotations;
- whether the +90 matrix transforms projection-support/object coordinates before projection or acts after projection in another space;
- code-400 versus code-401 composition order when both are present;
- behavior for special projection modes 7/8.

## Next falsification target

There is no second asymmetric ASCII `SI_Texture2D` example in the supplied archive. The next evidence route is therefore corpus-driven:

1. find binary TXMP records with asymmetric non-identity +90 state (non-pi rotation and/or non-unit scale/translation);
2. prioritize assets with explicit source/baked UVs, mirrored left/right geometry, or otherwise independently checkable texture orientation;
3. test direct/inverse and matrix-orientation candidates against those independent UV/symmetry constraints;
4. only then promote non-identity matrix application into `bz2_projection_uv.py` and add regression/corpus validation.

# Texture matrix reversal — 2026-08-17

Source: supplied `bz2_art.7z`, SHA-256 `d5afa754837b1a3d1217f558d1e3d110d951c0e753e6fafb15d7726e3eff96bd`.

Reproducible census: `scripts/bz2_txmp_corpus_census.py` and `artifacts/validation/texture_matrix_census_2026-08-17.json`.

## Primary-corpus facts

- 14,486 TXMP records across 1,139 primary DSC scenes.
- DSC code 400: 283 edges; 229 identity +90 matrices and 54 non-identity.
- DSC code 401: 9,985 edges; 8,101 identity +90 matrices and 1,884 non-identity.
- Code 401 non-identity by projection code: 1=86, 2=275, 3=79, 4=1,363, 5=8, 6=72, 7=0, 8=1.
- Code 400 non-identity by projection code: 1=1, 2=2, 3=2, 4=42, 5=7, 6=0, 8=0.

These primary-corpus counts supersede the earlier historical-subset estimate of 133 non-identity code-401 edges.

## ASCII source evidence

`ISDF_vehicles/PICTURES/ivstas00.xsi` contains 11 source-era `SI_Texture2D` blocks. Ten reference `ivstas00.pic`; eight have the identity 4x4 matrix and two have exactly `diag(-1, 1, -1, 1)`.

This directly confirms that source-era `SI_Texture2D` objects store authored non-identity 4x4 transform state alongside the texture/projection fields.

## Stasis Truck ASCII/binary correspondence

The primary binary source contains **59 TXMP records** referencing the same `ivstas00` picture family. Eight are non-identity and all are projection code 4 with unit XYZ scale and zero XYZ translation:

- `Stasis_Truck_t-t2d2` revisions 1/2: Y rotation approximately `-pi`;
- `Stasis_Truck_t-t2d3` revisions 1/2: Y rotation approximately `-pi`;
- `Stasis_Truck_t-t2d9` revisions 1/2: Y rotation approximately `+pi`;
- `Stasis_Truck_t-t2d10` revisions 1/2: Y rotation approximately `+pi`.

The ASCII matrix `diag(-1,1,-1,1)` is exactly the conventional 4x4 rotation matrix produced by a 180-degree Y-axis rotation. The ASCII and binary records therefore independently corroborate the same authored transform family and substantially strengthen the interpretation of the binary +90 rotation/scale/translation block as real `SI_Texture2D` transform state.

This still does **not** establish which specific ASCII texture block corresponds to which binary `t2dN` member, nor how Softimage applies the stored transform to generated texture coordinates.

## Still unresolved

Do not apply the non-identity binary matrices to reconstructed UV/projection output until these are proven from an ASCII/binary context match:

- exact ASCII block-to-binary TXMP member mapping;
- direct versus inverse matrix application;
- row-vector versus column-vector convention;
- Euler rotation construction order for general XYZ rotations;
- code-400 versus code-401 composition order;
- behavior for special projection modes 7/8.

The next target is to identify one texture block uniquely by picture + enclosing frame/mesh/material context in both ASCII and binary forms, then use that pair to determine transform direction and composition without guessing.

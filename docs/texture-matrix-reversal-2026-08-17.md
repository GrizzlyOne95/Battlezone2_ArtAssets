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

This directly confirms that source-era `SI_Texture2D` objects store authored non-identity 4x4 transform state alongside the texture/projection fields. It strongly corroborates the binary TXMP +90 matrix as real source semantics rather than padding.

## Still unresolved

Do not apply the non-identity binary matrices to reconstructed UV/projection output until these are proven from an ASCII/binary matched example:

- exact binary TXMP counterpart for the two non-identity Stasis Truck ASCII blocks;
- direct versus inverse matrix application;
- row-vector versus column-vector convention;
- Euler rotation construction order;
- code-400 versus code-401 composition order;
- behavior for special projection modes 7/8.

The next target is to identify one texture block uniquely by picture + mesh/material context in both ASCII and binary forms, then use that pair to determine transform direction and composition without guessing.

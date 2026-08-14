# NURBS glTF validation

The assembled HRC exporter can now be layered with `bz2_hrc_gltf_parametric.py` to attach recovered class-9/class-10 rational geometry to the same glTF hierarchy as class-4 polygon meshes.

## Mixed Voyager model

`movieAssets/movie_hires/MODELS/lowresvger-voyager.1-0.hrc` contains:

- 132 HRC hierarchy nodes;
- 72 class-4 polygon meshes;
- 31 class-10 NURBS surfaces.

The mixed export produces **103 glTF geometry objects** (72 polygon + 31 NURBS). The generated glTF was loaded through an independent scene reader with all 103 geometry instances present and finite assembled scene bounds.

`lowresvger-null2.3-0.hrc` similarly produces 32 geometry objects: 18 polygon meshes plus 14 reconstructed NURBS surfaces.

## Manual Voyager ground truth: `nurbs133`

The supplied geometry-only `voyagerlowpoly.blend` reference retains a manually polygonized `nurbs133` mesh with:

- 145 vertices;
- 112 polygons;
- bounds approximately `(-2.5249801, 0.6207400, 1.2424660)` to `(-0.4241860, 2.0377769, 2.6701059)`.

An earlier source revision, `lowresvger-null2.3-0.hrc`, preserves `nurbs133` as a true class-10 rational surface:

- 4 × 10 control points;
- cubic in U and V;
- open in U and V;
- zero trim loops.

Evaluating that source NURBS on a 5 × 29 parameter grid yields exactly **145 vertices / 112 quads**. Its untransformed bounds match the later Softimage polygonized class-4 `nurbs133` bounds to normal float32 tolerance.

The recovered class-10 local translation is approximately:

```text
(-1.2951998711, 1.0857360363, 2.1699566841)
```

Applying that source SRT to the reconstructed NURBS bounds matches the manually converted Blender bounds within roughly `5e-7` source units. This independently validates both the rational surface evaluator and the recovered object placement.

## Second cross-version case: `nurbs106`

`nurbs106` in `lowresvger-null2.3-0.hrc` is a true class-10 surface with 16 × 9 control points, cubic U/V, closed U and open V. A later polygonized revision contains 112 vertices / 96 polygons. Sampling the original surface on a 16 × 7 grid yields the same 112/96 topology and near-identical bounds; small boundary differences are consistent with Softimage's tessellation parameter spacing rather than a control-point/knot decode error.

Later revisions materially alter this object, so the final manual 12-vertex version is not an exact revision match for the earlier NURBS source.

## Near-equal repeated knot precision

A Voyager surface named `nurbs4` exposed a numerical edge case in the existing evaluator. Its source contains repeated clamped knots represented as values such as:

```text
1.3
1.3000000000000003
```

Those values are mathematically the same knot but compare unequal as binary floats. Without normalization, the basis can collapse to all zeros exactly at the endpoint.

The glTF parametric layer canonicalizes adjacent knots within a relative `1e-12` tolerance before evaluation. With this correction, all 31 parametric records in `lowresvger-voyager.1-0.hrc` and all 50 in `VGER_TRANS_V11-voyager.1-0.hrc` evaluate successfully in the stress test.

## Remaining parametric subtype

A direct class-tag stress scan finds seven records that decode sufficiently for transform recovery but are not reconstruction-ready under the current knot-layout converter:

- `All_V7_temp-nurbs106` — an old curve record with an unsupported/degenerate control layout;
- `skin33`, `skin43`, and `skin56` in each of two soldier revisions — surfaces flagged closed in V while carrying a clamped/open-layout V knot vector.

The soldier surfaces are a specific mixed closed-flag/clamped-knot subtype, not an unknown binary record. They remain intentionally unsupported for tessellation until seam/topology behavior is proven.

## Discovery improvement

The older generic NURBS probe starts from printable strings of length four or greater. HRC hierarchy records permit shorter names, including real parametric objects named `SUN` and `EAR`.

`bz2_hrc_gltf_parametric.py` starts from the structurally decoded HRC tree instead, then constructs the parametric anchor directly from the known model record. This allows short-name parametric nodes to be preserved/exported without broadening the forensic printable-string scan.

## Scope boundary

The generated NURBS surface UVs are normalized parameter-space coordinates. They preserve a useful parametric mapping but are **not** presented as original Softimage texture projection. Source material and texture binding remains an independent DSC/MTR/TXT/PIC reconstruction step.

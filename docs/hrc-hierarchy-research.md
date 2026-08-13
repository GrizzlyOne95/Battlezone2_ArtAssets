# Binary HRC hierarchy / transform research

This note records hierarchy findings that are proven strongly enough to use as regression constraints, but are not yet folded into the production scene exporter.

## Ground-truth source

`movieAssets/soft_soldier/MODELS/ALL_Skeleton_V13-null38.1-0.hrc` was compared directly with the user's historical Softimage-to-Blender conversion (`isdfsoldier.blend`). The Blender file was read from its self-describing SDNA structures, without Blender installed, so object names, parent pointers, local fields, and `obmat` values could be inspected directly.

## Named binary object records

Nested object records use a recurring binary prefix:

```text
00 01 <zero-terminated object name> <u16 class> <u16 subtype> ...
```

Known class values encountered in the soldier include:

- `0` — null/transform-style node
- `1` — primitive
- `2` — face
- `4` — polygon mesh
- `5` — joint
- `9` — NURBS curve
- `10` — NURBS surface

Class `0` and `5` nodes store nine big-endian floats immediately after the class/subtype fields:

```text
scale.x scale.y scale.z
rot.x   rot.y   rot.z
pos.x   pos.y   pos.z
```

Examples recovered from the source HRC and independently checked against the Blender conversion:

- `jnt20_2`: scale `(1,1,1)`, translation approximately `(15.257875,0,0)`
- `null1_3`: scale approximately `(0.305996,0.223511,0.223511)`, translation approximately `(6.124701,0.188152,0.250886)`

The corresponding Blender object values agree to normal import/export floating-point tolerance. Rotation may be represented differently after the legacy conversion, so the HRC values remain authoritative.

## Preorder hierarchy encoding

For real nested object records in the soldier, the run of zero bytes immediately before the next `00 01 <name>` record encodes the tree transition.

With the soldier's structural baseline of 22 bytes:

```text
20 zero bytes = descend one level (child)
22 zero bytes = same level (sibling)
24 zero bytes = ascend one level
26 zero bytes = ascend two levels
28 zero bytes = ascend three levels
...
```

Equivalently:

```text
depth_delta = (baseline_zero_run - zero_run) / 2
```

Filtering out internal non-model records whose zero run is shorter than 20 bytes, the reconstructed preorder hierarchy matched **348 / 348 uniquely identifiable Blender parent relationships** in the soldier fixture with zero mismatches.

Examples recovered correctly include:

```text
null38
└─ chn18
   └─ jnt20_1
      └─ jnt20_2
         ├─ eff18
         │  └─ ...
         ├─ null33
         ├─ nurbs106
         └─ Knee_Pad
```

and the deeper facial/skeleton hierarchy.

## Structural baseline varies by HRC

The baseline is not globally fixed at 22. Complete-corpus probing shows a small set of file-level baselines. Choosing the smallest even baseline that yields a valid preorder walk (`depth >= 1`, and a new record can descend by at most one level) produced viable walks for every tested multi-record HRC.

Observed minimum valid baselines in the direct multi-record corpus were primarily:

- `20`
- `22`
- `26`

The soldier fixture uniquely resolves to `22`. Large polygon/surface-rooted HRCs commonly resolve to `26`. This strongly suggests the baseline reflects enclosing root/primitive scopes in the serialized HRC structure rather than a change to the fundamental two-byte-per-level nesting rule.

This inference needs further cross-validation before the production exporter relies on it for every HRC.

## Scene hierarchy bug found in the existing DSC exporter

`bz2_extract.py` currently treats every `MODELS -> MODELS` DSC relation as a parent relationship. That is too broad.

The wormhole scenes contain model self-relations using relation code `260` and other procedural relations such as `251`. Treating those as parents creates false cycles. The actual parent/child relation code in the validated scenes is `110`.

When model-parent construction is restricted to relation code `110`, all **720 / 720 single-record NURBS scene references** decode and transform successfully:

- 258 NURBS surface references
- 462 NURBS curve references
- 49 references with a real DSC model parent
- 16 references sourced from embedded `Archival.zip` scenes
- 0 missing scenes
- 0 missing HRCs
- 0 decode failures
- 0 non-finite transform results

This `110` filter should be applied to the core scene exporter before broader NURBS scene integration.

## Current integration boundary

The low-risk single-record HRC path is proven. Multi-record HRCs still require authoritative internal object transforms/hierarchy before they should be flattened into production scene output. The binary hierarchy/SRT work above is the path toward eliminating that remaining restriction.

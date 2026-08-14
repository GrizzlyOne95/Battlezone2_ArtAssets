# HRC geometry-node SRT layout

This note promotes the geometry-node transform rule established during the HRC hierarchy work.

## Correction to the earlier model

Class `9` / `10` NURBS records are not necessarily transformless leaves. Blender ground-truth objects such as `nurbs105`, `nurbs114`, and `nurbs138` carry non-trivial local transforms that must be composed with the surrounding HRC hierarchy.

The transform uses the same nine big-endian floats as null/joint nodes:

```text
scale.x scale.y scale.z
rot.x   rot.y   rot.z
pos.x   pos.y   pos.z
```

For a structurally decoded NURBS record, let `decoded_end` be `decoded_through_trims` when a surface trim section was recovered, otherwise `decoded_through`.

The local SRT starts at:

```text
class 9  curve:   decoded_end + 12 bytes
class 10 surface: decoded_end + 64 bytes
```

Additional surface/material metadata follows the SRT rather than moving it.

## Corpus validation

The rule was cross-validated against the full known parametric corpus:

- direct HRC records: **1,908 / 1,908**
- HRC records inside `modelsdirectory/Archival.zip`: **79 / 79**
- total: **1,987 / 1,987**
- zero buffer overruns
- zero non-finite or implausible transform blocks

`scripts/bz2_hrc_tree_probe.py` now decodes these blocks and labels their source as `post_parametric_metadata`. Immediate class `0` / `5` transforms remain labeled `immediate_transform_payload`.

## Scene-integration consequence

The major multi-record placement blocker is no longer missing NURBS-node SRT. Remaining work is to validate the per-HRC preorder baseline, compose internal local transforms with DSC model parenting, and replace NURBS placeholders with the already-decoded/tessellated geometry.

The DSC exporter must also restrict `MODELS -> MODELS` parent edges to relation code `110`; relation codes such as `251` and `260` are not ordinary parent links and can create false cycles.

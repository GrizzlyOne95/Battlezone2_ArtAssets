#!/usr/bin/env python3
"""Annotate reconstructed glTF primitives with recovered UV provenance/status.

Class-4 HRC polygon UVs are source-authored per-corner values. Parametric NURBS
surfaces currently carry normalized surface-parameter UVs as a tessellation aid,
not source texture projection. This pass makes that distinction explicit and
marks all-zero source UV primitives that depend on model-local projection state.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path

COMPONENT_FLOAT = 5126
TYPE_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def _read_float_accessor(gltf: dict, buffer: bytes, accessor_index: int) -> list[tuple[float, ...]]:
    accessor = gltf["accessors"][int(accessor_index)]
    if int(accessor.get("componentType", 0)) != COMPONENT_FLOAT:
        raise ValueError("UV accessor is not FLOAT")
    component_count = TYPE_COMPONENTS.get(str(accessor.get("type")))
    if component_count is None:
        raise ValueError(f"unsupported accessor type {accessor.get('type')}")
    view = gltf["bufferViews"][int(accessor["bufferView"])]
    base = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    packed_size = component_count * 4
    stride = int(view.get("byteStride", packed_size))
    count = int(accessor.get("count", 0))
    values = []
    for index in range(count):
        offset = base + index * stride
        if offset + packed_size > len(buffer):
            raise ValueError("accessor overruns glTF buffer")
        values.append(struct.unpack_from("<" + "f" * component_count, buffer, offset))
    return values


def annotate(input_gltf: Path, output_gltf: Path) -> dict:
    gltf = json.loads(input_gltf.read_text(encoding="utf-8"))
    if not gltf.get("buffers"):
        raise ValueError("glTF has no buffer")
    source_buffer = input_gltf.parent / str(gltf["buffers"][0]["uri"])
    buffer = source_buffer.read_bytes()

    nodes_by_mesh: dict[int, list[dict]] = {}
    for node in gltf.get("nodes", []):
        if node.get("mesh") is not None:
            nodes_by_mesh.setdefault(int(node["mesh"]), []).append(node)

    explicit = zero = parametric = missing = failures = 0
    projected_zero = zero_without_projection = 0
    records = []

    for mesh_index, mesh in enumerate(gltf.get("meshes", [])):
        mesh_extras = mesh.get("extras") or {}
        source_kind = (
            "nurbs_parameter_space"
            if mesh_extras.get("uv_source") == "normalized_parameter_space"
            else "hrc_polygon_corner"
        )
        mesh_nodes = nodes_by_mesh.get(mesh_index, [])
        has_model_projection = any(
            bool((node.get("extras") or {}).get("bz2_model_texture_projections"))
            for node in mesh_nodes
        )
        for primitive_index, primitive in enumerate(mesh.get("primitives", [])):
            texcoord_accessor = (primitive.get("attributes") or {}).get("TEXCOORD_0")
            extras = primitive.setdefault("extras", {})
            extras["bz2_uv_source_kind"] = source_kind
            if texcoord_accessor is None:
                missing += 1
                extras["bz2_uv_status"] = "missing_texcoord0"
                records.append({"mesh": mesh.get("name"), "primitive": primitive_index, "status": "missing_texcoord0"})
                continue
            try:
                uvs = _read_float_accessor(gltf, buffer, int(texcoord_accessor))
                if not all(math.isfinite(value) for uv in uvs for value in uv):
                    raise ValueError("non-finite UV value")
                all_zero = all(abs(value) <= 1.0e-12 for uv in uvs for value in uv)
                if uvs:
                    minimum = [min(uv[axis] for uv in uvs) for axis in range(2)]
                    maximum = [max(uv[axis] for uv in uvs) for axis in range(2)]
                else:
                    minimum = maximum = [0.0, 0.0]
                extras["bz2_uv_all_zero"] = all_zero
                extras["bz2_uv_bounds"] = {"min": minimum, "max": maximum}
                extras["bz2_uv_sample_count"] = len(uvs)
                if source_kind == "nurbs_parameter_space":
                    parametric += 1
                    status = "normalized_nurbs_parameter_space_not_source_projection"
                elif all_zero and has_model_projection:
                    zero += 1
                    projected_zero += 1
                    status = "source_uv_zero_model_projection_required"
                elif all_zero:
                    zero += 1
                    zero_without_projection += 1
                    status = "source_uv_zero_no_model_projection_resolved"
                else:
                    explicit += 1
                    status = "source_explicit_polygon_uv"
                extras["bz2_uv_status"] = status
                records.append({
                    "mesh": mesh.get("name"),
                    "primitive": primitive_index,
                    "status": status,
                    "uv_min": minimum,
                    "uv_max": maximum,
                })
            except Exception as exc:
                failures += 1
                extras["bz2_uv_status"] = f"decode_failure:{type(exc).__name__}"
                records.append({
                    "mesh": mesh.get("name"),
                    "primitive": primitive_index,
                    "status": "decode_failure",
                    "error": f"{type(exc).__name__}: {exc}",
                })

    if output_gltf.parent.resolve() != input_gltf.parent.resolve():
        raise ValueError("output_gltf must remain beside the existing buffer/textures")
    output_gltf.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    summary = {
        "schema": "bz2-uv-provenance-gltf-v1",
        "input_gltf": str(input_gltf),
        "output_gltf": str(output_gltf),
        "source_explicit_polygon_uv_primitive_count": explicit,
        "source_zero_uv_primitive_count": zero,
        "zero_uv_with_model_projection_count": projected_zero,
        "zero_uv_without_model_projection_count": zero_without_projection,
        "nurbs_parameter_space_uv_primitive_count": parametric,
        "missing_texcoord0_primitive_count": missing,
        "decode_failure_count": failures,
        "records": records,
        "notes": [
            "Class-4 polygon UVs are preserved exactly from HRC per-corner source records.",
            "An all-zero class-4 TEXCOORD_0 is not treated as an authored usable unwrap when model-local projection state exists.",
            "NURBS parameter-space UVs remain explicitly distinct from recovered Softimage texture projections.",
        ],
    }
    output_gltf.with_suffix(".uv_provenance.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_gltf", type=Path)
    parser.add_argument("output_gltf", type=Path, nargs="?")
    args = parser.parse_args()
    output = args.output_gltf or args.input_gltf
    result = annotate(args.input_gltf, output)
    print(json.dumps({key: result[key] for key in (
        "source_explicit_polygon_uv_primitive_count",
        "source_zero_uv_primitive_count",
        "zero_uv_with_model_projection_count",
        "zero_uv_without_model_projection_count",
        "nurbs_parameter_space_uv_primitive_count",
        "missing_texcoord0_primitive_count",
        "decode_failure_count",
    )}, indent=2))
    return 1 if result["decode_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

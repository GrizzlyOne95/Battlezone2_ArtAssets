#!/usr/bin/env python3
"""Append renderable specialized ROOT-HRC geometry to a multi-root DSC glTF.

Proven ROOT handling currently covers class-1 surface type 2 as the historical
control-cage approximation plus class-1 surface type 3 as a uniform cubic B-spline
tensor surface. Type-3 evaluation is archive-backed by the 5x4 open/open movie
soldier patch, source Step fields, 183-record class-1 envelope census, and complete
downstream scene reconstruction. This is intentionally ROOT-only: nested class-1
records are often Softimage construction/history objects and must not be emitted
as duplicate render meshes merely because they share a class id.

No UV set is fabricated. The reference tank/walker grids are projection-mapped
through DSC/TXMP state and carry no authoritative baked texture coordinates.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path

import bz2_dsc_material_gltf as dscmat


def _outer_record(data: bytes) -> dict | None:
    marker = data.find(b"HRCH")
    if marker < 0:
        return None
    end = data.find(b"\0", marker + 4)
    if end < 0 or end + 5 > len(data):
        return None
    return {
        "name": data[marker + 4 : end].decode("latin-1", errors="replace"),
        "class_id": int.from_bytes(data[end + 1 : end + 3], "big"),
        "subtype": int.from_bytes(data[end + 3 : end + 5], "big"),
        "payload_offset": end + 5,
    }


def _cubic_bspline_basis(t: float) -> tuple[float, float, float, float]:
    """Uniform cubic B-spline basis for one four-control-point span."""
    one = 1.0 - t
    return (
        (one * one * one) / 6.0,
        (3.0 * t * t * t - 6.0 * t * t + 4.0) / 6.0,
        (-3.0 * t * t * t + 3.0 * t * t + 3.0 * t + 1.0) / 6.0,
        (t * t * t) / 6.0,
    )


def _decode_class1_grid(data: bytes, outer: dict) -> dict | None:
    if outer.get("class_id") != 1:
        return None
    offset = int(outer["payload_offset"])
    if offset + 6 > len(data):
        return None
    primitive_kind, u_count, v_count = struct.unpack_from(">HHH", data, offset)
    if primitive_kind not in {2, 3}:
        return None
    minimum_count = 4 if primitive_kind == 3 else 2
    if not (minimum_count <= u_count <= 4096 and minimum_count <= v_count <= 4096):
        return None
    control_count = u_count * v_count
    control_end = offset + 6 + control_count * 12
    if control_count > 2_000_000 or control_end > len(data):
        return None
    values = struct.unpack_from(f">{control_count * 3}f", data, offset + 6)
    controls = [tuple(values[index : index + 3]) for index in range(0, len(values), 3)]
    if not all(math.isfinite(value) and abs(value) < 1.0e9 for point in controls for value in point):
        return None

    if primitive_kind == 2:
        # Preserve the existing type-2 behavior until Cardinal interpolation and
        # closed-boundary semantics are independently validated. This remains an
        # explicit control-cage approximation rather than an exact patch claim.
        vertices = controls
        sample_u_count, sample_v_count = u_count, v_count
        u_step = v_step = None
        evaluator = "control_cage"
    else:
        # All three type-3 corpus records are 5x4, open/open, tension 0.5, Step 3.
        # The decoded source structure and surface-type correspondence support a
        # uniform cubic B-spline tensor evaluator. Closed type-3 directions are
        # deliberately rejected because no archive example establishes them.
        if control_end + 20 > len(data):
            return None
        u_closed, v_closed = struct.unpack_from(">HH", data, control_end)
        _u_tension, _v_tension = struct.unpack_from(">ff", data, control_end + 4)
        u_step, v_step = struct.unpack_from(">HH", data, control_end + 12)
        if u_closed or v_closed or u_step < 1 or v_step < 1:
            return None
        u_spans = u_count - 3
        v_spans = v_count - 3
        if u_spans < 1 or v_spans < 1:
            return None
        sample_u_count = u_spans * u_step + 1
        sample_v_count = v_spans * v_step + 1
        vertices = []
        for sample_u in range(sample_u_count):
            if sample_u == sample_u_count - 1:
                u_span, tu = u_spans - 1, 1.0
            else:
                position = sample_u / u_step
                u_span = int(position)
                tu = position - u_span
            basis_u = _cubic_bspline_basis(tu)
            for sample_v in range(sample_v_count):
                if sample_v == sample_v_count - 1:
                    v_span, tv = v_spans - 1, 1.0
                else:
                    position = sample_v / v_step
                    v_span = int(position)
                    tv = position - v_span
                basis_v = _cubic_bspline_basis(tv)
                xyz = [0.0, 0.0, 0.0]
                for local_u in range(4):
                    for local_v in range(4):
                        weight = basis_u[local_u] * basis_v[local_v]
                        point = controls[(u_span + local_u) * v_count + (v_span + local_v)]
                        for axis in range(3):
                            xyz[axis] += weight * point[axis]
                vertices.append(tuple(xyz))
        evaluator = "uniform_cubic_bspline"

    indices: list[int] = []
    normal_accum = [[0.0, 0.0, 0.0] for _ in vertices]

    def cross(a: tuple[float, float, float], b: tuple[float, float, float]):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    # Row-major sampled lattice. This winding matches the prior type-2 output.
    for u in range(sample_u_count - 1):
        for v in range(sample_v_count - 1):
            a = u * sample_v_count + v
            b = (u + 1) * sample_v_count + v
            c = (u + 1) * sample_v_count + v + 1
            d = u * sample_v_count + v + 1
            for triangle in ((a, d, c), (a, c, b)):
                indices.extend(triangle)
                p0, p1, p2 = [vertices[index] for index in triangle]
                edge1 = tuple(p1[axis] - p0[axis] for axis in range(3))
                edge2 = tuple(p2[axis] - p0[axis] for axis in range(3))
                face_normal = cross(edge1, edge2)
                for index in triangle:
                    for axis in range(3):
                        normal_accum[index][axis] += face_normal[axis]

    normals = []
    for vector in normal_accum:
        length = math.sqrt(sum(value * value for value in vector))
        normals.append(
            tuple(value / length for value in vector) if length > 1.0e-15 else (0.0, 1.0, 0.0)
        )
    return {
        "primitive_kind": primitive_kind,
        "surface_type_code": primitive_kind,
        "u_count": u_count,
        "v_count": v_count,
        "sample_u_count": sample_u_count,
        "sample_v_count": sample_v_count,
        "u_step": u_step,
        "v_step": v_step,
        "evaluator": evaluator,
        "vertices": vertices,
        "normals": normals,
        "indices": indices,
    }


def _append_chunk(buffer: bytearray, payload: bytes) -> tuple[int, int]:
    while len(buffer) % 4:
        buffer.append(0)
    offset = len(buffer)
    buffer.extend(payload)
    return offset, len(payload)


def _append_accessor(
    gltf: dict,
    buffer: bytearray,
    payload: bytes,
    component_type: int,
    accessor_type: str,
    count: int,
    *,
    target: int,
    minimum: list[float] | None = None,
    maximum: list[float] | None = None,
) -> int:
    offset, length = _append_chunk(buffer, payload)
    gltf.setdefault("bufferViews", []).append(
        {"buffer": 0, "byteOffset": offset, "byteLength": length, "target": target}
    )
    accessor = {
        "bufferView": len(gltf["bufferViews"]) - 1,
        "componentType": component_type,
        "count": count,
        "type": accessor_type,
    }
    if minimum is not None:
        accessor["min"] = minimum
    if maximum is not None:
        accessor["max"] = maximum
    gltf.setdefault("accessors", []).append(accessor)
    return len(gltf["accessors"]) - 1


def append_root_geometry(
    input_gltf: Path,
    asset_source: Path,
    scene_prefix: str,
    output_gltf: Path,
) -> dict:
    gltf = json.loads(input_gltf.read_text(encoding="utf-8"))
    input_bin = input_gltf.parent / gltf["buffers"][0]["uri"]
    buffer = bytearray(input_bin.read_bytes())
    store = dscmat.open_store(asset_source)
    emitted = []
    unsupported_class1_roots = []

    for node_index, node in enumerate(gltf.get("nodes", [])):
        extras = node.get("extras") or {}
        if extras.get("bz2_root_local_node_index") != 0:
            continue
        model_name = extras.get("bz2_dsc_model_name") or extras.get("bz2_root_hrc_model")
        if not model_name:
            continue
        member = store.find_basename(str(model_name) + ".hrc", f"{scene_prefix}/MODELS")
        if not member:
            continue
        data = store.read(member)
        outer = _outer_record(data)
        if not outer or outer.get("class_id") != 1:
            continue
        grid = _decode_class1_grid(data, outer)
        if grid is None:
            unsupported_class1_roots.append(
                {
                    "model_name": model_name,
                    "source_hrc": member,
                    "subtype": outer.get("subtype"),
                }
            )
            continue
        if node.get("mesh") is not None:
            raise RuntimeError(f"refusing to overwrite existing mesh on class-1 root {model_name}")

        vertices = grid["vertices"]
        position_values = [value for point in vertices for value in point]
        normal_values = [value for normal in grid["normals"] for value in normal]
        position_accessor = _append_accessor(
            gltf,
            buffer,
            struct.pack(f"<{len(position_values)}f", *position_values),
            5126,
            "VEC3",
            len(vertices),
            target=34962,
            minimum=[min(point[axis] for point in vertices) for axis in range(3)],
            maximum=[max(point[axis] for point in vertices) for axis in range(3)],
        )
        normal_accessor = _append_accessor(
            gltf,
            buffer,
            struct.pack(f"<{len(normal_values)}f", *normal_values),
            5126,
            "VEC3",
            len(vertices),
            target=34962,
        )
        index_accessor = _append_accessor(
            gltf,
            buffer,
            struct.pack(f"<{len(grid['indices'])}I", *grid["indices"]),
            5125,
            "SCALAR",
            len(grid["indices"]),
            target=34963,
        )
        gltf.setdefault("meshes", []).append(
            {
                "name": f"{node.get('name', model_name)}_primitive_grid",
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": position_accessor,
                            "NORMAL": normal_accessor,
                        },
                        "indices": index_accessor,
                        "material": 0,
                    }
                ],
                "extras": {
                    "source_kind": "softimage_class1_primitive_grid",
                    "primitive_kind": grid["primitive_kind"],
                    "surface_type_code": grid["surface_type_code"],
                    "evaluator": grid["evaluator"],
                    "u_count": grid["u_count"],
                    "v_count": grid["v_count"],
                    "sample_u_count": grid["sample_u_count"],
                    "sample_v_count": grid["sample_v_count"],
                    "u_step": grid["u_step"],
                    "v_step": grid["v_step"],
                    "texture_coordinates": "projection_required_no_baked_uv",
                },
            }
        )
        node["mesh"] = len(gltf["meshes"]) - 1
        node.setdefault("extras", {})["class1_grid_geometry_emitted"] = True
        emitted.append(
            {
                "model_name": model_name,
                "source_hrc": member,
                "node_index": node_index,
                "surface_type_code": grid["surface_type_code"],
                "evaluator": grid["evaluator"],
                "u_count": grid["u_count"],
                "v_count": grid["v_count"],
                "sample_u_count": grid["sample_u_count"],
                "sample_v_count": grid["sample_v_count"],
                "u_step": grid["u_step"],
                "v_step": grid["v_step"],
                "vertex_count": len(vertices),
                "triangle_count": len(grid["indices"]) // 3,
                "bounds": {
                    "min": [min(point[axis] for point in vertices) for axis in range(3)],
                    "max": [max(point[axis] for point in vertices) for axis in range(3)],
                },
            }
        )

    output_gltf.parent.mkdir(parents=True, exist_ok=True)
    output_bin = output_gltf.with_suffix(".bin")
    gltf["buffers"][0] = {"byteLength": len(buffer), "uri": output_bin.name}
    output_gltf.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    output_bin.write_bytes(buffer)
    summary = {
        "schema": "bz2-root-special-geometry-v1",
        "input_gltf": str(input_gltf),
        "asset_source": str(asset_source),
        "scene_prefix": scene_prefix,
        "output_gltf": str(output_gltf),
        "class1_grid_emitted_count": len(emitted),
        "class1_grids": emitted,
        "unsupported_class1_root_count": len(unsupported_class1_roots),
        "unsupported_class1_roots": unsupported_class1_roots,
        "final_mesh_count": len(gltf.get("meshes", [])),
        "notes": [
            "outer/ROOT class-1 surface type 2 retains the historical control-cage approximation pending exact Cardinal/closure validation",
            "outer/ROOT class-1 surface type 3 is evaluated as an open uniform cubic B-spline using the serialized U/V Step values",
            "nested class-1 construction/history nodes remain non-rendering hierarchy objects",
            "no TEXCOORD_0 is fabricated; class-1 source texture coordinates remain projection-dependent unless independently recovered",
        ],
    }
    output_gltf.with_suffix(".special_geometry.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_gltf", type=Path)
    parser.add_argument("asset_source", type=Path)
    parser.add_argument("scene_prefix")
    parser.add_argument("output_gltf", type=Path)
    args = parser.parse_args()
    result = append_root_geometry(
        args.input_gltf,
        args.asset_source,
        args.scene_prefix,
        args.output_gltf,
    )
    print(json.dumps(result, indent=2))
    return 1 if result["unsupported_class1_root_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

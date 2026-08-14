#!/usr/bin/env python3
"""Add decoded class-9/class-10 NURBS geometry to an assembled HRC glTF.

The polygon hierarchy/export remains owned by ``bz2_hrc_gltf.py``. This layer
reuses that output, matches parametric HRC nodes by source byte offset, decodes
the proven rational NURBS payloads, and appends curve/surface geometry to the
same glTF nodes and binary buffer.

Surface UVs in this milestone are normalized NURBS parameter-space coordinates,
not a claim about original source texture projection. Original DSC/MTR/TXT/PIC
material and texture binding remains a later source-scene integration stage.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path

import bz2_hrc_gltf as assembled
import bz2_hrc_tree_probe as hrc_tree
import bz2_nurbs_eval as evaluator
import bz2_nurbs_probe as nurbs_probe


def _canonicalize_knots(values: list[float] | None) -> list[float] | None:
    """Snap numerically equivalent adjacent knots to one exact value.

    Some Softimage repeated knots differ only at ~1e-16 after binary-to-decimal
    round trips (for example 1.3 vs 1.3000000000000003). Treating those as
    distinct can zero the basis at an otherwise valid clamped endpoint.
    """
    if not values:
        return values
    scale = max(1.0, max(abs(value) for value in values), abs(values[-1] - values[0]))
    epsilon = 1.0e-12 * scale
    output = [float(values[0])]
    for value in values[1:]:
        output.append(output[-1] if abs(value - output[-1]) <= epsilon else float(value))
    return output


def _canonicalize_record(record: dict) -> dict:
    if record["kind"] == "nurbs_curve":
        record["knots_standard"] = _canonicalize_knots(record.get("knots_standard"))
        if record.get("knots_standard_open") is not None:
            record["knots_standard_open"] = _canonicalize_knots(record.get("knots_standard_open"))
        return record

    record["knots_u_standard"] = _canonicalize_knots(record.get("knots_u_standard"))
    record["knots_v_standard"] = _canonicalize_knots(record.get("knots_v_standard"))
    if record.get("knots_u_standard_open") is not None:
        record["knots_u_standard_open"] = _canonicalize_knots(record.get("knots_u_standard_open"))
    if record.get("knots_v_standard_open") is not None:
        record["knots_v_standard_open"] = _canonicalize_knots(record.get("knots_v_standard_open"))
    for trim in (record.get("trim_section") or {}).get("trims", []):
        trim["knots_standard"] = _canonicalize_knots(trim.get("knots_standard"))
    return record


def _append_accessor(
    gltf: dict,
    buffer: bytearray,
    payload: bytes,
    component_type: int,
    accessor_type: str,
    count: int,
    *,
    target: int | None = None,
    minimum: list[float] | None = None,
    maximum: list[float] | None = None,
) -> int:
    offset, length = assembled._add_chunk(buffer, payload)
    view = {"buffer": 0, "byteOffset": offset, "byteLength": length}
    if target is not None:
        view["target"] = target
    gltf["bufferViews"].append(view)
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
    gltf["accessors"].append(accessor)
    return len(gltf["accessors"]) - 1


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _accumulate_surface_normals(vertices, triangles):
    accum = [[0.0, 0.0, 0.0] for _ in vertices]
    for a, b, c in triangles:
        pa, pb, pc = vertices[a], vertices[b], vertices[c]
        ab = tuple(pb[index] - pa[index] for index in range(3))
        ac = tuple(pc[index] - pa[index] for index in range(3))
        normal = _cross(ab, ac)
        for vertex_index in (a, b, c):
            for axis in range(3):
                accum[vertex_index][axis] += normal[axis]
    output = []
    for normal in accum:
        length = math.sqrt(sum(value * value for value in normal))
        output.append(
            tuple(value / length for value in normal)
            if length > 1.0e-15
            else (0.0, 0.0, 1.0)
        )
    return output


def _surface_geometry(record: dict, steps_u: int, steps_v: int) -> dict:
    _, count_u, count_v, knots_u, knots_v, degree_u, degree_v, closed_u, closed_v = evaluator._surface_data(record)
    us = evaluator._samples(knots_u, degree_u, count_u, steps_u, closed_u)
    vs = evaluator._samples(knots_v, degree_v, count_v, steps_v, closed_v)
    vertices = [evaluator.evaluate_surface(record, u, v) for v in vs for u in us]

    u_start, u_end = knots_u[degree_u], knots_u[count_u]
    v_start, v_end = knots_v[degree_v], knots_v[count_v]
    u_span = u_end - u_start
    v_span = v_end - v_start
    texcoords = [
        (
            (u - u_start) / u_span if abs(u_span) > 1.0e-15 else 0.0,
            (v - v_start) / v_span if abs(v_span) > 1.0e-15 else 0.0,
        )
        for v in vs
        for u in us
    ]

    boundaries, holes = evaluator._trim_loops(record)
    apply_trim = bool(boundaries or holes)
    u_domain = u_start, u_end
    v_domain = v_start, v_end
    cells_u = steps_u if closed_u else steps_u - 1
    cells_v = steps_v if closed_v else steps_v - 1
    triangles = []
    clipped_cells = 0

    for v_index in range(cells_v):
        v_next = (v_index + 1) % steps_v
        v_mid = evaluator._mid(vs[v_index], vs[v_next], *v_domain, closed_v)
        for u_index in range(cells_u):
            u_next = (u_index + 1) % steps_u
            u_mid = evaluator._mid(us[u_index], us[u_next], *u_domain, closed_u)
            if apply_trim and not evaluator._keep_uv((u_mid, v_mid), boundaries, holes):
                clipped_cells += 1
                continue
            a = v_index * steps_u + u_index
            b = v_index * steps_u + u_next
            c = v_next * steps_u + u_next
            d = v_next * steps_u + u_index
            triangles.append((a, b, c))
            triangles.append((a, c, d))

    return {
        "vertices": vertices,
        "texcoords": texcoords,
        "normals": _accumulate_surface_normals(vertices, triangles),
        "triangles": triangles,
        "trim_boundary_loops": len(boundaries),
        "trim_hole_loops": len(holes),
        "trimmed_cells": clipped_cells,
    }


def _emit_surface(gltf: dict, buffer: bytearray, name: str, record: dict, steps_u: int, steps_v: int) -> tuple[int, dict]:
    geometry = _surface_geometry(record, steps_u, steps_v)
    vertices = geometry["vertices"]
    triangles = geometry["triangles"]
    if not vertices or not triangles:
        raise ValueError("NURBS surface tessellation produced no triangles")

    positions = [value for vertex in vertices for value in vertex]
    texcoords = [value for uv in geometry["texcoords"] for value in uv]
    normals = [value for normal in geometry["normals"] for value in normal]
    indices = [index for triangle in triangles for index in triangle]
    attributes = {
        "POSITION": _append_accessor(
            gltf,
            buffer,
            struct.pack(f"<{len(positions)}f", *positions),
            5126,
            "VEC3",
            len(vertices),
            target=34962,
            minimum=[min(vertex[axis] for vertex in vertices) for axis in range(3)],
            maximum=[max(vertex[axis] for vertex in vertices) for axis in range(3)],
        ),
        "TEXCOORD_0": _append_accessor(
            gltf,
            buffer,
            struct.pack(f"<{len(texcoords)}f", *texcoords),
            5126,
            "VEC2",
            len(vertices),
            target=34962,
        ),
        "NORMAL": _append_accessor(
            gltf,
            buffer,
            struct.pack(f"<{len(normals)}f", *normals),
            5126,
            "VEC3",
            len(vertices),
            target=34962,
        ),
    }
    index_accessor = _append_accessor(
        gltf,
        buffer,
        struct.pack(f"<{len(indices)}I", *indices),
        5125,
        "SCALAR",
        len(indices),
        target=34963,
    )
    gltf["meshes"].append(
        {
            "name": name,
            "primitives": [
                {
                    "attributes": attributes,
                    "indices": index_accessor,
                    "material": 0,
                }
            ],
            "extras": {
                "source_kind": "nurbs_surface",
                "uv_source": "normalized_parameter_space",
                "trim_boundary_loops": geometry["trim_boundary_loops"],
                "trim_hole_loops": geometry["trim_hole_loops"],
                "trimmed_cells": geometry["trimmed_cells"],
            },
        }
    )
    return len(gltf["meshes"]) - 1, geometry


def _emit_curve(gltf: dict, buffer: bytearray, name: str, record: dict, steps: int) -> tuple[int, int]:
    points, knots, degree, closed = evaluator._curve_data(record)
    params = evaluator._samples(knots, degree, len(points), steps, closed)
    vertices = [evaluator.evaluate_curve(record, value) for value in params]
    if len(vertices) < 2:
        raise ValueError("NURBS curve tessellation produced fewer than two vertices")
    indices = list(range(len(vertices)))
    if closed:
        indices.append(0)
    positions = [value for vertex in vertices for value in vertex]
    position_accessor = _append_accessor(
        gltf,
        buffer,
        struct.pack(f"<{len(positions)}f", *positions),
        5126,
        "VEC3",
        len(vertices),
        target=34962,
        minimum=[min(vertex[axis] for vertex in vertices) for axis in range(3)],
        maximum=[max(vertex[axis] for vertex in vertices) for axis in range(3)],
    )
    index_accessor = _append_accessor(
        gltf,
        buffer,
        struct.pack(f"<{len(indices)}I", *indices),
        5125,
        "SCALAR",
        len(indices),
        target=34963,
    )
    gltf["meshes"].append(
        {
            "name": name,
            "primitives": [
                {
                    "attributes": {"POSITION": position_accessor},
                    "indices": index_accessor,
                    "mode": 3,
                    "material": 0,
                }
            ],
            "extras": {"source_kind": "nurbs_curve"},
        }
    )
    return len(gltf["meshes"]) - 1, len(vertices)


def export_parametric(
    source: Path,
    output: Path,
    *,
    curve_steps: int = 64,
    surface_steps_u: int = 32,
    surface_steps_v: int = 32,
) -> dict:
    base_summary = assembled.export_hrc(source, output)
    if base_summary["class4_decode_failures"] or base_summary["unresolved_class4_srt_count"]:
        raise RuntimeError("base class-4 HRC export is incomplete; refusing to layer NURBS geometry onto it")

    gltf = json.loads(output.read_text(encoding="utf-8"))
    bin_path = output.with_suffix(".bin")
    buffer = bytearray(bin_path.read_bytes())
    data = source.read_bytes()
    tree_report = hrc_tree.probe(source)

    source_items = []
    outer = tree_report.get("outer_model") or {}
    if outer.get("class_id") in {9, 10}:
        source_items.append(dict(outer))
    source_items.extend(
        dict(item)
        for item in tree_report.get("tree", [])
        if item.get("class_id") in {9, 10}
    )

    node_index_by_offset = {
        int(node.get("extras", {}).get("source_offset")): index
        for index, node in enumerate(gltf.get("nodes", []))
        if node.get("extras", {}).get("source_offset") is not None
    }

    exported = []
    failures = []
    for item in source_items:
        name = str(item.get("name") or "unnamed_parametric")
        string_offset = item.get("string_offset")
        source_offset = item.get("offset")
        node_index = node_index_by_offset.get(int(source_offset)) if source_offset is not None else None
        if string_offset is None or node_index is None:
            failures.append({"name": name, "reason": "missing_source_or_node_offset"})
            continue
        if not item.get("local_srt"):
            failures.append({"name": name, "reason": "missing_local_srt"})
            continue

        anchor = nurbs_probe.StringAnchor(
            offset=int(string_offset),
            value=name,
            parametric=True,
        )
        record = nurbs_probe.decode_parametric_record(data, anchor)
        if record is None:
            failures.append({"name": name, "reason": "parametric_decode_failed"})
            continue
        if not record.get("reconstruction_ready"):
            failures.append({"name": name, "reason": "parametric_record_not_reconstruction_ready"})
            continue
        record = _canonicalize_record(record)

        try:
            if record["kind"] == "nurbs_surface":
                mesh_index, geometry = _emit_surface(
                    gltf,
                    buffer,
                    name,
                    record,
                    surface_steps_u,
                    surface_steps_v,
                )
                exported.append(
                    {
                        "name": name,
                        "kind": record["kind"],
                        "mesh_index": mesh_index,
                        "vertices": len(geometry["vertices"]),
                        "triangles": len(geometry["triangles"]),
                        "trim_boundary_loops": geometry["trim_boundary_loops"],
                        "trim_hole_loops": geometry["trim_hole_loops"],
                        "trimmed_cells": geometry["trimmed_cells"],
                    }
                )
            else:
                mesh_index, vertex_count = _emit_curve(
                    gltf,
                    buffer,
                    name,
                    record,
                    curve_steps,
                )
                exported.append(
                    {
                        "name": name,
                        "kind": record["kind"],
                        "mesh_index": mesh_index,
                        "vertices": vertex_count,
                    }
                )
            gltf["nodes"][node_index]["mesh"] = mesh_index
            gltf["nodes"][node_index].setdefault("extras", {})["parametric_geometry_emitted"] = True
        except Exception as exc:
            failures.append(
                {
                    "name": name,
                    "reason": "parametric_tessellation_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    gltf["buffers"][0]["byteLength"] = len(buffer)
    output.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    bin_path.write_bytes(buffer)

    summary = {
        "schema": "bz2-assembled-hrc-gltf-parametric-v1",
        "source": str(source),
        "gltf": str(output),
        "bin": str(bin_path),
        "base": base_summary,
        "settings": {
            "curve_steps": curve_steps,
            "surface_steps_u": surface_steps_u,
            "surface_steps_v": surface_steps_v,
            "surface_uvs": "normalized_parameter_space",
            "trim_tessellation": "uv_face_centroid_clip_validation_quality",
            "knot_canonicalization_relative_epsilon": 1.0e-12,
        },
        "parametric_source_count": len(source_items),
        "parametric_exported_count": len(exported),
        "parametric_failure_count": len(failures),
        "parametric_exports": exported,
        "parametric_failures": failures,
        "total_gltf_mesh_count": len(gltf.get("meshes", [])),
        "notes": [
            "class-4 polygon geometry comes from bz2_hrc_gltf.py",
            "class-9 NURBS curves are emitted as glTF LINE_STRIP primitives",
            "class-10 NURBS surfaces are emitted as triangle primitives with generated smooth normals",
            "normalized parameter-space UVs are provisional geometry-preservation UVs, not original texture projection",
            "near-equal repeated knots are canonicalized before evaluation to avoid false endpoint basis degeneracy",
        ],
    }
    output.with_suffix(".json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--curve-steps", type=int, default=64)
    parser.add_argument("--surface-steps-u", type=int, default=32)
    parser.add_argument("--surface-steps-v", type=int, default=32)
    args = parser.parse_args()
    summary = export_parametric(
        args.source,
        args.output,
        curve_steps=max(2, args.curve_steps),
        surface_steps_u=max(2, args.surface_steps_u),
        surface_steps_v=max(2, args.surface_steps_v),
    )
    print(json.dumps(summary, indent=2))
    return 1 if summary["parametric_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

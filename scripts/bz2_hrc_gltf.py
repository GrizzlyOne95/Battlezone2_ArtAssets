#!/usr/bin/env python3
"""Export one binary Softimage HRC hierarchy as an assembled glTF 2.0 scene.

This is the first production-oriented HRC model path. It emits every structurally
decoded class-4 polygon mesh at its recovered local HRC transform and preserves
other HRC records as named hierarchy nodes for later parametric/animation work.

Class-9/class-10 parametric geometry is deliberately not emitted here yet. Those
nodes remain in the hierarchy so adding NURBS geometry later does not require a
second scene-structure rewrite.

Multi-contour class-4 polygons use Shapely >= 2.1 constrained triangulation. The
exporter fails rather than silently dropping those source polygons when Shapely
is unavailable.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
from pathlib import Path

import bz2_hrc_tree_probe as hrc_tree

try:
    from shapely import constrained_delaunay_triangles
    from shapely.geometry import Polygon
except Exception:  # pragma: no cover - optional runtime dependency
    Polygon = None
    constrained_delaunay_triangles = None


CONTOUR_SEPARATOR = 0xFFFFFFFF
SLOT_MATERIAL_RE = re.compile(rb"\x00([\x01-\xff])\x00\x00([ -~]{1,80})\x00")


def _identity() -> list[list[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[row][k] * b[k][col] for k in range(4)) for col in range(4)] for row in range(4)]


def _srt_matrix(srt: dict | None) -> list[list[float]]:
    if not srt:
        return _identity()
    sx, sy, sz = srt["scale"]
    rx, ry, rz = srt["rotation_xyz"]
    tx, ty, tz = srt["translation_xyz"]
    cx, sxn = math.cos(rx), math.sin(rx)
    cy, syn = math.cos(ry), math.sin(ry)
    cz, szn = math.cos(rz), math.sin(rz)
    rot_x = [[1, 0, 0, 0], [0, cx, sxn, 0], [0, -sxn, cx, 0], [0, 0, 0, 1]]
    rot_y = [[cy, 0, -syn, 0], [0, 1, 0, 0], [syn, 0, cy, 0], [0, 0, 0, 1]]
    rot_z = [[cz, szn, 0, 0], [-szn, cz, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    scale = [[sx, 0, 0, 0], [0, sy, 0, 0], [0, 0, sz, 0], [0, 0, 0, 1]]
    translate = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [tx, ty, tz, 1]]
    return _mul(scale, _mul(rot_x, _mul(rot_y, _mul(rot_z, translate))))


def _gltf_matrix(srt: dict | None) -> list[float]:
    # Internal matrices use row vectors with translation in row 4. glTF uses
    # column vectors and column-major serialization, so flattening this row-major
    # matrix serializes the equivalent transpose correctly.
    return [float(value) for row in _srt_matrix(srt) for value in row]


def _plausible_srt(values: tuple[float, ...]) -> bool:
    return (
        len(values) == 9
        and all(math.isfinite(value) for value in values)
        and all(1.0e-9 < abs(value) < 1.0e8 for value in values[:3])
        and all(abs(value) < 1.0e9 for value in values[3:])
    )


def _slot_material_srt(data: bytes, start: int, end: int) -> dict | None:
    """Recover class-4 SRT from material-slot envelopes missed by the v1 probe.

    Later archived assets use slot tags other than ``00 01 00 00`` immediately
    after the SRT (for example slots 2 and 3 in the high-resolution ISDF tank).
    The nine-float SRT remains directly before that slot/material block.
    """
    candidates = []
    for match in SLOT_MATERIAL_RE.finditer(data, start, end):
        offset = match.start() - 36
        if offset < start:
            continue
        values = struct.unpack_from(">9f", data, offset)
        if not _plausible_srt(values):
            continue
        candidates.append(
            (
                offset,
                match.group(1)[0],
                match.group(2).decode("latin-1", errors="replace"),
                values,
            )
        )
    if not candidates:
        return None
    trailing = [item for item in candidates if item[0] >= max(start, end - 4096)]
    offset, slot, material_name, values = (
        min(trailing, key=lambda item: item[0])
        if trailing
        else max(candidates, key=lambda item: item[0])
    )
    return {
        "scale": list(values[:3]),
        "rotation_xyz": list(values[3:6]),
        "translation_xyz": list(values[6:]),
        "source": "pre_mesh_material_slot_block",
        "offset": offset,
        "anchor_slot": slot,
        "anchor_name": material_name,
    }


def _outer_payload_offset(data: bytes, outer: dict) -> int:
    end = data.find(b"\0", int(outer["string_offset"]))
    if end < 0:
        raise ValueError("unterminated outer HRC model name")
    return end + 5


def _record_end(records: list[dict], index: int, data_length: int) -> int:
    if index + 1 >= len(records):
        return data_length
    next_record = records[index + 1]
    return int(next_record["offset"]) - int(next_record["zero_run"])


def _decode_mesh(data: bytes, payload_offset: int, end: int) -> dict:
    if payload_offset + 8 > end:
        raise ValueError("short class-4 mesh header")
    vertex_count = int.from_bytes(data[payload_offset + 4 : payload_offset + 8], "big")
    if vertex_count > 2_000_000:
        raise ValueError(f"implausible class-4 vertex count: {vertex_count}")

    cursor = payload_offset + 8
    vertex_end = cursor + vertex_count * 14
    if vertex_end > end:
        raise ValueError("class-4 vertex array overruns record")
    vertices = []
    for offset in range(cursor, vertex_end, 14):
        xyz = struct.unpack_from(">fff", data, offset)
        if not all(math.isfinite(value) for value in xyz):
            raise ValueError("non-finite class-4 vertex")
        vertices.append(tuple(float(value) for value in xyz))
    cursor = vertex_end

    if vertex_count == 0 or cursor + 4 > end:
        return {"vertices": vertices, "polygons": [], "multicontour_polygons": 0}

    polygon_count = int.from_bytes(data[cursor : cursor + 4], "big")
    cursor += 4
    if polygon_count > 1_000_000:
        raise ValueError(f"implausible class-4 polygon count: {polygon_count}")

    polygons = []
    multicontour_polygons = 0
    for _ in range(polygon_count):
        if cursor + 2 > end:
            raise ValueError("class-4 polygon header overrun")
        corner_count = int.from_bytes(data[cursor : cursor + 2], "big")
        cursor += 2
        if corner_count < 3 or cursor + corner_count * 28 + 4 > end:
            raise ValueError("class-4 polygon corner overrun")

        contours: list[list[dict]] = [[]]
        for corner_index in range(corner_count):
            offset = cursor + corner_index * 28
            vertex_index = int.from_bytes(data[offset : offset + 4], "big")
            if vertex_index == CONTOUR_SEPARATOR:
                contours.append([])
                continue
            if vertex_index >= vertex_count:
                raise ValueError(f"class-4 vertex index out of range: {vertex_index}/{vertex_count}")

            nx, ny, nz = struct.unpack_from(">fff", data, offset + 4)
            normal = None
            nan_flags = (math.isnan(nx), math.isnan(ny), math.isnan(nz))
            if all(nan_flags):
                normal = None
            elif any(nan_flags) or not all(math.isfinite(value) for value in (nx, ny, nz)):
                raise ValueError("invalid class-4 normal")
            else:
                normal = (float(nx), float(ny), float(nz))

            u, v = struct.unpack_from(">ff", data, offset + 16)
            if not (math.isfinite(u) and math.isfinite(v)):
                raise ValueError("non-finite class-4 UV")
            contours[-1].append(
                {
                    "vertex_index": vertex_index,
                    "normal": normal,
                    "uv": (float(u), float(v)),
                }
            )

        contours = [contour for contour in contours if contour]
        if len(contours) > 1:
            multicontour_polygons += 1
        polygons.append(contours)
        cursor += corner_count * 28 + 4

    return {
        "vertices": vertices,
        "polygons": polygons,
        "multicontour_polygons": multicontour_polygons,
    }


def _newell(points: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    nx = ny = nz = 0.0
    for current, following in zip(points, points[1:] + points[:1]):
        nx += (current[1] - following[1]) * (current[2] + following[2])
        ny += (current[2] - following[2]) * (current[0] + following[0])
        nz += (current[0] - following[0]) * (current[1] + following[1])
    return nx, ny, nz


def _signed_area(loop: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        loop[index][0] * loop[(index + 1) % len(loop)][1]
        - loop[(index + 1) % len(loop)][0] * loop[index][1]
        for index in range(len(loop))
    )


def _triangulate(mesh: dict, contours: list[list[dict]]) -> list[list[dict]]:
    if len(contours) == 1:
        corners = contours[0]
        if len(corners) < 3:
            return []
        return [[corners[0], corners[index], corners[index + 1]] for index in range(1, len(corners) - 1)]

    if Polygon is None or constrained_delaunay_triangles is None:
        raise RuntimeError(
            "multi-contour HRC polygons require Shapely >= 2.1; install it rather than dropping source polygons"
        )

    loops_3d = [[mesh["vertices"][corner["vertex_index"]] for corner in contour] for contour in contours]
    reference = max(loops_3d, key=lambda loop: sum(value * value for value in _newell(loop)))
    normal = _newell(reference)
    drop_axis = max(range(3), key=lambda axis: abs(normal[axis]))

    def project(point: tuple[float, float, float]) -> tuple[float, float]:
        if drop_axis == 0:
            return point[1], point[2]
        if drop_axis == 1:
            return point[0], point[2]
        return point[0], point[1]

    loops_2d = [[project(point) for point in loop] for loop in loops_3d]
    shell_index = max(range(len(loops_2d)), key=lambda index: abs(_signed_area(loops_2d[index])))
    shell = loops_2d[shell_index]
    holes = [loop for index, loop in enumerate(loops_2d) if index != shell_index]
    polygon = Polygon(shell, holes)
    if not polygon.is_valid:
        raise RuntimeError("invalid projected multi-contour HRC polygon")

    corner_by_xy: dict[tuple[float, float], dict] = {}
    for contour, coordinates in zip(contours, loops_2d):
        for corner, xy in zip(contour, coordinates):
            corner_by_xy.setdefault((round(xy[0], 9), round(xy[1], 9)), corner)

    triangles = []
    for triangle in constrained_delaunay_triangles(polygon).geoms:
        output = []
        for xy in list(triangle.exterior.coords)[:-1]:
            key = round(xy[0], 9), round(xy[1], 9)
            corner = corner_by_xy.get(key)
            if corner is None:
                # Defensive recovery for tiny GEOS coordinate-rounding differences.
                corner = min(
                    (candidate for contour in contours for candidate in contour),
                    key=lambda candidate: (
                        (project(mesh["vertices"][candidate["vertex_index"]])[0] - xy[0]) ** 2
                        + (project(mesh["vertices"][candidate["vertex_index"]])[1] - xy[1]) ** 2
                    ),
                )
            output.append(corner)
        triangles.append(output)
    return triangles


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _normalize(vector):
    length = math.sqrt(sum(value * value for value in vector))
    return tuple(value / length for value in vector) if length > 1.0e-15 else (0.0, 0.0, 1.0)


def _face_normal(positions):
    a, b, c = positions[:3]
    return _normalize(
        _cross(
            tuple(b[index] - a[index] for index in range(3)),
            tuple(c[index] - a[index] for index in range(3)),
        )
    )


def _add_chunk(buffer: bytearray, payload: bytes) -> tuple[int, int]:
    offset = len(buffer)
    buffer.extend(payload)
    while len(buffer) % 4:
        buffer.append(0)
    return offset, len(payload)


def _accessor(gltf, buffer, payload, component_type, accessor_type, count, *, target=None, minimum=None, maximum=None):
    offset, length = _add_chunk(buffer, payload)
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


def _emit_mesh(gltf: dict, buffer: bytearray, name: str, mesh: dict) -> int | None:
    primitive_vertices = []
    vertex_map = {}
    indices = []

    for contours in mesh["polygons"]:
        for triangle in _triangulate(mesh, contours):
            positions = [mesh["vertices"][corner["vertex_index"]] for corner in triangle]
            fallback_normal = _face_normal(positions)
            for corner, position in zip(triangle, positions):
                normal = corner["normal"] or fallback_normal
                key = position, corner["uv"], normal
                if key not in vertex_map:
                    vertex_map[key] = len(primitive_vertices)
                    primitive_vertices.append(key)
                indices.append(vertex_map[key])

    if not indices:
        return None

    positions = [value for position, _, _ in primitive_vertices for value in position]
    texcoords = [value for _, uv, _ in primitive_vertices for value in uv]
    normals = [value for _, _, normal in primitive_vertices for value in normal]
    points = [position for position, _, _ in primitive_vertices]

    attributes = {
        "POSITION": _accessor(
            gltf,
            buffer,
            struct.pack(f"<{len(positions)}f", *positions),
            5126,
            "VEC3",
            len(primitive_vertices),
            target=34962,
            minimum=[min(point[axis] for point in points) for axis in range(3)],
            maximum=[max(point[axis] for point in points) for axis in range(3)],
        ),
        "TEXCOORD_0": _accessor(
            gltf,
            buffer,
            struct.pack(f"<{len(texcoords)}f", *texcoords),
            5126,
            "VEC2",
            len(primitive_vertices),
            target=34962,
        ),
        "NORMAL": _accessor(
            gltf,
            buffer,
            struct.pack(f"<{len(normals)}f", *normals),
            5126,
            "VEC3",
            len(primitive_vertices),
            target=34962,
        ),
    }
    index_accessor = _accessor(
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
        }
    )
    return len(gltf["meshes"]) - 1


def export_hrc(source: Path, output: Path) -> dict:
    data = source.read_bytes()
    report = hrc_tree.probe(source)
    outer = dict(report.get("outer_model") or {})
    records = [dict(item) for item in report.get("tree", [])]
    if not outer:
        raise ValueError(f"no HRCH root found in {source}")

    outer["depth"] = 0
    outer["parent_name"] = None
    outer["payload_offset"] = _outer_payload_offset(data, outer)
    nodes = [outer, *records]

    # Promote later material-slot forms that the original class-4 SRT heuristic
    # did not recognize. This is only applied when the validated probe has no SRT.
    for index, item in enumerate(records):
        if item.get("class_id") != 4 or item.get("local_srt"):
            continue
        end = _record_end(records, index, len(data))
        recovered = _slot_material_srt(data, int(item["offset"]), end)
        if recovered:
            item["local_srt"] = recovered

    if outer.get("class_id") == 4 and not outer.get("local_srt"):
        end = int(records[0]["offset"]) - int(records[0]["zero_run"]) if records else len(data)
        recovered = _slot_material_srt(data, int(outer["offset"]), end)
        if recovered:
            outer["local_srt"] = recovered

    gltf = {
        "asset": {"version": "2.0", "generator": "bz2_hrc_gltf.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [],
        "meshes": [],
        "materials": [
            {
                "name": "HRC_Unbound_Material",
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.8, 0.8, 0.8, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        ],
        "buffers": [{}],
        "bufferViews": [],
        "accessors": [],
    }
    buffer = bytearray()

    index_by_name: dict[str, int] = {}
    unresolved_srt = []
    unresolved_class4_srt = []
    for index, item in enumerate(nodes):
        node = {
            "name": item.get("name") or f"node_{index}",
            "matrix": _gltf_matrix(item.get("local_srt")),
            "extras": {
                "class_id": item.get("class_id"),
                "subtype": item.get("subtype"),
                "source_offset": item.get("offset"),
                "srt_source": (item.get("local_srt") or {}).get("source"),
            },
        }
        gltf["nodes"].append(node)
        index_by_name[node["name"]] = index
        if not item.get("local_srt"):
            unresolved_srt.append(node["name"])
            if item.get("class_id") == 4:
                unresolved_class4_srt.append(node["name"])

    # Parent relationships come from the structurally validated HRC preorder tree.
    for index, item in enumerate(nodes[1:], start=1):
        parent_name = item.get("parent_name")
        parent_index = index_by_name.get(parent_name) if parent_name else 0
        if parent_index is None:
            parent_index = 0
        gltf["nodes"][parent_index].setdefault("children", []).append(index)

    mesh_count = 0
    multicontour_tessellated = 0
    class4_decode_failures = []
    for node_index, item in enumerate(nodes):
        if item.get("class_id") != 4:
            continue
        if item is outer:
            end = int(records[0]["offset"]) - int(records[0]["zero_run"]) if records else len(data)
            payload_offset = int(outer["payload_offset"])
        else:
            record_index = node_index - 1
            end = _record_end(records, record_index, len(data))
            payload_offset = int(item["payload_offset"])
        try:
            mesh = _decode_mesh(data, payload_offset, end)
            multicontour_tessellated += int(mesh["multicontour_polygons"])
            mesh_index = _emit_mesh(gltf, buffer, str(item["name"]), mesh)
        except Exception as exc:
            class4_decode_failures.append({"name": item.get("name"), "error": f"{type(exc).__name__}: {exc}"})
            continue
        if mesh_index is not None:
            gltf["nodes"][node_index]["mesh"] = mesh_index
            mesh_count += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    bin_path = output.with_suffix(".bin")
    gltf["buffers"][0] = {"byteLength": len(buffer), "uri": bin_path.name}
    output.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    bin_path.write_bytes(buffer)

    summary = {
        "schema": "bz2-assembled-hrc-gltf-v1",
        "source": str(source),
        "gltf": str(output),
        "bin": str(bin_path),
        "node_count": len(nodes),
        "class4_node_count": sum(1 for item in nodes if item.get("class_id") == 4),
        "mesh_count": mesh_count,
        "multicontour_polygons_tessellated": multicontour_tessellated,
        "unresolved_srt_count": len(unresolved_srt),
        "unresolved_srt": unresolved_srt,
        "unresolved_class4_srt_count": len(unresolved_class4_srt),
        "unresolved_class4_srt": unresolved_class4_srt,
        "class4_decode_failures": class4_decode_failures,
        "parametric_nodes_preserved_without_geometry": sum(
            1 for item in nodes if item.get("class_id") in {9, 10}
        ),
        "notes": [
            "class-4 polygon geometry, UVs, normals, hierarchy, and recovered local SRT are emitted",
            "all-NaN source normals receive a generated per-triangle fallback normal",
            "multi-contour polygons are constrained-triangulated rather than dropped",
            "the placeholder double-sided material is intentional; source material/texture binding is a later integration stage",
            "class-9/class-10 parametric nodes are preserved as hierarchy nodes but their geometry is not emitted by this milestone",
        ],
    }
    output.with_suffix(".json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Source .hrc file")
    parser.add_argument("output", type=Path, help="Destination .gltf path")
    args = parser.parse_args()
    summary = export_hrc(args.source, args.output)
    print(json.dumps(summary, indent=2))
    return 1 if summary["class4_decode_failures"] or summary["unresolved_class4_srt_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

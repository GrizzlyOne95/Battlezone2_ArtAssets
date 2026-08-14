#!/usr/bin/env python3
"""Validate structurally decoded class-4 polygon meshes across the BZ2 HRC corpus.

The validator covers both outer HRCH class-4 roots and nested ``00 01 <name>``
class-4 records. It encodes three corpus-proven rules that the older exporter did
not handle correctly:

* polygon corner counts are u16 and may exceed 32;
* an all-NaN normal triplet is a valid missing-normal sentinel;
* vertex index ``0xFFFFFFFF`` is an in-polygon contour separator, not a vertex.

The last rule is used by a small set of movie assets to represent polygons with
multiple contours/holes. Those polygons are decoded and counted, but the report
intentionally does not claim a triangle count for them until hole-aware
triangulation is implemented.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import time
from collections import Counter
from pathlib import Path

NAME_RE = re.compile(rb"\x00\x01([ -~]{1,80})\x00")
KNOWN_CLASSES = {0, 1, 2, 4, 5, 6, 9, 10}
CONTOUR_SEPARATOR = 0xFFFFFFFF


def zero_run_before(data: bytes, offset: int) -> int:
    cursor = offset - 1
    while cursor >= 0 and data[cursor] == 0:
        cursor -= 1
    return offset - 1 - cursor


def outer_class4(data: bytes) -> tuple[int, int] | None:
    marker = data.find(b"HRCH")
    if marker < 0:
        return None
    end = data.find(b"\0", marker + 4)
    if end < 0 or end + 5 > len(data):
        return None
    class_id = int.from_bytes(data[end + 1 : end + 3], "big")
    if class_id != 4:
        return None
    return end + 5, marker


def structural_records(data: bytes) -> list[tuple[int, int, int, int]]:
    records: list[tuple[int, int, int, int]] = []
    for match in NAME_RE.finditer(data):
        class_offset = match.end()
        if class_offset + 4 > len(data):
            continue
        class_id = int.from_bytes(data[class_offset : class_offset + 2], "big")
        zero_run = zero_run_before(data, match.start())
        if class_id not in KNOWN_CLASSES or zero_run < 20 or zero_run % 2:
            continue
        records.append((class_id, class_offset + 4, match.start(), zero_run))
    return records


def decode_class4(data: bytes, payload_offset: int, end: int) -> tuple[dict | None, str | None]:
    if payload_offset + 8 > end:
        return None, "short_mesh_header"

    vertex_count = int.from_bytes(data[payload_offset + 4 : payload_offset + 8], "big")
    if vertex_count > 2_000_000:
        return None, "implausible_vertex_count"

    cursor = payload_offset + 8
    vertex_end = cursor + vertex_count * 14
    if vertex_end > end:
        return None, "vertex_array_overrun"

    for offset in range(cursor, vertex_end, 14):
        xyz = struct.unpack_from(">fff", data, offset)
        if not all(math.isfinite(value) for value in xyz):
            return None, "nonfinite_vertex"
    cursor = vertex_end

    if vertex_count == 0 or cursor + 4 > end:
        return {
            "vertex_count": vertex_count,
            "polygon_count": 0,
            "simple_fan_triangles": 0,
            "nan_normal_corners": 0,
            "contour_separators": 0,
            "multi_contour_polygons": 0,
            "max_polygon_corners": 0,
            "max_contours_per_polygon": 0,
            "transform_only": True,
        }, None

    polygon_count = int.from_bytes(data[cursor : cursor + 4], "big")
    cursor += 4
    if polygon_count > 1_000_000:
        return None, "implausible_polygon_count"

    simple_fan_triangles = 0
    nan_normal_corners = 0
    contour_separators = 0
    multi_contour_polygons = 0
    max_polygon_corners = 0
    max_contours_per_polygon = 0

    for _ in range(polygon_count):
        if cursor + 2 > end:
            return None, "polygon_header_overrun"
        corner_count = int.from_bytes(data[cursor : cursor + 2], "big")
        cursor += 2
        if corner_count < 3 or cursor + corner_count * 28 + 4 > end:
            return None, "polygon_corner_overrun"

        max_polygon_corners = max(max_polygon_corners, corner_count)
        real_corners = 0
        contours = 1

        for corner_index in range(corner_count):
            offset = cursor + corner_index * 28
            vertex_index = int.from_bytes(data[offset : offset + 4], "big")
            if vertex_index == CONTOUR_SEPARATOR:
                contour_separators += 1
                contours += 1
                continue
            if vertex_index >= vertex_count:
                return None, "vertex_index_out_of_range"
            real_corners += 1

            nx, ny, nz = struct.unpack_from(">fff", data, offset + 4)
            nan_flags = (math.isnan(nx), math.isnan(ny), math.isnan(nz))
            if all(nan_flags):
                nan_normal_corners += 1
            elif any(nan_flags) or not all(math.isfinite(value) for value in (nx, ny, nz)):
                return None, "invalid_normal"
            elif nx * nx + ny * ny + nz * nz > 1.5625:
                return None, "implausible_normal"

            u, v = struct.unpack_from(">ff", data, offset + 16)
            if not (math.isfinite(u) and math.isfinite(v)):
                return None, "nonfinite_uv"

        if contours > 1:
            multi_contour_polygons += 1
        else:
            simple_fan_triangles += max(0, real_corners - 2)
        max_contours_per_polygon = max(max_contours_per_polygon, contours)
        cursor += corner_count * 28 + 4

    return {
        "vertex_count": vertex_count,
        "polygon_count": polygon_count,
        "simple_fan_triangles": simple_fan_triangles,
        "nan_normal_corners": nan_normal_corners,
        "contour_separators": contour_separators,
        "multi_contour_polygons": multi_contour_polygons,
        "max_polygon_corners": max_polygon_corners,
        "max_contours_per_polygon": max_contours_per_polygon,
        "transform_only": vertex_count == 0 and polygon_count == 0,
    }, None


def validate_corpus(root: Path) -> dict:
    counters: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    started = time.time()

    for path in root.rglob("*.hrc"):
        data = path.read_bytes()
        records = structural_records(data)
        candidates: list[tuple[int, int, bool]] = []

        outer = outer_class4(data)
        if outer is not None:
            payload_offset, _ = outer
            end = records[0][2] if records else len(data)
            candidates.append((payload_offset, end, True))

        for index, (class_id, payload_offset, offset, _) in enumerate(records):
            if class_id != 4:
                continue
            if index + 1 < len(records):
                next_record = records[index + 1]
                end = next_record[2] - next_record[3]
            else:
                end = len(data)
            candidates.append((payload_offset, end, False))

        counters["hrc_files"] += 1
        for payload_offset, end, is_outer in candidates:
            counters["class4_records"] += 1
            counters["outer_records" if is_outer else "nested_records"] += 1
            decoded, error = decode_class4(data, payload_offset, end)
            if decoded is None:
                failures[error or "unknown"] += 1
                continue

            counters["decoded_records"] += 1
            counters["decoded_outer" if is_outer else "decoded_nested"] += 1
            counters["vertices"] += decoded["vertex_count"]
            counters["polygons"] += decoded["polygon_count"]
            counters["simple_fan_triangles"] += decoded["simple_fan_triangles"]
            counters["nan_normal_corners"] += decoded["nan_normal_corners"]
            counters["contour_separators"] += decoded["contour_separators"]
            counters["multi_contour_polygons"] += decoded["multi_contour_polygons"]
            counters["max_contours_per_polygon"] = max(
                counters["max_contours_per_polygon"], decoded["max_contours_per_polygon"]
            )
            if decoded["contour_separators"]:
                counters["records_with_contour_separators"] += 1
            if decoded["max_polygon_corners"] > 32:
                counters["records_with_polygon_gt32"] += 1
            if decoded["transform_only"]:
                counters["transform_only_records"] += 1

    return {
        "schema": "bz2-class4-geometry-validation-v2",
        "source_root": str(root.resolve()),
        "summary": dict(counters),
        "failures": dict(failures),
        "seconds": round(time.time() - started, 3),
        "notes": [
            "simple_fan_triangles excludes multi-contour polygons because hole-aware triangulation is still required",
            "0xFFFFFFFF corner records are preserved as contour separators rather than treated as invalid vertex indices",
            "all-NaN normal triplets are valid missing-normal sentinels",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate_corpus(args.source_root)
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if not result["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

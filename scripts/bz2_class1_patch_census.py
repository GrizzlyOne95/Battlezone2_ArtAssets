#!/usr/bin/env python3
"""Census outer class-1 Softimage patch records across a BZ2 source archive.

The binary layout was derived from the original bz2_art corpus and is intentionally
reported with a numeric ``surface_type_code`` until its Softimage enum mapping is
independently established. This tool parses source geometry/approximation state;
it does not claim that the current glTF tessellator reproduces each spline type.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import struct
from pathlib import Path
from typing import Any

import bz2_full_extract as full


def _outer_record(data: bytes) -> dict[str, Any] | None:
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


def decode_class1_patch(data: bytes) -> dict[str, Any] | None:
    outer = _outer_record(data)
    if not outer or outer["class_id"] != 1:
        return None
    offset = int(outer["payload_offset"])
    if offset + 6 > len(data):
        raise ValueError("short class-1 patch header")
    surface_type_code, u_count, v_count = struct.unpack_from(">HHH", data, offset)
    point_count = u_count * v_count
    if point_count <= 0 or point_count > 2_000_000:
        raise ValueError(f"implausible class-1 control-point count: {point_count}")
    control_start = offset + 6
    control_end = control_start + point_count * 12
    minimum_size = control_end + 54 + point_count * 2 + 36
    if minimum_size > len(data):
        raise ValueError("short class-1 patch payload")

    values = struct.unpack_from(f">{point_count * 3}f", data, control_start)
    control_points = [tuple(float(v) for v in values[i : i + 3]) for i in range(0, len(values), 3)]
    if not all(math.isfinite(v) and abs(v) < 1.0e9 for point in control_points for v in point):
        raise ValueError("invalid class-1 control point")

    post = control_end
    u_closed, v_closed = struct.unpack_from(">HH", data, post)
    u_tension, v_tension = struct.unpack_from(">ff", data, post + 4)
    u_step, v_step, u_curve, v_curve = struct.unpack_from(">HHHH", data, post + 12)
    reserved8 = data[post + 20 : post + 28]
    approx_type = struct.unpack_from(">I", data, post + 28)[0]
    view_dep = struct.unpack_from(">H", data, post + 32)[0]
    spatial, curv_u, curv_v = struct.unpack_from(">fff", data, post + 34)
    rec_min, rec_max = struct.unpack_from(">HH", data, post + 46)
    recursion = struct.unpack_from(">I", data, post + 50)[0]
    tags = list(struct.unpack_from(f">{point_count}H", data, post + 54))
    srt_offset = post + 54 + point_count * 2
    srt = tuple(float(v) for v in struct.unpack_from(">9f", data, srt_offset))
    if not all(math.isfinite(v) and abs(v) < 1.0e9 for v in srt):
        raise ValueError("invalid class-1 local SRT")

    return {
        "name": outer["name"],
        "outer_subtype": outer["subtype"],
        "surface_type_code": surface_type_code,
        "u_count": u_count,
        "v_count": v_count,
        "control_point_count": point_count,
        "u_closed": bool(u_closed),
        "v_closed": bool(v_closed),
        "u_tension": float(u_tension),
        "v_tension": float(v_tension),
        "u_step": u_step,
        "v_step": v_step,
        "u_curve": u_curve,
        "v_curve": v_curve,
        "reserved8_hex": reserved8.hex(),
        "approx_type": approx_type,
        "view_dep": view_dep,
        "spatial": float(spatial),
        "curv_u": float(curv_u),
        "curv_v": float(curv_v),
        "rec_min": rec_min,
        "rec_max": rec_max,
        "recursion": recursion,
        "nonzero_tag_count": sum(value != 0 for value in tags),
        "srt_offset": srt_offset,
        "srt": list(srt),
        "bounds": {
            "min": [min(point[axis] for point in control_points) for axis in range(3)],
            "max": [max(point[axis] for point in control_points) for axis in range(3)],
        },
    }


def census(source: Path, *, include_embedded_zips: bool = True) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    with full.prepared_source(source) as (primary, source_info):
        with full.prepared_scene_sources(primary, include_embedded_zips=include_embedded_zips) as (roots, _scenes, sources):
            labels = {str(primary.resolve()): "primary"}
            for item in sources:
                root = item.get("modelsdirectory")
                if root:
                    labels[str(Path(root).resolve())] = str(item.get("label") or "primary")
            for root in roots:
                root = root.resolve()
                label = labels.get(str(root), "primary" if root == primary.resolve() else root.name)
                for path in sorted(root.rglob("*.hrc")):
                    data = path.read_bytes()
                    outer = _outer_record(data)
                    if not outer or outer["class_id"] != 1:
                        continue
                    decoded = decode_class1_patch(data)
                    if decoded is None:
                        continue
                    decoded.update(
                        {
                            "source_label": label,
                            "path": path.relative_to(root).as_posix(),
                            "file_size": len(data),
                        }
                    )
                    records.append(decoded)

    profile_counts = collections.Counter(
        (
            item["surface_type_code"], item["outer_subtype"], item["u_count"], item["v_count"],
            item["u_closed"], item["v_closed"], item["u_tension"], item["v_tension"],
            item["u_step"], item["v_step"], item["u_curve"], item["v_curve"],
        )
        for item in records
    )
    return {
        "schema": "bz2-class1-patch-census-v1",
        "source": source_info,
        "discovered_sources": sources,
        "counts": {
            "class1_outer_hrc_count": len(records),
            "by_source": dict(collections.Counter(item["source_label"] for item in records)),
            "surface_type_code_counts": dict(collections.Counter(str(item["surface_type_code"]) for item in records)),
            "nonzero_tag_record_count": sum(item["nonzero_tag_count"] > 0 for item in records),
        },
        "profiles": [
            {
                "count": count,
                "surface_type_code": key[0], "outer_subtype": key[1], "u_count": key[2], "v_count": key[3],
                "u_closed": key[4], "v_closed": key[5], "u_tension": key[6], "v_tension": key[7],
                "u_step": key[8], "v_step": key[9], "u_curve": key[10], "v_curve": key[11],
            }
            for key, count in sorted(profile_counts.items())
        ],
        "records": records,
        "notes": [
            "All parsed class-1 roots use the same control-lattice/post-field/tag/SRT envelope.",
            "surface_type_code remains numeric until the source enum is independently proven.",
            "Parsing these source fields does not by itself validate direct control-cage triangulation as the final patch evaluator.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-embedded-zips", action="store_true")
    args = parser.parse_args()
    payload = census(args.source, include_embedded_zips=not args.no_embedded_zips)
    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Census outer and nested class-1 Softimage patch records across BZ2 source.

The binary envelope is corpus-derived. ``surface_type_code`` intentionally stays
numeric until the legacy Softimage enum mapping/evaluator is independently proven.
This tool parses geometry and approximation state; it does not claim that direct
control-cage triangulation is an exact spline reconstruction.
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
import bz2_hrc_tree_probe as hrc_tree


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


def decode_class1_payload(data: bytes, offset: int, *, end: int | None = None) -> dict[str, Any]:
    """Decode the common class-1 patch payload at ``offset``.

    The post-control layout is validated by 183 class-1 records in the supplied
    archive (29 outer roots + 154 nested records). The tagged-point section is N
    u16 flags followed by a zero u16 terminator; the nine-float SRT follows that
    terminator. This boundary is important: treating recursion as u32 shifts tags.
    """
    limit = len(data) if end is None else min(len(data), end)
    if offset + 6 > limit:
        raise ValueError("short class-1 patch header")
    surface_type_code, u_count, v_count = struct.unpack_from(">HHH", data, offset)
    point_count = u_count * v_count
    if point_count <= 0 or point_count > 2_000_000:
        raise ValueError(f"implausible class-1 control-point count: {point_count}")
    control_start = offset + 6
    control_end = control_start + point_count * 12
    srt_offset = control_end + 54 + point_count * 2
    if srt_offset + 36 > limit:
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
    recursion = struct.unpack_from(">H", data, post + 50)[0]
    tags = list(struct.unpack_from(f">{point_count}H", data, post + 52))
    tag_terminator = struct.unpack_from(">H", data, post + 52 + point_count * 2)[0]
    srt = tuple(float(v) for v in struct.unpack_from(">9f", data, srt_offset))
    if not all(math.isfinite(v) and abs(v) < 1.0e9 for v in srt):
        raise ValueError("invalid class-1 local SRT")

    return {
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
        "tag_values": tags,
        "tag_terminator": tag_terminator,
        "srt_offset": srt_offset,
        "srt": list(srt),
        "bounds": {
            "min": [min(point[axis] for point in control_points) for axis in range(3)],
            "max": [max(point[axis] for point in control_points) for axis in range(3)],
        },
    }


def _record_end(records: list[dict], index: int, data_length: int) -> int:
    if index + 1 >= len(records):
        return data_length
    following = records[index + 1]
    return int(following["offset"]) - int(following["zero_run"])


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
                    if outer and outer["class_id"] == 1:
                        decoded = decode_class1_payload(data, int(outer["payload_offset"]))
                        decoded.update(
                            {
                                "scope": "outer",
                                "name": outer["name"],
                                "class_id": 1,
                                "subtype": outer["subtype"],
                                "source_label": label,
                                "path": path.relative_to(root).as_posix(),
                                "file_size": len(data),
                            }
                        )
                        records.append(decoded)

                    nested = hrc_tree.discover_records(data)
                    for index, item in enumerate(nested):
                        if item.get("class_id") != 1:
                            continue
                        end = _record_end(nested, index, len(data))
                        decoded = decode_class1_payload(data, int(item["payload_offset"]), end=end)
                        decoded.update(
                            {
                                "scope": "nested",
                                "name": item["name"],
                                "class_id": 1,
                                "subtype": item["subtype"],
                                "source_offset": item["offset"],
                                "source_label": label,
                                "path": path.relative_to(root).as_posix(),
                                "file_size": len(data),
                                "trailing_bytes_before_next_record": end - (decoded["srt_offset"] + 36),
                            }
                        )
                        records.append(decoded)

    profile_counts = collections.Counter(
        (
            item["surface_type_code"], item["subtype"], item["u_count"], item["v_count"],
            item["u_closed"], item["v_closed"], item["u_tension"], item["v_tension"],
            item["u_step"], item["v_step"], item["u_curve"], item["v_curve"],
        )
        for item in records
    )
    return {
        "schema": "bz2-class1-patch-census-v2",
        "source": source_info,
        "discovered_sources": sources,
        "counts": {
            "class1_record_count": len(records),
            "outer_count": sum(item["scope"] == "outer" for item in records),
            "nested_count": sum(item["scope"] == "nested" for item in records),
            "by_source": dict(collections.Counter(item["source_label"] for item in records)),
            "surface_type_code_counts": dict(collections.Counter(str(item["surface_type_code"]) for item in records)),
            "nonzero_tag_record_count": sum(item["nonzero_tag_count"] > 0 for item in records),
            "nonzero_tag_terminator_count": sum(item["tag_terminator"] != 0 for item in records),
        },
        "profiles": [
            {
                "count": count,
                "surface_type_code": key[0], "subtype": key[1], "u_count": key[2], "v_count": key[3],
                "u_closed": key[4], "v_closed": key[5], "u_tension": key[6], "v_tension": key[7],
                "u_step": key[8], "v_step": key[9], "u_curve": key[10], "v_curve": key[11],
            }
            for key, count in sorted(profile_counts.items())
        ],
        "records": records,
        "notes": [
            "The common envelope validates for every class-1 outer and nested record found in the prepared source roots.",
            "recursion is u16; it is followed by N tagged-point u16 flags, then a zero u16 terminator, then the nine-float local SRT.",
            "surface_type_code remains numeric until the legacy source enum/evaluator is independently proven.",
            "Parsing source fields does not by itself validate direct control-cage triangulation as the final patch evaluator.",
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

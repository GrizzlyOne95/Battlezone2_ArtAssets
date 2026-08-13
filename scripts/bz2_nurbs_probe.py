#!/usr/bin/env python3
"""Decode and probe binary Softimage/BZ2 NURBS records.

The structural decoder is intentionally conservative: only NUL-terminated names
followed by proven tag-9/tag-10 records that pass full bounds and field validation
are promoted as parametric objects. Generic numeric scans remain forensic evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

PRINTABLE_RE = re.compile(rb"[ -~]{4,}")
PARAMETRIC_NAME_RE = re.compile(r"(?:^|[-_])(?:nurbs|surf|surface|spline|curve)\w*", re.IGNORECASE)
DEFAULT_REPORT_ROOT = Path("artifacts/reports/nurbs_probes")


@dataclass(frozen=True)
class StringAnchor:
    offset: int
    value: str
    parametric: bool


@dataclass(frozen=True)
class IntCandidate:
    offset: int
    value: int


@dataclass(frozen=True)
class FloatRun:
    offset: int
    count: int
    minimum: float
    maximum: float
    sample: list[float]


@dataclass(frozen=True)
class KnotCandidate:
    offset: int
    count: int
    minimum: float
    maximum: float
    distinct_rounded: int
    sample: list[float]


@dataclass(frozen=True)
class VectorRun:
    offset: int
    width: int
    vector_count: int
    mins: list[float]
    maxs: list[float]
    sample: list[list[float]]


def _finite_reasonable(value: float, limit: float) -> bool:
    magnitude = abs(value)
    return math.isfinite(value) and magnitude <= limit and (value == 0.0 or magnitude >= 1e-30)


def _read_f32(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]


def _iter_aligned_floats(data: bytes, start: int, end: int) -> Iterable[tuple[int, float]]:
    start = max(0, start + ((4 - start % 4) % 4))
    end = min(len(data), end)
    for offset in range(start, end - 3, 4):
        yield offset, _read_f32(data, offset)


def extract_string_anchors(data: bytes) -> list[StringAnchor]:
    anchors: list[StringAnchor] = []
    for match in PRINTABLE_RE.finditer(data):
        value = match.group(0).decode("latin-1", errors="replace").strip()
        if not value:
            continue
        anchors.append(StringAnchor(offset=match.start(), value=value, parametric=bool(PARAMETRIC_NAME_RE.search(value))))
    return anchors


def find_int_candidates(data: bytes, start: int, end: int, maximum: int) -> list[IntCandidate]:
    results: list[IntCandidate] = []
    aligned = max(0, start + ((4 - start % 4) % 4))
    for offset in range(aligned, min(end, len(data)) - 3, 4):
        value = struct.unpack_from("<I", data, offset)[0]
        if 1 <= value <= maximum:
            results.append(IntCandidate(offset=offset, value=value))
    return results


def find_float_runs(data: bytes, start: int, end: int, *, min_count: int, abs_limit: float, max_results: int) -> list[FloatRun]:
    runs: list[FloatRun] = []
    current: list[tuple[int, float]] = []

    def flush() -> None:
        nonlocal current
        if len(current) >= min_count:
            values = [value for _, value in current]
            runs.append(FloatRun(offset=current[0][0], count=len(values), minimum=min(values), maximum=max(values), sample=[round(v, 7) for v in values[:16]]))
        current = []

    for offset, value in _iter_aligned_floats(data, start, end):
        if _finite_reasonable(value, abs_limit):
            current.append((offset, value))
        else:
            flush()
    flush()
    runs.sort(key=lambda run: (-run.count, run.offset))
    return runs[:max_results]


def _monotonic_runs(values: Sequence[tuple[int, float]], epsilon: float) -> Iterable[list[tuple[int, float]]]:
    current: list[tuple[int, float]] = []
    previous: float | None = None
    for item in values:
        _, value = item
        if previous is None or value + epsilon >= previous:
            current.append(item)
        else:
            if current:
                yield current
            current = [item]
        previous = value
    if current:
        yield current


def find_knot_candidates(data: bytes, start: int, end: int, *, min_count: int, abs_limit: float, epsilon: float, max_results: int) -> list[KnotCandidate]:
    floats = [item for item in _iter_aligned_floats(data, start, end) if _finite_reasonable(item[1], abs_limit)]
    candidates: list[KnotCandidate] = []
    for run in _monotonic_runs(floats, epsilon):
        if len(run) < min_count:
            continue
        vals = [v for _, v in run]
        distinct = len({round(v, 6) for v in vals})
        if distinct < 2:
            continue
        candidates.append(KnotCandidate(offset=run[0][0], count=len(vals), minimum=min(vals), maximum=max(vals), distinct_rounded=distinct, sample=[round(v, 7) for v in vals[:20]]))
    candidates.sort(key=lambda item: (-item.count, -item.distinct_rounded, item.offset))
    return candidates[:max_results]


def find_vector_runs(data: bytes, start: int, end: int, *, width: int, min_vectors: int, abs_limit: float, max_results: int) -> list[VectorRun]:
    floats = list(_iter_aligned_floats(data, start, end))
    results: list[VectorRun] = []
    i = 0
    while i < len(floats):
        begin = i
        while i < len(floats) and _finite_reasonable(floats[i][1], abs_limit):
            i += 1
        values = floats[begin:i]
        usable = (len(values) // width) * width
        if usable >= width * min_vectors:
            vectors = [[values[j + k][1] for k in range(width)] for j in range(0, usable, width)]
            mins = [min(vec[k] for vec in vectors) for k in range(width)]
            maxs = [max(vec[k] for vec in vectors) for k in range(width)]
            results.append(VectorRun(offset=values[0][0], width=width, vector_count=len(vectors), mins=[round(v, 7) for v in mins], maxs=[round(v, 7) for v in maxs], sample=[[round(v, 7) for v in vec] for vec in vectors[:8]]))
        i += 1
    results.sort(key=lambda item: (-item.vector_count, item.offset))
    return results[:max_results]


def _window_for_anchor(anchor: StringAnchor, parametric: Sequence[StringAnchor], file_size: int, *, before: int, after: int) -> tuple[int, int]:
    start = max(0, anchor.offset - before)
    end = min(file_size, anchor.offset + after)
    for candidate in parametric:
        if candidate.offset > anchor.offset:
            end = min(end, candidate.offset)
            break
    if end <= start:
        end = min(file_size, start + after)
    return start, end


def _decode_control_points(data: bytes, offset: int, count: int) -> tuple[list[dict], int]:
    points: list[dict] = []
    for index in range(count):
        if offset + 36 > len(data):
            raise ValueError(f"control-point record {index} overruns source")
        x, y, z, w = struct.unpack_from(">4d", data, offset)
        flags = data[offset + 32 : offset + 36].hex()
        if not all(math.isfinite(value) for value in (x, y, z, w)):
            raise ValueError(f"non-finite control point at index {index}")
        points.append({"index": index, "offset": offset, "xyzw": [round(x, 12), round(y, 12), round(z, 12), round(w, 12)], "flags_hex": flags})
        offset += 36
    return points, offset


def _point_summary(points: list[dict]) -> dict:
    if not points:
        return {}
    coords = [point["xyzw"] for point in points]
    flags: dict[str, int] = {}
    for point in points:
        flags[point["flags_hex"]] = flags.get(point["flags_hex"], 0) + 1
    return {"count": len(points), "xyz_min": [min(point[i] for point in coords) for i in range(3)], "xyz_max": [max(point[i] for point in coords) for i in range(3)], "weight_min": min(point[3] for point in coords), "weight_max": max(point[3] for point in coords), "flags_histogram": dict(sorted(flags.items()))}


def _read_be_doubles(data: bytes, offset: int, count: int) -> tuple[list[float], int]:
    if count < 0 or count > 1_000_000:
        raise ValueError(f"implausible double count: {count}")
    end = offset + 8 * count
    if end > len(data):
        raise ValueError("double array overruns source")
    values = list(struct.unpack_from(f">{count}d", data, offset)) if count else []
    if not all(math.isfinite(value) for value in values):
        raise ValueError("non-finite value in double array")
    return [round(value, 12) for value in values], end


def _standardize_si_knots(knots: list[float], closed: bool, order: int, control_count: int) -> dict | None:
    """Convert proven Softimage SI-NURBS knot layouts to conventional vectors."""
    if not knots or order < 1 or control_count < 1:
        return None
    degree = order - 1
    if any(knots[index + 1] < knots[index] for index in range(len(knots) - 1)):
        return None
    if not closed:
        if len(knots) != control_count + order - 2:
            return None
        standard = [knots[0], *knots, knots[-1]]
        return {"strategy": "si_open_endpoint_duplication", "standard_knots": standard, "control_wrap_count": 0, "effective_control_count": control_count, "parameter_domain": [standard[degree], standard[control_count]]}
    if len(knots) != control_count + 1 or degree == 0:
        return None
    deltas = [knots[index + 1] - knots[index] for index in range(len(knots) - 1)]
    if not deltas or any(delta < 0.0 for delta in deltas) or not any(delta > 0.0 for delta in deltas):
        return None
    left_reversed: list[float] = []
    value = knots[0]
    for index in range(degree):
        value -= deltas[-1 - (index % len(deltas))]
        left_reversed.append(value)
    left = list(reversed(left_reversed))
    right: list[float] = []
    value = knots[-1]
    for index in range(degree):
        value += deltas[index % len(deltas)]
        right.append(value)
    standard = [*left, *knots, *right]
    effective_count = control_count + degree
    if len(standard) != effective_count + order:
        return None
    return {"strategy": "si_closed_periodic_wrap", "standard_knots": standard, "control_wrap_count": degree, "effective_control_count": effective_count, "parameter_domain": [knots[0], knots[-1]]}


def _decode_surface_trim_sections(data: bytes, offset: int, surface_record: dict) -> dict | None:
    if offset + 6 > len(data):
        return None
    prefix_unknown = struct.unpack_from(">I", data, offset)[0]
    trim_count = struct.unpack_from(">H", data, offset + 4)[0]
    if trim_count > 256:
        return None
    cursor = offset + 6
    trims: list[dict] = []
    for trim_index in range(trim_count):
        start = cursor
        if cursor + 14 > len(data):
            return None
        is_boundary_raw = struct.unpack_from(">H", data, cursor)[0]
        projection_type_raw = struct.unpack_from(">I", data, cursor + 2)[0]
        order = struct.unpack_from(">I", data, cursor + 6)[0]
        control_count = struct.unpack_from(">I", data, cursor + 10)[0]
        cursor += 14
        if is_boundary_raw not in (0, 1) or not (1 <= order <= 32 and 1 <= control_count <= 100_000):
            return None
        try:
            points, cursor = _decode_control_points(data, cursor, control_count)
            if cursor + 10 > len(data):
                return None
            closed = bool(struct.unpack_from(">H", data, cursor)[0])
            parameterization_code = struct.unpack_from(">I", data, cursor + 2)[0]
            knot_count = struct.unpack_from(">I", data, cursor + 6)[0]
            cursor += 10
            knots, cursor = _read_be_doubles(data, cursor, knot_count)
            if cursor + 16 > len(data):
                return None
            parameter_range = list(struct.unpack_from(">2d", data, cursor))
            cursor += 16
            if not all(math.isfinite(value) for value in parameter_range):
                return None
            parameter_range = [round(value, 12) for value in parameter_range]
        except (ValueError, struct.error, OverflowError):
            return None
        conversion = _standardize_si_knots(knots, closed, order, control_count)
        trailer = data[cursor : cursor + 10]
        if len(trailer) != 10:
            return None
        cursor += 10
        ranges = surface_record.get("parameter_ranges") or {}
        u_range = ranges.get("u")
        v_range = ranges.get("v")
        uv_candidate = False
        if u_range and v_range and points:
            tolerance = 1e-5 * max(1.0, abs(u_range[1] - u_range[0]), abs(v_range[1] - v_range[0]))
            uv_candidate = all(abs(point["xyzw"][2]) <= tolerance and u_range[0] - tolerance <= point["xyzw"][0] <= u_range[1] + tolerance and v_range[0] - tolerance <= point["xyzw"][1] <= v_range[1] + tolerance for point in points)
        trims.append({"index": trim_index, "record_start": start, "is_boundary_raw": bool(is_boundary_raw), "projection_type_raw": projection_type_raw, "order": order, "degree_inferred": order - 1, "control_count": control_count, "control_points": points, "control_point_summary": _point_summary(points), "closed": closed, "parameterization_code": parameterization_code, "knot_count": knot_count, "knots_si": knots, "knots_standard": (conversion or {}).get("standard_knots"), "knot_conversion": conversion, "parameter_range": parameter_range, "uv_space_candidate": uv_candidate, "trailer_hex": trailer.hex(), "decoded_through": cursor, "reconstruction_ready": conversion is not None})
    return {"prefix_unknown_u32": prefix_unknown, "trim_count": trim_count, "trims": trims, "decoded_through": cursor}


def decode_parametric_record(data: bytes, anchor: StringAnchor) -> dict | None:
    name_bytes = anchor.value.encode("latin-1", errors="replace")
    pos = anchor.offset + len(name_bytes)
    if pos >= len(data) or data[pos] != 0:
        return None
    pos += 1
    if pos + 2 > len(data):
        return None
    tag = struct.unpack_from(">H", data, pos)[0]
    record_start = anchor.offset
    pos += 2
    try:
        if tag == 0x0009:
            if pos + 8 > len(data):
                return None
            order, control_count = struct.unpack_from(">2I", data, pos)
            pos += 8
            if not (1 <= order <= 32 and 1 <= control_count <= 100_000):
                return None
            points, pos = _decode_control_points(data, pos, control_count)
            if pos + 10 > len(data):
                return None
            closed = bool(struct.unpack_from(">H", data, pos)[0])
            parameterization_code = struct.unpack_from(">I", data, pos + 2)[0]
            knot_count = struct.unpack_from(">I", data, pos + 6)[0]
            pos += 10
            knots, pos = _read_be_doubles(data, pos, knot_count)
            parameter_range = None
            if pos + 16 <= len(data):
                r0, r1 = struct.unpack_from(">2d", data, pos)
                if math.isfinite(r0) and math.isfinite(r1):
                    parameter_range = [round(r0, 12), round(r1, 12)]
                    pos += 16
            conversion = _standardize_si_knots(knots, closed, order, control_count)
            return {"kind": "nurbs_curve", "tag": tag, "record_start": record_start, "decoded_through": pos, "order": order, "degree_inferred": order - 1, "control_count": control_count, "control_points": points, "control_point_summary": _point_summary(points), "closed": closed, "parameterization_code": parameterization_code, "knot_count": knot_count, "knots_si": knots, "knots_standard_open": (conversion or {}).get("standard_knots") if not closed else None, "knots_standard": (conversion or {}).get("standard_knots"), "knot_conversion": conversion, "parameter_range": parameter_range, "reconstruction_ready_open": conversion is not None and not closed, "reconstruction_ready": conversion is not None}

        if tag == 0x000A:
            if pos + 16 > len(data):
                return None
            order_u, order_v, count_u, count_v = struct.unpack_from(">4I", data, pos)
            pos += 16
            if not (1 <= order_u <= 32 and 1 <= order_v <= 32 and 1 <= count_u <= 10_000 and 1 <= count_v <= 10_000 and count_u * count_v <= 1_000_000):
                return None
            points, pos = _decode_control_points(data, pos, count_u * count_v)
            if pos + 12 > len(data):
                return None
            closed_u, closed_v = struct.unpack_from(">2H", data, pos)
            parameterization_code = struct.unpack_from(">I", data, pos + 4)[0]
            knot_count_u = struct.unpack_from(">I", data, pos + 8)[0]
            pos += 12
            knots_u, pos = _read_be_doubles(data, pos, knot_count_u)
            if pos + 4 > len(data):
                return None
            knot_count_v = struct.unpack_from(">I", data, pos)[0]
            pos += 4
            knots_v, pos = _read_be_doubles(data, pos, knot_count_v)
            parameter_ranges = None
            if pos + 32 <= len(data):
                ranges = struct.unpack_from(">4d", data, pos)
                if all(math.isfinite(value) for value in ranges):
                    parameter_ranges = {"u": [round(ranges[0], 12), round(ranges[1], 12)], "v": [round(ranges[2], 12), round(ranges[3], 12)]}
                    pos += 32
            conversion_u = _standardize_si_knots(knots_u, bool(closed_u), order_u, count_u)
            conversion_v = _standardize_si_knots(knots_v, bool(closed_v), order_v, count_v)
            record = {"kind": "nurbs_surface", "tag": tag, "record_start": record_start, "decoded_through": pos, "order_u": order_u, "order_v": order_v, "degree_u_inferred": order_u - 1, "degree_v_inferred": order_v - 1, "control_count_u": count_u, "control_count_v": count_v, "control_points": points, "control_point_summary": _point_summary(points), "closed_u": bool(closed_u), "closed_v": bool(closed_v), "parameterization_code": parameterization_code, "knot_count_u": knot_count_u, "knot_count_v": knot_count_v, "knots_u_si": knots_u, "knots_v_si": knots_v, "knots_u_standard_open": (conversion_u or {}).get("standard_knots") if not bool(closed_u) else None, "knots_v_standard_open": (conversion_v or {}).get("standard_knots") if not bool(closed_v) else None, "knots_u_standard": (conversion_u or {}).get("standard_knots"), "knots_v_standard": (conversion_v or {}).get("standard_knots"), "knot_conversion_u": conversion_u, "knot_conversion_v": conversion_v, "parameter_ranges": parameter_ranges, "reconstruction_ready_open": conversion_u is not None and conversion_v is not None and not bool(closed_u) and not bool(closed_v), "reconstruction_ready": conversion_u is not None and conversion_v is not None}
            trim_section = _decode_surface_trim_sections(data, pos, record)
            if trim_section is not None:
                record["trim_section"] = trim_section
                record["trim_count"] = trim_section["trim_count"]
                record["decoded_through_trims"] = trim_section["decoded_through"]
            else:
                record["trim_section"] = None
                record["trim_count"] = None
            return record
    except (ValueError, struct.error, OverflowError):
        return None
    return None


def discover_parametric_anchors(data: bytes, anchors: list[StringAnchor] | None = None) -> tuple[list[StringAnchor], list[StringAnchor]]:
    source_anchors = anchors if anchors is not None else extract_string_anchors(data)
    decoded: list[StringAnchor] = []
    failed: list[StringAnchor] = []
    for anchor in source_anchors:
        name_bytes = anchor.value.encode("latin-1", errors="replace")
        pos = anchor.offset + len(name_bytes)
        if pos >= len(data) or data[pos] != 0 or pos + 3 > len(data):
            continue
        tag = struct.unpack_from(">H", data, pos + 1)[0]
        if tag not in (0x0009, 0x000A):
            continue
        if decode_parametric_record(data, anchor) is None:
            failed.append(anchor)
        else:
            decoded.append(anchor)
    return decoded, failed


def build_probe(path: Path, args: argparse.Namespace) -> dict:
    data = path.read_bytes()
    anchors = extract_string_anchors(data)
    name_pattern_anchors = [anchor for anchor in anchors if anchor.parametric]
    all_parametric, failed_tag_candidates = discover_parametric_anchors(data, anchors)
    selected_parametric = all_parametric
    if args.object:
        wanted = re.compile(args.object, re.IGNORECASE)
        selected_parametric = [anchor for anchor in all_parametric if wanted.search(anchor.value)]
    objects = []
    for anchor in selected_parametric[: args.max_objects]:
        start, end = _window_for_anchor(anchor, all_parametric, len(data), before=args.window_before, after=args.window_after)
        scan_start = min(end, anchor.offset + len(anchor.value.encode("latin-1", errors="replace")) + 1)
        decoded_parametric = decode_parametric_record(data, anchor)
        objects.append({"anchor": asdict(anchor), "decoded_parametric": decoded_parametric, "window": {"start": start, "scan_start": scan_start, "end": end, "size": end - start}, "nearby_strings": [asdict(item) for item in anchors if start <= item.offset < end][: args.max_strings], "integer_candidates": [asdict(item) for item in find_int_candidates(data, scan_start, end, args.max_dimension)][: args.max_ints], "float_runs": [asdict(item) for item in find_float_runs(data, scan_start, end, min_count=args.min_float_run, abs_limit=args.float_abs_limit, max_results=args.max_float_runs)], "knot_candidates": [asdict(item) for item in find_knot_candidates(data, scan_start, end, min_count=args.min_knot_run, abs_limit=args.knot_abs_limit, epsilon=args.knot_epsilon, max_results=args.max_knot_runs)], "vec3_runs": [asdict(item) for item in find_vector_runs(data, scan_start, end, width=3, min_vectors=args.min_vectors, abs_limit=args.float_abs_limit, max_results=args.max_vector_runs)], "vec4_runs": [asdict(item) for item in find_vector_runs(data, scan_start, end, width=4, min_vectors=args.min_vectors, abs_limit=args.float_abs_limit, max_results=args.max_vector_runs)]})
    decoded_records = [obj["decoded_parametric"] for obj in objects if obj.get("decoded_parametric")]
    kind_counts: dict[str, int] = {}
    for record in decoded_records:
        kind_counts[record["kind"]] = kind_counts.get(record["kind"], 0) + 1
    return {"schema": "bz2-nurbs-probe-v3", "source": str(path.as_posix()), "source_size": len(data), "source_sha256": hashlib.sha256(data).hexdigest(), "parametric_anchor_count": len(all_parametric), "name_pattern_anchor_count": len(name_pattern_anchors), "failed_tag_candidate_count": len(failed_tag_candidates), "failed_tag_candidates": [asdict(anchor) for anchor in failed_tag_candidates], "selected_anchor_count": len(selected_parametric), "decoded_parametric_count": len(decoded_records), "decoded_kind_counts": dict(sorted(kind_counts.items())), "anchors": [asdict(anchor) for anchor in selected_parametric], "objects": objects, "settings": {key: value for key, value in vars(args).items() if key not in {"path", "output"}}, "notes": ["Tag 0x0009 curve and 0x000A surface payload fields are structurally decoded and discovered independent of object naming conventions.", "Name-pattern anchor counts are retained only for comparison with the original 47-object soft-soldier test scope.", "Generic integer/float/vector candidates remain heuristic evidence.", "Offsets are absolute byte offsets in the source HRC."]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--object")
    parser.add_argument("--window-before", type=int, default=256)
    parser.add_argument("--window-after", type=int, default=65536)
    parser.add_argument("--max-objects", type=int, default=256)
    parser.add_argument("--max-strings", type=int, default=64)
    parser.add_argument("--max-ints", type=int, default=128)
    parser.add_argument("--max-dimension", type=int, default=4096)
    parser.add_argument("--min-float-run", type=int, default=12)
    parser.add_argument("--max-float-runs", type=int, default=24)
    parser.add_argument("--float-abs-limit", type=float, default=1_000_000.0)
    parser.add_argument("--min-knot-run", type=int, default=6)
    parser.add_argument("--max-knot-runs", type=int, default=24)
    parser.add_argument("--knot-abs-limit", type=float, default=100_000.0)
    parser.add_argument("--knot-epsilon", type=float, default=1e-6)
    parser.add_argument("--min-vectors", type=int, default=4)
    parser.add_argument("--max-vector-runs", type=int, default=16)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.path.is_file():
        raise SystemExit(f"HRC not found: {args.path}")
    payload = build_probe(args.path, args)
    output = args.output or DEFAULT_REPORT_ROOT / f"{args.path.stem}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {output}")
    print(f"parametric anchors: {payload['selected_anchor_count']} selected / {payload['parametric_anchor_count']} total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

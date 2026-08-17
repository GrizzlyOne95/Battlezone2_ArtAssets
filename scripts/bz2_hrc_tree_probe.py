#!/usr/bin/env python3
"""Decode nested model hierarchy and local SRT in binary Softimage HRC files.

Hierarchy depth is inferred from the zero-run preorder encoding. Transform/null
and joint nodes carry SRT immediately after their class/subtype payload. Proven
parametric records carry SRT after their complete NURBS payload:

* curve (class 9): decoded payload end + 12 bytes
* surface (class 10): decoded payload/trim end + 64 bytes

The parametric SRT offsets are corpus-validated against all 1,987 decoded
parametric records in the complete BZ2 art dump. Polygon-mesh (class 4) SRT is
recovered from structurally validated post-mesh envelope anchors (material, short/standard
tail, or t2d texture-reference). Baseline ambiguity is retained
in the report rather than hidden.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import struct
import sys
from pathlib import Path

NAME_RE = re.compile(rb"\x00\x01([ -~]{1,80})\x00")
KNOWN_CLASSES = {0, 1, 2, 4, 5, 6, 9, 10}
MESH_MATERIAL_RE = re.compile(rb"(?=\x00([\x01-\xff])\x00\x00([ -~]{1,80})\x00)")
MESH_SHORT_TAIL = bytes.fromhex("3f800000000000000004")
MESH_STANDARD_TAIL = bytes.fromhex(
    "0000000000000000000000000000000000000000000000000007000000003f800000000000000004"
)
MESH_STANDARD_TAIL_VARIANT_5 = bytes.fromhex(
    "0000000000000000000000050000000000000000000000000007000000003f800000000000000004"
)
MESH_STANDARD_TAIL_VARIANT_6 = bytes.fromhex(
    "0000000000000000000000060000000000000000000000000007000000003f800000000000000004"
)
MESH_STANDARD_TAIL_ZERO_UNIT = bytes.fromhex(
    "00000000000000000000000000000000000000000000000000070000000000000000000000000004"
)
MESH_MIRE_GRID_EXTENDED_TAIL = bytes.fromhex(
    "000000000000000100003e4ccccd3f6666663f19999a3f800000000000003e99999afffffffe"
    "000000000000000100014270000041700000417000000000000000000007000000003f800000000000000004"
)
CUSA_PREAMBLE_PREFIX = bytes.fromhex("0000000000000000000d00000000000000000000000000")


def _load_nurbs_probe():
    path = Path(__file__).with_name("bz2_nurbs_probe.py")
    spec = importlib.util.spec_from_file_location("bz2_nurbs_probe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load NURBS decoder from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


nurbs = _load_nurbs_probe()


def zero_run_before(data: bytes, offset: int) -> int:
    cursor = offset - 1
    while cursor >= 0 and data[cursor] == 0:
        cursor -= 1
    return offset - 1 - cursor


def _srt(values: tuple[float, ...], source: str, offset: int) -> dict | None:
    if len(values) != 9 or not all(math.isfinite(value) for value in values):
        return None
    if any(abs(value) > 1.0e9 for value in values):
        return None
    return {
        "scale": list(values[0:3]),
        "rotation_xyz": list(values[3:6]),
        "translation_xyz": list(values[6:9]),
        "source": source,
        "offset": offset,
    }


def outer_model(data: bytes) -> dict | None:
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
        "offset": marker,
        "string_offset": marker + 4,
    }


def _decode_parametric_at(data: bytes, string_offset: int, name: str) -> tuple[dict | None, dict | None]:
    anchor = nurbs.StringAnchor(offset=string_offset, value=name, parametric=True)
    record = nurbs.decode_parametric_record(data, anchor)
    if record is None:
        return None, None
    end = record.get("decoded_through_trims") or record.get("decoded_through")
    if not isinstance(end, int):
        return record, None
    if record["kind"] == "nurbs_curve":
        srt_offset = end + 12
        source = "post_parametric_payload_curve_plus_12"
    else:
        srt_offset = end + 64
        source = "post_parametric_payload_surface_plus_64"
    if srt_offset + 36 > len(data):
        return record, None
    values = struct.unpack_from(">9f", data, srt_offset)
    return record, _srt(values, source, srt_offset)


def _attach_srt(data: bytes, item: dict, string_offset: int) -> None:
    class_id = item["class_id"]
    if class_id in {0, 5}:
        offset = item["payload_offset"]
        if offset + 36 <= len(data):
            decoded = _srt(
                struct.unpack_from(">9f", data, offset),
                "immediate_transform_payload",
                offset,
            )
            if decoded:
                item["local_srt"] = decoded
        return
    if class_id in {9, 10}:
        record, decoded = _decode_parametric_at(data, string_offset, item["name"])
        if record is not None:
            item["parametric_kind"] = record["kind"]
            item["parametric_record_start"] = record["record_start"]
            item["parametric_decoded_through"] = record.get("decoded_through_trims") or record.get("decoded_through")
            item["trim_count"] = record.get("trim_count") or 0
            item["reconstruction_ready"] = bool(record.get("reconstruction_ready"))
        if decoded:
            item["local_srt"] = decoded


def _plausible_mesh_srt(values: tuple[float, ...]) -> bool:
    return (
        len(values) == 9
        and all(math.isfinite(value) for value in values)
        and all(1.0e-9 < abs(value) < 1.0e8 for value in values[:3])
        and all(abs(value) < 1.0e9 for value in values[3:])
    )


def _decode_mesh_srt_between(
    data: bytes, start: int, end: int, next_zero_run: int
) -> dict | None:
    """Recover class-4 local SRT from the model's post-mesh envelope.

    The envelope forms below were regression-checked against all 17 class-4
    objects in the historical soft-soldier conversion, then against the complete
    multi-record NURBS dependency corpus (1,204/1,204 placeable records).
    """
    if end <= start or not (0 <= next_zero_run <= end - start):
        return None
    tail_end = end - next_zero_run

    # All authored material-slot tags (not only slot 1) use the same SRT-before-
    # slot envelope. Zero-width lookahead is required because bytes in the final
    # SRT float can themselves resemble a shorter slot signature and would make a
    # consuming regex skip the genuine marker one byte later. This consolidates
    # the exporter's archive-proven slot fallback into the shared tree probe.
    material_candidates: list[tuple[int, int, str, tuple[float, ...]]] = []
    for match in MESH_MATERIAL_RE.finditer(data, start, tail_end):
        srt_offset = match.start() - 36
        if srt_offset < start:
            continue
        values = struct.unpack_from(">9f", data, srt_offset)
        if _plausible_mesh_srt(values):
            material_candidates.append(
                (
                    srt_offset,
                    match.group(1)[0],
                    match.group(2).decode("latin-1", errors="replace"),
                    values,
                )
            )
    if material_candidates:
        trailing = [
            item for item in material_candidates if item[0] >= max(start, end - 4096)
        ]
        chosen = min(trailing, key=lambda item: item[0]) if trailing else max(
            material_candidates, key=lambda item: item[0]
        )
        offset, slot, material_name, values = chosen
        source = "pre_mesh_material_block" if slot == 1 else "pre_mesh_material_slot_block"
        decoded = _srt(values, source, offset)
        if decoded:
            decoded["anchor_slot"] = slot
            decoded["anchor_name"] = material_name
            return decoded

    # Softimage custom-attribute blocks begin with a 24-byte preamble followed by
    # the ASCII CUSA tag. Across all 40 CUSA records in the supplied corpus the
    # model SRT is exactly 36 bytes immediately before that preamble. Thirty-nine
    # are class-2 effect records; the sole class-4 occurrence is the movie
    # explode1 ROOT, whose recovered SRT independently matches its DSC ENVIRONMENT
    # SRT. Accept only the two observed preamble terminal tags (2/3).
    cursor = start
    cusa_candidates: list[tuple[int, tuple[float, ...]]] = []
    while True:
        cusa_offset = data.find(b"CUSA", cursor, tail_end)
        if cusa_offset < 0:
            break
        preamble_offset = cusa_offset - 24
        srt_offset = preamble_offset - 36
        if srt_offset >= start and preamble_offset >= start:
            preamble = data[preamble_offset:cusa_offset]
            if (
                len(preamble) == 24
                and preamble[:23] == CUSA_PREAMBLE_PREFIX
                and preamble[23] in {2, 3}
            ):
                values = struct.unpack_from(">9f", data, srt_offset)
                if _plausible_mesh_srt(values):
                    cusa_candidates.append((srt_offset, values))
        cursor = cusa_offset + 4
    if cusa_candidates:
        offset, values = max(cusa_candidates, key=lambda item: item[0])
        decoded = _srt(values, "pre_custom_attribute_cusa", offset)
        if decoded:
            decoded["anchor_name"] = "CUSA"
            return decoded

    # PATCH: a large class-4 corpus variant appends even-length zero padding
    # after the otherwise standard/short post-mesh tail. The older decoder
    # required the tail marker to end exactly at ``tail_end``, leaving valid
    # SRTs unresolved (hardpoints, collision helpers, Walker dork__h, etc.).
    # Accept the last tail marker near the record end only when every following
    # byte is zero, then decode the nine floats immediately before the marker.
    # This preserves the exact-tail case and avoids scanning arbitrary mesh data.
    def srt_before_zero_padded_tail(marker: bytes, source: str) -> dict | None:
        search_start = max(start, tail_end - 512)
        marker_offset = data.rfind(marker, search_start, tail_end)
        if marker_offset < 0:
            return None
        suffix = data[marker_offset + len(marker) : tail_end]
        if any(suffix):
            return None
        offset = marker_offset - 36
        if offset < start:
            return None
        values = struct.unpack_from(">9f", data, offset)
        if not _plausible_mesh_srt(values):
            return None
        decoded_source = source if not suffix else source + "_zero_padded"
        return _srt(values, decoded_source, offset)

    for marker, source in (
        (MESH_STANDARD_TAIL, "pre_mesh_standard_tail"),
        (MESH_STANDARD_TAIL_VARIANT_5, "pre_mesh_standard_tail_variant_5"),
        (MESH_STANDARD_TAIL_VARIANT_6, "pre_mesh_standard_tail_variant_6"),
        (MESH_STANDARD_TAIL_ZERO_UNIT, "pre_mesh_standard_tail_zero_unit"),
        (MESH_MIRE_GRID_EXTENDED_TAIL, "pre_mesh_mire_grid_extended_tail"),
        (MESH_SHORT_TAIL, "pre_mesh_short_tail"),
    ):
        decoded = srt_before_zero_padded_tail(marker, source)
        if decoded:
            return decoded

    texture_candidates: list[tuple[int, str, tuple[float, ...]]] = []
    cursor = start
    while True:
        texture_offset = data.find(b"t2d", cursor, tail_end)
        if texture_offset < 0:
            break
        name_end = data.find(b"\0", texture_offset, tail_end)
        texture_name = (
            data[texture_offset:name_end].decode("latin-1", errors="replace")
            if name_end >= 0
            else "t2d"
        )
        for gap in (5, 6):
            srt_offset = texture_offset - gap - 36
            if srt_offset < start:
                continue
            values = struct.unpack_from(">9f", data, srt_offset)
            if _plausible_mesh_srt(values):
                texture_candidates.append((srt_offset, texture_name, values))
        cursor = texture_offset + 3
    if texture_candidates:
        offset, texture_name, values = max(texture_candidates, key=lambda item: item[0])
        decoded = _srt(values, "pre_mesh_texture_reference", offset)
        if decoded:
            decoded["anchor_name"] = texture_name
            return decoded
    return None


def _attach_mesh_srt(data: bytes, records: list[dict]) -> None:
    for index, item in enumerate(records):
        if item.get("class_id") != 4:
            continue
        end = records[index + 1]["offset"] if index + 1 < len(records) else len(data)
        next_zero_run = records[index + 1]["zero_run"] if index + 1 < len(records) else 0
        decoded = _decode_mesh_srt_between(data, item["offset"], end, next_zero_run)
        if decoded:
            item["local_srt"] = decoded


def discover_records(data: bytes) -> list[dict]:
    records = []
    for match in NAME_RE.finditer(data):
        payload = match.end()
        if payload + 4 > len(data):
            continue
        class_id = int.from_bytes(data[payload : payload + 2], "big")
        subtype = int.from_bytes(data[payload + 2 : payload + 4], "big")
        zeros = zero_run_before(data, match.start())
        if class_id not in KNOWN_CLASSES or zeros < 20 or zeros % 2:
            continue
        # Archive census: class 0 is a hierarchy transform/null only for
        # subtype 0. The 13 class-0/nonzero signatures across 7,665 HRCs
        # are internal/helper payload records (cls0, Face, t); treating them
        # as nodes creates garbage immediate SRTs and false parent scopes.
        if class_id == 0 and subtype != 0:
            continue
        item = {
            "name": match.group(1).decode("latin-1", errors="replace"),
            "offset": match.start(),
            "string_offset": match.start(1),
            "payload_offset": payload + 4,
            "class_id": class_id,
            "subtype": subtype,
            "zero_run": zeros,
        }
        _attach_srt(data, item, match.start(1))
        records.append(item)
    _attach_mesh_srt(data, records)
    return records


def try_baseline(records: list[dict], baseline: int) -> tuple[bool, list[int]]:
    if not records:
        return True, []
    depths = [1]
    depth = 1
    for record in records[1:]:
        numerator = baseline - record["zero_run"]
        if numerator % 2:
            return False, []
        delta = numerator // 2
        if delta > 1:
            return False, []
        depth += delta
        if depth < 1:
            return False, []
        depths.append(depth)
    return True, depths


def infer_baselines(records: list[dict]) -> list[dict]:
    candidates = []
    for baseline in range(18, 42, 2):
        valid, depths = try_baseline(records, baseline)
        if valid:
            candidates.append(
                {
                    "baseline_zero_run": baseline,
                    "max_depth": max(depths, default=0),
                    "final_depth": depths[-1] if depths else 0,
                }
            )
    return candidates


def apply_tree(records: list[dict], outer_name: str, baseline: int) -> list[dict]:
    valid, depths = try_baseline(records, baseline)
    if not valid:
        raise ValueError(f"baseline {baseline} does not produce a valid preorder walk")
    stack = [outer_name]
    output = []
    for record, depth in zip(records, depths):
        if depth - 1 >= len(stack):
            raise ValueError("missing parent while reconstructing preorder stack")
        item = dict(record)
        item["depth"] = depth
        item["parent_name"] = stack[depth - 1]
        stack = stack[:depth]
        stack.append(record["name"])
        output.append(item)
    return output


def probe(path: Path, forced_baseline: int | None = None) -> dict:
    data = path.read_bytes()
    outer = outer_model(data)
    if outer and (outer["class_id"] == 5 or (outer["class_id"] == 0 and outer.get("subtype") == 0)):
        name_end = data.find(b"\0", outer["string_offset"])
        srt_offset = name_end + 5 if name_end >= 0 else -1
        if srt_offset >= 0 and srt_offset + 36 <= len(data):
            decoded = _srt(
                struct.unpack_from(">9f", data, srt_offset),
                "outer_immediate_transform_payload",
                srt_offset,
            )
            if decoded:
                outer["local_srt"] = decoded
    elif outer and outer["class_id"] in {9, 10}:
        record, decoded = _decode_parametric_at(data, outer["string_offset"], outer["name"])
        if record is not None:
            outer["parametric_kind"] = record["kind"]
            outer["parametric_record_start"] = record["record_start"]
            outer["parametric_decoded_through"] = record.get("decoded_through_trims") or record.get("decoded_through")
            outer["trim_count"] = record.get("trim_count") or 0
            outer["reconstruction_ready"] = bool(record.get("reconstruction_ready"))
        if decoded:
            outer["local_srt"] = decoded
    if outer and outer.get("class_id") == 4:
        raw_records = []
        for match in NAME_RE.finditer(data):
            payload = match.end()
            if payload + 4 > len(data):
                continue
            class_id = int.from_bytes(data[payload : payload + 2], "big")
            zeros = zero_run_before(data, match.start())
            if class_id in KNOWN_CLASSES and zeros >= 20 and zeros % 2 == 0:
                raw_records.append({"offset": match.start(), "zero_run": zeros})
                break
        end = raw_records[0]["offset"] if raw_records else len(data)
        next_zero_run = raw_records[0]["zero_run"] if raw_records else 0
        decoded = _decode_mesh_srt_between(data, outer["offset"], end, next_zero_run)
        if decoded:
            outer["local_srt"] = decoded
    records = discover_records(data)
    baselines = infer_baselines(records)
    chosen = forced_baseline
    if chosen is None and baselines:
        chosen = baselines[0]["baseline_zero_run"]
    tree = []
    if outer and chosen is not None:
        tree = apply_tree(records, outer["name"], chosen)
    return {
        "schema": "bz2-binary-hrc-tree-probe-v3",
        "source": str(path),
        "outer_model": outer,
        "record_count": len(records),
        "baseline_candidates": baselines,
        "chosen_baseline": chosen,
        "tree": tree,
        "srt_summary": {
            "tree_nodes_with_srt": sum(1 for item in tree if item.get("local_srt")),
            "tree_parametric_nodes": sum(1 for item in tree if item.get("class_id") in {9, 10}),
            "tree_mesh_nodes": sum(1 for item in tree if item.get("class_id") == 4),
            "tree_mesh_nodes_with_srt": sum(1 for item in tree if item.get("class_id") == 4 and item.get("local_srt")),
            "tree_parametric_nodes_with_srt": sum(
                1 for item in tree if item.get("class_id") in {9, 10} and item.get("local_srt")
            ),
            "outer_has_srt": bool(outer and outer.get("local_srt")),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hrc", type=Path)
    parser.add_argument("--baseline", type=int)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    payload = probe(args.hrc, args.baseline)
    text = json.dumps(payload, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

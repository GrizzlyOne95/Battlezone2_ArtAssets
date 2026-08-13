#!/usr/bin/env python3
"""Probe nested model hierarchy and local transforms in binary Softimage HRC files.

The preorder hierarchy rule is regression-proven against the manually converted
soft-soldier fixture. Geometry-node transforms are now decoded for proven SI3D
NURBS curve/surface records as well as null/joint nodes.

Files whose hierarchy baseline is ambiguous retain every valid candidate in the
report. Parametric SRT is only promoted when the NURBS payload decodes structurally
and the complete nine-float transform block is finite and in-bounds.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import struct
import sys
from collections import Counter
from pathlib import Path

NAME_RE = re.compile(rb"\x00\x01([ -~]{1,80})\x00")
KNOWN_CLASSES = {0, 1, 2, 4, 5, 6, 9, 10}
PARAMETRIC_SRT_SKIP = {
    "nurbs_curve": 12,
    "nurbs_surface": 64,
}
SRT_FLOAT_COUNT = 9
SRT_SIZE = SRT_FLOAT_COUNT * 4
SRT_ABS_LIMIT = 1.0e12


def _load_sibling(name: str):
    path = Path(__file__).with_name(name)
    module_name = path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


nurbs_probe = _load_sibling("bz2_nurbs_probe.py")


def zero_run_before(data: bytes, offset: int) -> int:
    cursor = offset - 1
    while cursor >= 0 and data[cursor] == 0:
        cursor -= 1
    return offset - 1 - cursor


def _decode_srt_block(data: bytes, offset: int, source: str) -> tuple[dict | None, str | None]:
    if offset < 0 or offset + SRT_SIZE > len(data):
        return None, "srt_overrun"
    values = list(struct.unpack_from(">9f", data, offset))
    if not all(math.isfinite(value) for value in values):
        return None, "srt_non_finite"
    if any(abs(value) > SRT_ABS_LIMIT for value in values):
        return None, "srt_implausible_magnitude"
    return {
        "scale": values[0:3],
        "rotation_xyz": values[3:6],
        "translation_xyz": values[6:9],
        "offset": offset,
        "size": SRT_SIZE,
        "source": source,
    }, None


def _decode_parametric_srt(data: bytes, name_offset: int, name: str, class_id: int) -> tuple[dict | None, dict | None, str | None]:
    anchor = nurbs_probe.StringAnchor(offset=name_offset, value=name, parametric=True)
    record = nurbs_probe.decode_parametric_record(data, anchor)
    if record is None:
        return None, None, "parametric_decode_failed"

    expected_kind = "nurbs_curve" if class_id == 9 else "nurbs_surface"
    if record.get("kind") != expected_kind:
        return None, None, f"parametric_kind_mismatch:{record.get('kind')}"

    decoded_through = record.get("decoded_through_trims", record.get("decoded_through"))
    if not isinstance(decoded_through, int):
        return None, None, "parametric_missing_decoded_end"
    skip = PARAMETRIC_SRT_SKIP[expected_kind]
    srt_offset = decoded_through + skip
    srt, error = _decode_srt_block(data, srt_offset, "post_parametric_metadata")
    summary = {
        "kind": expected_kind,
        "record_start": record.get("record_start"),
        "decoded_through": decoded_through,
        "metadata_skip_to_srt": skip,
        "srt_offset": srt_offset,
        "trim_count": int(record.get("trim_count") or 0) if expected_kind == "nurbs_surface" else 0,
        "reconstruction_ready": bool(record.get("reconstruction_ready")),
    }
    if srt is not None:
        srt["parametric_kind"] = expected_kind
        srt["record_decoded_through"] = decoded_through
        srt["metadata_skip_to_srt"] = skip
    return summary, srt, error


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
    }


def discover_records(data: bytes) -> list[dict]:
    records = []
    for match in NAME_RE.finditer(data):
        payload = match.end()
        if payload + 4 > len(data):
            continue
        class_id = int.from_bytes(data[payload : payload + 2], "big")
        subtype = int.from_bytes(data[payload + 2 : payload + 4], "big")
        zeros = zero_run_before(data, match.start())
        # Internal primitive/cluster records can use the same name tag. The model
        # records proven against the soldier fixture have >=20 zero bytes before
        # the tag; shorter runs are retained only in diagnostics, not the tree.
        if class_id not in KNOWN_CLASSES or zeros < 20 or zeros % 2:
            continue
        name = match.group(1).decode("latin-1", errors="replace")
        item = {
            "name": name,
            "name_offset": match.start(1),
            "offset": match.start(),
            "payload_offset": payload + 4,
            "class_id": class_id,
            "subtype": subtype,
            "zero_run": zeros,
        }
        if class_id in {0, 5}:
            srt, error = _decode_srt_block(data, payload + 4, "immediate_transform_payload")
            if srt is not None:
                item["local_srt"] = srt
            elif error:
                item["srt_decode_error"] = error
        elif class_id in {9, 10}:
            parametric, srt, error = _decode_parametric_srt(data, match.start(1), name, class_id)
            if parametric is not None:
                item["parametric_record"] = parametric
            if srt is not None:
                item["local_srt"] = srt
            elif error:
                item["srt_decode_error"] = error
        records.append(item)
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
        # Preorder can descend only to an immediate child in one transition.
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
            candidates.append({
                "baseline_zero_run": baseline,
                "max_depth": max(depths, default=0),
                "final_depth": depths[-1] if depths else 0,
            })
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
    records = discover_records(data)
    baselines = infer_baselines(records)
    chosen = forced_baseline
    if chosen is None and baselines:
        # Keep the smallest valid baseline as the working hypothesis; all valid
        # candidates remain in the report so ambiguity is never hidden.
        chosen = baselines[0]["baseline_zero_run"]
    tree = []
    if outer and chosen is not None:
        tree = apply_tree(records, outer["name"], chosen)

    srt_sources = Counter(
        record["local_srt"]["source"]
        for record in records
        if record.get("local_srt")
    )
    parametric_records = [record for record in records if record.get("parametric_record")]
    parametric_srt = [record for record in parametric_records if record.get("local_srt")]
    parametric_errors = Counter(
        record.get("srt_decode_error", "missing")
        for record in parametric_records
        if not record.get("local_srt")
    )
    return {
        "schema": "bz2-binary-hrc-tree-probe-v2",
        "source": str(path),
        "outer_model": outer,
        "record_count": len(records),
        "local_srt_count": sum(srt_sources.values()),
        "local_srt_sources": dict(sorted(srt_sources.items())),
        "parametric_record_count": len(parametric_records),
        "parametric_srt_count": len(parametric_srt),
        "parametric_srt_errors": dict(sorted(parametric_errors.items())),
        "baseline_candidates": baselines,
        "chosen_baseline": chosen,
        "tree": tree,
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

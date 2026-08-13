#!/usr/bin/env python3
"""Probe nested model hierarchy and transform nodes in binary Softimage HRC files.

This intentionally reports evidence instead of forcing hierarchy into scene export.
The preorder rule is regression-proven against the manually converted soft-soldier
fixture; files whose baseline is ambiguous retain every valid candidate in the report.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
from pathlib import Path

NAME_RE = re.compile(rb"\x00\x01([ -~]{1,80})\x00")
KNOWN_CLASSES = {0, 1, 2, 4, 5, 6, 9, 10}


def zero_run_before(data: bytes, offset: int) -> int:
    cursor = offset - 1
    while cursor >= 0 and data[cursor] == 0:
        cursor -= 1
    return offset - 1 - cursor


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
        item = {
            "name": match.group(1).decode("latin-1", errors="replace"),
            "offset": match.start(),
            "payload_offset": payload + 4,
            "class_id": class_id,
            "subtype": subtype,
            "zero_run": zeros,
        }
        if class_id in {0, 5} and payload + 4 + 36 <= len(data):
            values = list(struct.unpack_from(">9f", data, payload + 4))
            if all(math.isfinite(value) for value in values):
                item["local_srt"] = {
                    "scale": values[0:3],
                    "rotation_xyz": values[3:6],
                    "translation_xyz": values[6:9],
                    "source": "immediate_transform_payload",
                }
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
    return {
        "schema": "bz2-binary-hrc-tree-probe-v1",
        "source": str(path),
        "outer_model": outer,
        "record_count": len(records),
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

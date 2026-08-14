#!/usr/bin/env python3
"""Validate transform-chain coverage for multi-record NURBS HRC files."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import bz2_hrc_tree_probe as hrc_tree

SUPPORTED_TRANSFORM_CLASSES = {0, 4, 5, 9, 10}
PARAMETRIC_CLASSES = {9, 10}


def validate_roots(roots: list[Path]) -> dict:
    summary: Counter[str] = Counter()
    blocked_reasons: Counter[str] = Counter()
    blocked_files: list[dict] = []

    for root in roots:
        for path in sorted(root.rglob("*.hrc")):
            probe = hrc_tree.probe(path)
            outer = probe.get("outer_model") or {}
            records = probe.get("tree", [])
            total = sum(1 for item in records if item.get("class_id") in PARAMETRIC_CLASSES)
            if outer.get("class_id") in PARAMETRIC_CLASSES:
                total += 1
            if total <= 1:
                continue

            summary["multi_record_hrc_count"] += 1
            summary["parametric_record_count"] += total
            file_failures: list[dict] = []

            if outer.get("class_id") in PARAMETRIC_CLASSES:
                if outer.get("local_srt"):
                    summary["placeable_parametric_count"] += 1
                else:
                    summary["blocked_parametric_count"] += 1
                    blocked_reasons["outer_parametric_missing_srt"] += 1
                    file_failures.append({"name": outer.get("name"), "reasons": ["outer_parametric_missing_srt"]})

            stack: list[dict] = [outer]
            for item in records:
                stack = stack[: item["depth"]]
                if item.get("class_id") in PARAMETRIC_CLASSES:
                    reasons: list[str] = []
                    if not item.get("local_srt"):
                        reasons.append("parametric_missing_srt")
                    for ancestor in stack:
                        if not ancestor:
                            continue
                        class_id = ancestor.get("class_id")
                        if class_id not in SUPPORTED_TRANSFORM_CLASSES:
                            reasons.append(f"unsupported_ancestor_class_{class_id}")
                        elif not ancestor.get("local_srt"):
                            reasons.append(f"ancestor_class_{class_id}_missing_srt")
                    reasons = sorted(set(reasons))
                    if reasons:
                        summary["blocked_parametric_count"] += 1
                        for reason in reasons:
                            blocked_reasons[reason] += 1
                        file_failures.append({"name": item.get("name"), "reasons": reasons})
                    else:
                        summary["placeable_parametric_count"] += 1
                stack.append(item)

            if file_failures:
                summary["blocked_multi_record_hrc_count"] += 1
                blocked_files.append({"path": str(path), "failures": file_failures})
            else:
                summary["fully_placeable_multi_record_hrc_count"] += 1

    return {
        "schema": "bz2-multirecord-nurbs-placement-validation-v1",
        "roots": [str(root) for root in roots],
        "summary": dict(summary),
        "blocked_reasons": dict(sorted(blocked_reasons.items())),
        "blocked_files": blocked_files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    payload = validate_roots(args.roots)
    text = json.dumps(payload, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 1 if payload["blocked_files"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

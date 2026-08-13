#!/usr/bin/env python3
"""Corpus-wide exporter for decoded Battlezone 2 Softimage|3D NURBS HRC records.

This is intentionally separate from ``bz2_extract.py`` while the parametric decoder
is being validated. It scans HRC files, decodes proven SI3D curve/surface records
using ``bz2_nurbs_probe.py``, and emits open OBJ previews plus a machine-readable
index suitable for later scene-export integration.

Surface trimming currently uses the validation tessellator from
``bz2_nurbs_preview.py``. Its UV centroid clipping is conservative/approximate at
trim boundaries; the original rational NURBS and trim control data remain in the
probe reports and should be treated as the preservation source of truth.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path


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


probe = _load_sibling("bz2_nurbs_probe.py")
preview = _load_sibling("bz2_nurbs_preview.py")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_component(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return value or "unnamed"


def unique_output_name(name: str, offset: int, used: set[str]) -> str:
    base = sanitize_component(name)
    candidate = f"{base}.obj"
    if candidate.lower() not in used:
        used.add(candidate.lower())
        return candidate
    candidate = f"{base}__0x{offset:X}.obj"
    suffix = 1
    while candidate.lower() in used:
        candidate = f"{base}__0x{offset:X}_{suffix}.obj"
        suffix += 1
    used.add(candidate.lower())
    return candidate


def relative_output_dir(source_root: Path, source_path: Path, output_root: Path) -> Path:
    rel = source_path.relative_to(source_root)
    return output_root / rel.parent / sanitize_component(rel.name)


def decode_hrc(path: Path) -> tuple[list[tuple[object, dict]], list[dict]]:
    data = path.read_bytes()
    anchors, rejected = probe.discover_parametric_anchors(data)
    decoded: list[tuple[object, dict]] = []
    failures: list[dict] = []
    for anchor in anchors:
        record = probe.decode_parametric_record(data, anchor)
        if record is None:
            failures.append({"name": anchor.value, "offset": anchor.offset, "reason": "decode_failed"})
            continue
        decoded.append((anchor, record))
    for anchor in rejected:
        failures.append({"name": anchor.value, "offset": anchor.offset, "reason": "candidate_rejected"})
    return decoded, failures


def export_hrc(
    source_root: Path,
    source_path: Path,
    output_root: Path,
    curve_steps: int,
    surface_steps_u: int,
    surface_steps_v: int,
) -> dict:
    decoded, failures = decode_hrc(source_path)
    out_dir = relative_output_dir(source_root, source_path, output_root)
    used: set[str] = set()
    records: list[dict] = []

    for anchor, record in decoded:
        item = {
            "name": anchor.value,
            "anchor_offset": anchor.offset,
            "kind": record["kind"],
            "record_start": record.get("record_start"),
            "decoded_through": record.get("decoded_through_trims", record.get("decoded_through")),
            "reconstruction_ready": bool(record.get("reconstruction_ready")),
            "closed": bool(record.get("closed")) if record["kind"] == "nurbs_curve" else None,
            "closed_u": bool(record.get("closed_u")) if record["kind"] == "nurbs_surface" else None,
            "closed_v": bool(record.get("closed_v")) if record["kind"] == "nurbs_surface" else None,
            "trim_count": int(record.get("trim_count") or 0) if record["kind"] == "nurbs_surface" else 0,
        }
        if not record.get("reconstruction_ready"):
            item["status"] = "unsupported"
            if record["kind"] == "nurbs_curve":
                item["knot_strategy"] = (record.get("knot_conversion") or {}).get("strategy")
            else:
                item["knot_strategy_u"] = (record.get("knot_conversion_u") or {}).get("strategy")
                item["knot_strategy_v"] = (record.get("knot_conversion_v") or {}).get("strategy")
            records.append(item)
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        filename = unique_output_name(anchor.value, anchor.offset, used)
        output_path = out_dir / filename
        try:
            if record["kind"] == "nurbs_curve":
                stats = preview.write_curve_obj(output_path, record, curve_steps)
            else:
                stats = preview.write_surface_obj(output_path, record, surface_steps_u, surface_steps_v)
        except Exception as exc:
            item["status"] = "export_failed"
            item["error"] = f"{type(exc).__name__}: {exc}"
            records.append(item)
            continue

        item["status"] = "exported"
        item["obj"] = output_path.relative_to(output_root.parent).as_posix()
        item["stats"] = stats
        records.append(item)

    rel = source_path.relative_to(source_root).as_posix()
    return {
        "source": rel,
        "source_size": source_path.stat().st_size,
        "source_sha256": sha256(source_path),
        "record_count": len(decoded),
        "failure_count": len(failures),
        "records": records,
        "candidate_failures": failures,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path, help="Root directory containing HRC files")
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/extracts/hrc_nurbs"))
    parser.add_argument("--report", type=Path, default=Path("artifacts/reports/hrc_nurbs.json"))
    parser.add_argument("--curve-steps", type=int, default=64)
    parser.add_argument("--surface-steps-u", type=int, default=32)
    parser.add_argument("--surface-steps-v", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", help="Optional case-insensitive regex applied to relative HRC paths")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    report_path = args.report.resolve()
    wanted = re.compile(args.only, re.IGNORECASE) if args.only else None

    files = sorted(source_root.rglob("*.hrc")) + sorted(source_root.rglob("*.HRC"))
    if wanted:
        files = [path for path in files if wanted.search(path.relative_to(source_root).as_posix())]
    if args.limit:
        files = files[: args.limit]

    counters = Counter()
    entries: list[dict] = []
    for index, path in enumerate(files, 1):
        entry = export_hrc(source_root, path, output_root, args.curve_steps, args.surface_steps_u, args.surface_steps_v)
        if not entry["record_count"] and not entry["failure_count"]:
            continue
        entries.append(entry)
        counters["hrc_files_with_parametric_data"] += 1
        counters["records"] += entry["record_count"]
        counters["candidate_failures"] += entry["failure_count"]
        for record in entry["records"]:
            counters[record["kind"]] += 1
            counters[f"status:{record['status']}"] += 1
            if record.get("trim_count"):
                counters["trimmed_surfaces"] += 1
                counters["trim_loops"] += record["trim_count"]
        if index % 250 == 0:
            print(f"scanned {index}/{len(files)} HRC files", file=sys.stderr)

    payload = {
        "schema": "bz2-hrc-nurbs-export-v1",
        "source_root": str(source_root),
        "output_root": str(output_root),
        "settings": {
            "curve_steps": args.curve_steps,
            "surface_steps_u": args.surface_steps_u,
            "surface_steps_v": args.surface_steps_v,
            "trim_tessellation": "uv_face_centroid_clip_validation_quality",
        },
        "summary": dict(sorted(counters.items())),
        "files": entries,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

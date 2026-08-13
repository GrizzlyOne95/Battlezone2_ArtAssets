#!/usr/bin/env python3
"""Complete BZ2 NURBS exporter: direct HRCs plus HRCs inside embedded ZIP archives."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath


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
evaluator = _load_sibling("bz2_nurbs_eval.py")


def sanitize_component(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return value or "unnamed"


def logical_output_dir(logical_source: str, output_root: Path) -> Path:
    rel = PurePosixPath(logical_source)
    return output_root.joinpath(*rel.parent.parts) / sanitize_component(rel.name)


def decode_hrc(data: bytes):
    anchors, rejected = probe.discover_parametric_anchors(data)
    decoded, failures = [], []
    for anchor in anchors:
        record = probe.decode_parametric_record(data, anchor)
        if record is None:
            failures.append({"name": anchor.value, "offset": anchor.offset, "reason": "decode_failed"})
        else:
            decoded.append((anchor, record))
    failures.extend({"name": anchor.value, "offset": anchor.offset, "reason": "candidate_rejected"} for anchor in rejected)
    return decoded, failures


def export_hrc(logical_source: str, data: bytes, output_root: Path, curve_steps: int, steps_u: int, steps_v: int, container: str | None):
    decoded, failures = decode_hrc(data)
    out_dir = logical_output_dir(logical_source, output_root)
    used: set[str] = set()
    records = []
    for anchor, record in decoded:
        item = {
            "name": anchor.value,
            "anchor_offset": anchor.offset,
            "kind": record["kind"],
            "reconstruction_ready": bool(record.get("reconstruction_ready")),
            "closed": bool(record.get("closed")) if record["kind"] == "nurbs_curve" else None,
            "closed_u": bool(record.get("closed_u")) if record["kind"] == "nurbs_surface" else None,
            "closed_v": bool(record.get("closed_v")) if record["kind"] == "nurbs_surface" else None,
            "trim_count": int(record.get("trim_count") or 0) if record["kind"] == "nurbs_surface" else 0,
        }
        if not record.get("reconstruction_ready"):
            item["status"] = "unsupported"
            item["knot_strategy_u"] = (record.get("knot_conversion_u") or {}).get("strategy")
            item["knot_strategy_v"] = (record.get("knot_conversion_v") or {}).get("strategy")
            records.append(item)
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        base = sanitize_component(anchor.value)
        filename = f"{base}.obj"
        if filename.lower() in used:
            filename = f"{base}__0x{anchor.offset:X}.obj"
        used.add(filename.lower())
        path = out_dir / filename
        try:
            stats = evaluator.write_curve_obj(path, record, curve_steps) if record["kind"] == "nurbs_curve" else evaluator.write_surface_obj(path, record, steps_u, steps_v)
        except Exception as exc:
            item["status"] = "export_failed"
            item["error"] = f"{type(exc).__name__}: {exc}"
            records.append(item)
            continue
        item["status"] = "exported"
        item["obj"] = path.relative_to(output_root.parent).as_posix()
        item["stats"] = stats
        records.append(item)
    result = {
        "source": logical_source,
        "source_size": len(data),
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "record_count": len(decoded),
        "failure_count": len(failures),
        "records": records,
        "candidate_failures": failures,
    }
    if container:
        result["container_source"] = container
    return result


def iter_sources(root: Path):
    direct = [path for path in sorted(root.rglob("*.hrc")) if path.is_file()]
    names = {path.relative_to(root).as_posix().lower() for path in direct}
    for path in direct:
        yield path.relative_to(root).as_posix(), path.read_bytes(), None
    for archive in [path for path in sorted(root.rglob("*.zip")) if path.is_file()]:
        archive_rel = PurePosixPath(archive.relative_to(root).as_posix())
        expanded = archive_rel.with_suffix("")
        try:
            zf = zipfile.ZipFile(archive, "r")
        except zipfile.BadZipFile:
            continue
        with zf:
            for info in sorted(zf.infolist(), key=lambda item: item.filename.lower()):
                if info.is_dir() or not info.filename.lower().endswith(".hrc"):
                    continue
                logical = (expanded / PurePosixPath(info.filename.replace("\\", "/"))).as_posix()
                if logical.lower() in names:
                    continue
                yield logical, zf.read(info), archive_rel.as_posix()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source_root", type=Path)
    p.add_argument("--output-root", type=Path, default=Path("artifacts/extracts/hrc_nurbs"))
    p.add_argument("--report", type=Path, default=Path("artifacts/reports/hrc_nurbs.json"))
    p.add_argument("--curve-steps", type=int, default=64)
    p.add_argument("--surface-steps-u", type=int, default=32)
    p.add_argument("--surface-steps-v", type=int, default=32)
    p.add_argument("--only", help="Optional regex over logical HRC paths")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root, output = args.source_root.resolve(), args.output_root.resolve()
    wanted = re.compile(args.only, re.IGNORECASE) if args.only else None
    counters = Counter()
    entries = []
    for logical, data, container in iter_sources(root):
        if wanted and not wanted.search(logical):
            continue
        counters["archive_hrc_members_scanned" if container else "direct_hrc_files_scanned"] += 1
        entry = export_hrc(logical, data, output, args.curve_steps, args.surface_steps_u, args.surface_steps_v, container)
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
    counters["logical_hrc_sources_scanned"] = counters["direct_hrc_files_scanned"] + counters["archive_hrc_members_scanned"]
    payload = {
        "schema": "bz2-hrc-nurbs-export-v2",
        "source_root": str(root),
        "output_root": str(output),
        "settings": {
            "curve_steps": args.curve_steps,
            "surface_steps_u": args.surface_steps_u,
            "surface_steps_v": args.surface_steps_v,
            "include_zip_archives": true,
            "trim_tessellation": "uv_face_centroid_clip_validation_quality"
        },
        "summary": dict(sorted(counters.items())),
        "files": entries,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

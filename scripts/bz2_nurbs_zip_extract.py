#!/usr/bin/env python3
"""Export BZ2 SI3D NURBS HRC members stored inside embedded ZIP archives.

Companion to bz2_nurbs_extract.py. This keeps the proven direct-HRC exporter small
while making archives such as modelsdirectory/Archival.zip part of a reproducible
full-dump extraction. ZIP members are materialized only in a temporary directory;
OBJ derivatives are written to the normal hrc_nurbs output tree.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath


def _load_base():
    path = Path(__file__).with_name("bz2_nurbs_extract.py")
    spec = importlib.util.spec_from_file_location("bz2_nurbs_extract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = _load_base()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source_root", type=Path, help="Root containing embedded ZIP archives, normally modelsdirectory")
    p.add_argument("--output-root", type=Path, default=Path("artifacts/extracts/hrc_nurbs"))
    p.add_argument("--report", type=Path, default=Path("artifacts/reports/hrc_nurbs_archives.json"))
    p.add_argument("--curve-steps", type=int, default=64)
    p.add_argument("--surface-steps-u", type=int, default=32)
    p.add_argument("--surface-steps-v", type=int, default=32)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root = args.source_root.resolve()
    out = args.output_root.resolve()
    direct = {p.relative_to(root).as_posix().lower() for p in root.rglob("*.hrc") if p.is_file()}
    counters = Counter()
    entries = []

    with tempfile.TemporaryDirectory(prefix="bz2_nurbs_zip_") as temp_name:
        temp_root = Path(temp_name)
        for archive in sorted(p for p in root.rglob("*.zip") if p.is_file()):
            archive_rel = PurePosixPath(archive.relative_to(root).as_posix())
            expanded = archive_rel.with_suffix("")
            try:
                zf = zipfile.ZipFile(archive, "r")
            except zipfile.BadZipFile:
                counters["bad_zip_archives"] += 1
                continue
            with zf:
                for info in sorted(zf.infolist(), key=lambda item: item.filename.lower()):
                    if info.is_dir() or not info.filename.lower().endswith(".hrc"):
                        continue
                    logical = (expanded / PurePosixPath(info.filename.replace("\\", "/"))).as_posix()
                    if logical.lower() in direct:
                        counters["duplicate_members_skipped"] += 1
                        continue
                    temp_path = temp_root.joinpath(*PurePosixPath(logical).parts)
                    temp_path.parent.mkdir(parents=True, exist_ok=True)
                    temp_path.write_bytes(zf.read(info))
                    counters["archive_hrc_members_scanned"] += 1
                    entry = base.export_hrc(
                        temp_root, temp_path, out,
                        args.curve_steps, args.surface_steps_u, args.surface_steps_v,
                    )
                    if not entry["record_count"] and not entry["failure_count"]:
                        continue
                    entry["container_source"] = archive_rel.as_posix()
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

    payload = {
        "schema": "bz2-hrc-nurbs-archive-export-v1",
        "source_root": str(root),
        "output_root": str(out),
        "summary": dict(sorted(counters.items())),
        "files": entries,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

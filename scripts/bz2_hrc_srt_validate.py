#!/usr/bin/env python3
"""Validate post-parametric local SRT placement across the complete BZ2 HRC corpus.

This scans direct HRC files plus HRC members stored in embedded ZIP archives and
checks the class-9/class-10 geometry-node transform rule without exporting meshes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
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


nurbs_probe = _load_sibling("bz2_nurbs_probe.py")
tree_probe = _load_sibling("bz2_hrc_tree_probe.py")


def validate_bytes(data: bytes, logical_source: str) -> dict:
    anchors, rejected = nurbs_probe.discover_parametric_anchors(data)
    counters = Counter()
    errors: list[dict] = []

    for anchor in anchors:
        record = nurbs_probe.decode_parametric_record(data, anchor)
        if record is None:
            counters["decode_failed"] += 1
            errors.append({
                "source": logical_source,
                "name": anchor.value,
                "offset": anchor.offset,
                "error": "parametric_decode_failed",
            })
            continue

        kind = record.get("kind")
        class_id = 9 if kind == "nurbs_curve" else 10 if kind == "nurbs_surface" else -1
        counters["records"] += 1
        counters[kind or "unknown_kind"] += 1
        summary, srt, error = tree_probe._decode_parametric_srt(
            data, anchor.offset, anchor.value, class_id
        )
        if srt is not None:
            counters["srt_valid"] += 1
            counters[f"srt_valid:{kind}"] += 1
            if summary and summary.get("trim_count"):
                counters["trimmed_surface_records"] += 1
        else:
            counters["srt_invalid"] += 1
            counters[f"srt_error:{error or 'unknown'}"] += 1
            errors.append({
                "source": logical_source,
                "name": anchor.value,
                "offset": anchor.offset,
                "kind": kind,
                "decoded": summary,
                "error": error or "unknown",
            })

    counters["rejected_tag_candidates"] += len(rejected)
    return {
        "summary": counters,
        "errors": errors,
    }


def merge_result(total: Counter, errors: list[dict], result: dict) -> None:
    total.update(result["summary"])
    errors.extend(result["errors"])


def validate_corpus(root: Path) -> dict:
    root = root.resolve()
    total = Counter()
    errors: list[dict] = []
    files_with_records = 0

    direct_paths = sorted(
        {path for pattern in ("*.hrc", "*.HRC") for path in root.rglob(pattern) if path.is_file()}
    )
    direct_logical = {path.relative_to(root).as_posix().lower() for path in direct_paths}

    for path in direct_paths:
        logical = path.relative_to(root).as_posix()
        result = validate_bytes(path.read_bytes(), logical)
        total["direct_hrc_files_scanned"] += 1
        if result["summary"].get("records"):
            files_with_records += 1
            total["direct_hrc_files_with_parametric_records"] += 1
        before = total["records"]
        merge_result(total, errors, result)
        total["direct_parametric_records"] += total["records"] - before

    for archive in sorted(path for path in root.rglob("*.zip") if path.is_file()):
        archive_rel = PurePosixPath(archive.relative_to(root).as_posix())
        expanded = archive_rel.with_suffix("")
        try:
            zf = zipfile.ZipFile(archive, "r")
        except zipfile.BadZipFile:
            total["bad_zip_archives"] += 1
            continue

        with zf:
            for info in sorted(zf.infolist(), key=lambda item: item.filename.lower()):
                if info.is_dir() or not info.filename.lower().endswith(".hrc"):
                    continue
                logical = (expanded / PurePosixPath(info.filename.replace("\\", "/"))).as_posix()
                if logical.lower() in direct_logical:
                    total["archive_duplicate_hrc_skipped"] += 1
                    continue

                total["archive_hrc_members_scanned"] += 1
                result = validate_bytes(zf.read(info), logical)
                if result["summary"].get("records"):
                    files_with_records += 1
                    total["archive_hrc_members_with_parametric_records"] += 1
                before = total["records"]
                merge_result(total, errors, result)
                total["archive_parametric_records"] += total["records"] - before

    total["logical_sources_with_parametric_records"] = files_with_records
    return {
        "schema": "bz2-hrc-geometry-srt-validation-v1",
        "source_root": str(root),
        "summary": dict(sorted(total.items())),
        "errors": errors,
        "notes": [
            "Curve SRT begins 12 bytes after the structurally decoded parametric payload.",
            "Surface SRT begins 64 bytes after decoded_through_trims when trims are present, otherwise decoded_through.",
            "The SRT block is nine big-endian float32 values: scale XYZ, rotation XYZ, translation XYZ.",
            "Embedded ZIP HRC members that duplicate a direct logical source path are skipped.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("artifacts/reports/hrc_geometry_srt_validation.json"),
    )
    args = parser.parse_args()

    payload = validate_corpus(args.source_root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    if payload["errors"]:
        print(f"SRT validation failures: {len(payload['errors'])}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

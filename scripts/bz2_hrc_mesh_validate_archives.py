#!/usr/bin/env python3
"""Extend class-4 HRC geometry validation to embedded ZIP archives.

This wrapper reuses the structurally proven decoder in
``bz2_hrc_mesh_validate_v2.py`` and treats direct HRC files plus HRC members
inside ZIP archives as one logical preservation corpus. Archive members whose
expanded logical path duplicates a direct source are skipped.
"""
from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

import bz2_hrc_mesh_validate_v2 as base


def accumulate(data: bytes, counters: Counter[str], failures: Counter[str], origin: str) -> None:
    records = base.structural_records(data)
    candidates: list[tuple[int, int, bool]] = []

    outer = base.outer_class4(data)
    if outer is not None:
        payload_offset, _ = outer
        end = records[0][2] if records else len(data)
        candidates.append((payload_offset, end, True))

    for index, (class_id, payload_offset, _offset, _zero_run) in enumerate(records):
        if class_id != 4:
            continue
        if index + 1 < len(records):
            next_record = records[index + 1]
            end = next_record[2] - next_record[3]
        else:
            end = len(data)
        candidates.append((payload_offset, end, False))

    counters["hrc_files"] += 1
    counters[f"{origin}_hrc_files"] += 1
    if candidates:
        counters["hrc_files_with_class4"] += 1
        counters[f"{origin}_hrc_files_with_class4"] += 1

    for payload_offset, end, is_outer in candidates:
        counters["class4_records"] += 1
        counters[f"{origin}_class4_records"] += 1
        counters["outer_records" if is_outer else "nested_records"] += 1

        decoded, error = base.decode_class4(data, payload_offset, end)
        if decoded is None:
            failures[error or "unknown"] += 1
            continue

        counters["decoded_records"] += 1
        counters["decoded_outer" if is_outer else "decoded_nested"] += 1
        counters["vertices"] += decoded["vertex_count"]
        counters["polygons"] += decoded["polygon_count"]
        counters["simple_fan_triangles"] += decoded["simple_fan_triangles"]
        counters["nan_normal_corners"] += decoded["nan_normal_corners"]
        counters["contour_separators"] += decoded["contour_separators"]
        counters["multi_contour_polygons"] += decoded["multi_contour_polygons"]
        counters["max_polygon_corners"] = max(
            counters["max_polygon_corners"], decoded["max_polygon_corners"]
        )
        counters["max_contours_per_polygon"] = max(
            counters["max_contours_per_polygon"], decoded["max_contours_per_polygon"]
        )
        if decoded["transform_only"]:
            counters["transform_only_records"] += 1
        if decoded["max_polygon_corners"] > 32:
            counters["records_with_polygon_gt32"] += 1
        if decoded["contour_separators"]:
            counters["records_with_contour_separators"] += 1


def validate(root: Path) -> dict:
    root = root.resolve()
    counters: Counter[str] = Counter()
    failures: Counter[str] = Counter()

    direct_paths = sorted(
        {path for pattern in ("*.hrc", "*.HRC") for path in root.rglob(pattern) if path.is_file()}
    )
    direct_logical = {path.relative_to(root).as_posix().lower() for path in direct_paths}
    for path in direct_paths:
        accumulate(path.read_bytes(), counters, failures, "direct")

    archives = sorted(
        {path for pattern in ("*.zip", "*.ZIP") for path in root.rglob(pattern) if path.is_file()}
    )
    counters["zip_archives_scanned"] = len(archives)
    for archive in archives:
        archive_rel = PurePosixPath(archive.relative_to(root).as_posix())
        expanded = archive_rel.with_suffix("")
        try:
            zf = zipfile.ZipFile(archive, "r")
        except zipfile.BadZipFile:
            failures["bad_zip_archive"] += 1
            continue
        with zf:
            for info in sorted(zf.infolist(), key=lambda item: item.filename.lower()):
                if info.is_dir() or not info.filename.lower().endswith(".hrc"):
                    continue
                logical = (expanded / PurePosixPath(info.filename.replace("\\", "/"))).as_posix()
                if logical.lower() in direct_logical:
                    counters["archive_duplicate_hrc_skipped"] += 1
                    continue
                accumulate(zf.read(info), counters, failures, "archive")

    return {
        "schema": "bz2-class4-geometry-validation-v3",
        "source_root": str(root),
        "summary": dict(counters),
        "failures": dict(failures),
        "notes": [
            "direct and embedded ZIP HRCs are both first-class preservation sources",
            "simple_fan_triangles excludes multi-contour polygons pending hole-aware tessellation",
            "0xFFFFFFFF is an in-polygon contour separator",
            "all-NaN normal triplets are valid missing-normal sentinels",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = validate(args.source_root)
    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if not payload["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

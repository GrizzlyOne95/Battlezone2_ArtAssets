#!/usr/bin/env python3
"""Census TXMP projection/placement state across an extracted BZ2 modelsdirectory.

This utility exists because the smaller nested high-resolution archive is useful
for focused reversal, but it is not representative of every historical asset in
``bz2_art.7z``.  Given an extracted ``modelsdirectory`` tree, this script scans
all TEXTURES2D TXMP records and relation-aware DSC code-400/code-401 edges and
emits a derived JSON census suitable for regression checking.

Example:

    python scripts/bz2_txmp_corpus_census.py \
        /path/to/extracted/modelsdirectory \
        out/txmp_full_archive_census.json

The source assets are never copied into the output.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import bz2_dsc_material_gltf as dscmat
import bz2_projection_uv as projection_uv
import bz2_texture_layers_gltf as texture_layers


def _counter(counter: collections.Counter) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in sorted(counter.items(), key=lambda item: str(item[0]))
    }


def _pair_counter(counter: collections.Counter) -> dict[str, int]:
    return {
        ",".join(str(value) for value in key): int(count)
        for key, count in sorted(counter.items(), key=lambda item: tuple(str(v) for v in item[0]))
    }


def _picture_stem(record: dict) -> str:
    raw = str(record.get("raw_source_path") or "").replace("\\", "/")
    return Path(raw).stem.lower()


def _find_case_insensitive(directory: Path, filename: str) -> Path | None:
    candidate = directory / filename
    if candidate.is_file():
        return candidate
    lower = filename.lower()
    if not directory.is_dir():
        return None
    return next(
        (item for item in directory.iterdir() if item.is_file() and item.name.lower() == lower),
        None,
    )


def census(modelsdirectory: Path) -> dict:
    modelsdirectory = modelsdirectory.resolve()
    if not modelsdirectory.is_dir():
        raise FileNotFoundError(modelsdirectory)

    txmp_paths = sorted(modelsdirectory.glob("**/TEXTURES2D/*.txt"))
    scene_paths = sorted(modelsdirectory.glob("**/SCENES/*.dsc"))

    parsed_by_path: dict[Path, dict] = {}
    basename_index: dict[str, list[Path]] = collections.defaultdict(list)
    raw_code_counts: collections.Counter = collections.Counter()
    raw_repeat_counts: collections.Counter = collections.Counter()
    raw_plus0_counts: collections.Counter = collections.Counter()
    raw_plus78_counts: collections.Counter = collections.Counter()
    raw_plus80_counts: collections.Counter = collections.Counter()
    decode_failures = []

    for path in txmp_paths:
        basename_index[path.name.lower()].append(path)
        try:
            record = texture_layers.parse_txmp(path.read_bytes())
        except Exception as exc:
            decode_failures.append(
                {
                    "path": path.relative_to(modelsdirectory).as_posix(),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        parsed_by_path[path] = record
        raw_code_counts[int(record.get("projection_or_mapping_code_candidate") or 0)] += 1
        repeat = record.get("si_texture2d_repeat_uv") or [0, 0]
        raw_repeat_counts[(int(repeat[0]), int(repeat[1]))] += 1
        raw_plus0_counts[int(record.get("field_u16_be_0") or 0)] += 1
        raw_plus78_counts[int(record.get("field_u16_be_78") or 0)] += 1
        raw_plus80_counts[int(record.get("field_u16_be_80") or 0)] += 1

    relation = {
        400: {
            "edge_count": 0,
            "unresolved_edge_count": 0,
            "code": collections.Counter(),
            "repeat": collections.Counter(),
            "matrix": collections.Counter(),
            "matrix_by_code": collections.defaultdict(collections.Counter),
            "picture_by_code": collections.defaultdict(collections.Counter),
            "aux_76_78_80": collections.Counter(),
        },
        401: {
            "edge_count": 0,
            "unresolved_edge_count": 0,
            "code": collections.Counter(),
            "repeat": collections.Counter(),
            "matrix": collections.Counter(),
            "matrix_by_code": collections.defaultdict(collections.Counter),
            "picture_by_code": collections.defaultdict(collections.Counter),
            "aux_76_78_80": collections.Counter(),
        },
    }

    def resolve_texture(scene_path: Path, texture_name: str) -> Path | None:
        local = _find_case_insensitive(
            scene_path.parent.parent / "TEXTURES2D",
            texture_name + ".txt",
        )
        if local:
            return local
        candidates = basename_index.get((texture_name + ".txt").lower(), [])
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            # Ambiguous global basenames are resolved by nearest path-prefix
            # overlap, but only after the authoritative scene-local lookup.
            prefix_parts = {
                part.lower() for part in scene_path.parent.parent.relative_to(modelsdirectory).parts
            }
            ranked = sorted(
                candidates,
                key=lambda path: sum(
                    part.lower() in prefix_parts
                    for part in path.relative_to(modelsdirectory).parts
                ),
                reverse=True,
            )
            return ranked[0]
        return None

    dsc_failures = []
    for scene_path in scene_paths:
        try:
            chapters, relations = dscmat.parse_dsc(scene_path)
        except Exception as exc:
            dsc_failures.append(
                {
                    "path": scene_path.relative_to(modelsdirectory).as_posix(),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        textures = chapters.get("TEXTURES2D", [])
        for edge in relations:
            code = int(edge.get("relation_code") or 0)
            if code not in relation:
                continue
            if edge.get("target_chapter") != "TEXTURES2D":
                continue
            target_index = int(edge.get("target_index") or 0)
            if not 0 <= target_index < len(textures):
                relation[code]["unresolved_edge_count"] += 1
                continue
            texture_name = textures[target_index]
            path = resolve_texture(scene_path, texture_name)
            record = parsed_by_path.get(path) if path else None
            if record is None:
                relation[code]["unresolved_edge_count"] += 1
                continue

            bucket = relation[code]
            bucket["edge_count"] += 1
            projection_code = int(record.get("projection_or_mapping_code_candidate") or 0)
            bucket["code"][projection_code] += 1
            repeat = record.get("si_texture2d_repeat_uv") or [0, 0]
            bucket["repeat"][(int(repeat[0]), int(repeat[1]))] += 1
            identity = projection_uv.matrix_srt_is_identity(record)
            bucket["matrix"]["identity" if identity else "nonidentity"] += 1
            bucket["matrix_by_code"][projection_code][
                "identity" if identity else "nonidentity"
            ] += 1
            bucket["picture_by_code"][projection_code][_picture_stem(record)] += 1
            bucket["aux_76_78_80"][
                (
                    projection_code,
                    int(record.get("field_u16_be_76") or 0),
                    int(record.get("field_u16_be_78") or 0),
                    int(record.get("field_u16_be_80") or 0),
                )
            ] += 1

    def relation_payload(code: int) -> dict:
        source = relation[code]
        return {
            "edge_count": int(source["edge_count"]),
            "unresolved_edge_count": int(source["unresolved_edge_count"]),
            "projection_code_counts": _counter(source["code"]),
            "repeat_pair_counts": _pair_counter(source["repeat"]),
            "matrix_identity_counts": _counter(source["matrix"]),
            "matrix_by_projection_code": {
                str(key): _counter(value)
                for key, value in sorted(source["matrix_by_code"].items())
            },
            "top_picture_families_by_projection_code": {
                str(key): [
                    {"picture": picture, "edge_count": int(count)}
                    for picture, count in value.most_common(12)
                ]
                for key, value in sorted(source["picture_by_code"].items())
            },
            "plus24_plus76_plus78_plus80_counts": {
                ",".join(str(value) for value in key): int(count)
                for key, count in sorted(source["aux_76_78_80"].items())
            },
        }

    return {
        "schema": "bz2-txmp-corpus-census-v1",
        "source_root": str(modelsdirectory),
        "source_policy": "Derived statistics only; no source asset bytes are emitted.",
        "txmp_record_count": len(parsed_by_path),
        "txmp_file_count": len(txmp_paths),
        "txmp_decode_failure_count": len(decode_failures),
        "txmp_decode_failures": decode_failures,
        "dsc_scene_count": len(scene_paths),
        "dsc_parse_failure_count": len(dsc_failures),
        "dsc_parse_failures": dsc_failures,
        "raw_txmp": {
            "projection_code_counts": _counter(raw_code_counts),
            "repeat_pair_counts": _pair_counter(raw_repeat_counts),
            "field_u16_be_0_counts": _counter(raw_plus0_counts),
            "field_u16_be_78_counts": _counter(raw_plus78_counts),
            "field_u16_be_80_counts": _counter(raw_plus80_counts),
        },
        "relation_code_400": relation_payload(400),
        "relation_code_401": relation_payload(401),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modelsdirectory", type=Path)
    parser.add_argument("output_json", type=Path)
    args = parser.parse_args()
    payload = census(args.modelsdirectory)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "txmp_record_count": payload["txmp_record_count"],
                "dsc_scene_count": payload["dsc_scene_count"],
                "code400_edge_count": payload["relation_code_400"]["edge_count"],
                "code401_edge_count": payload["relation_code_401"]["edge_count"],
                "output": str(args.output_json),
            },
            indent=2,
        )
    )
    return 1 if payload["txmp_decode_failure_count"] or payload["dsc_parse_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Probe Softimage TXMP tails for a nine-float projection transform.

The model-local projection exporter deliberately preserves ``txmp_tail_hex`` in
``scene.model_textures.json``.  This tool turns that preserved byte evidence into
a repeatable corpus probe instead of requiring one-off hex inspection.

Current reversal evidence says one TXMP block is laid out as nine big-endian
floats in field order::

    rotation X, Y, Z,
    scale X, Y, Z,
    translation X, Y, Z

The exact byte offset is intentionally *not* hard-coded here until it is
correlated across enough readable SI_Texture2D/TXMP records.  Without
``--offset``, the tool ranks every possible nine-float window.  With a selected
``--offset``, it emits the decoded fields and an R -> S -> T transform matrix for
each projection record.

This script never writes UV0 and never mutates the source sidecar.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
from collections import Counter
from pathlib import Path
from typing import Iterable

FLOAT_COUNT = 9
FLOAT_BYTES = FLOAT_COUNT * 4
DEFAULT_TAIL_BYTES = 167


def _iter_projection_records(document: dict, source: Path) -> Iterable[dict]:
    """Yield code-400 projection records with useful model/texture context."""
    for model in document.get("models", []):
        for projection in model.get("local_texture_projections", []):
            tail_hex = projection.get("txmp_tail_hex")
            if not tail_hex:
                continue
            try:
                tail = bytes.fromhex(tail_hex)
            except ValueError:
                continue
            yield {
                "source": str(source),
                "model_index": model.get("model_index"),
                "model_name": model.get("model_name"),
                "gltf_node_index": model.get("gltf_node_index"),
                "texture_index": projection.get("texture_index"),
                "texture_object": projection.get("texture_object"),
                "raw_source_path": projection.get("raw_source_path"),
                "mapping_code_candidate": projection.get(
                    "projection_or_mapping_code_candidate"
                ),
                "texture_2d_transform_candidate": projection.get(
                    "texture_2d_transform_candidate"
                ),
                "tail": tail,
            }


def _endian_prefix(name: str) -> str:
    return ">" if name == "big" else "<"


def decode_rst(tail: bytes, offset: int, endian: str = "big") -> dict:
    """Decode nine floats in observed RXYZ/SXYZ/TXYZ field order."""
    if offset < 0 or offset + FLOAT_BYTES > len(tail):
        raise ValueError(
            f"offset {offset} cannot decode {FLOAT_BYTES} bytes from tail of {len(tail)}"
        )
    values = struct.unpack_from(_endian_prefix(endian) + "9f", tail, offset)
    return {
        "offset": offset,
        "rotation_xyz": list(values[0:3]),
        "scale_xyz": list(values[3:6]),
        "translation_xyz": list(values[6:9]),
    }


def _all_finite(values: Iterable[float]) -> bool:
    return all(math.isfinite(value) for value in values)


def _window_score(decoded: dict) -> tuple[bool, float]:
    """Return a deliberately conservative plausibility score.

    This score is only a triage aid.  It does *not* claim that a high-scoring
    window is the projection transform.  It favors values that look like normal
    authored Softimage projection transforms while still allowing negative scale
    (mirroring), large translations, and rotations beyond one turn.
    """
    rotation = decoded["rotation_xyz"]
    scale = decoded["scale_xyz"]
    translation = decoded["translation_xyz"]
    values = rotation + scale + translation
    if not _all_finite(values):
        return False, float("-inf")
    if any(abs(value) > 1.0e12 for value in values):
        return False, float("-inf")
    if any(abs(value) < 1.0e-12 or abs(value) > 1.0e8 for value in scale):
        return False, float("-inf")

    score = 0.0

    # Authored projection rotations are normally human-scale values.  Keep the
    # bound loose enough for multiple turns and both degree/radian hypotheses.
    score += sum(1.0 for value in rotation if abs(value) <= 1440.0)

    # Scale defaults to 1/1/1.  Reward common authored ranges without requiring
    # the defaults; mirrored (negative) scales remain valid candidates.
    for value in scale:
        magnitude = abs(value)
        if 1.0e-4 <= magnitude <= 1.0e4:
            score += 2.0
        if abs(magnitude - 1.0) <= 1.0e-4:
            score += 1.5

    # Translation may be large on support-space records, but astronomical float
    # reinterpretations should rank below ordinary scene/projection values.
    score += sum(1.0 for value in translation if abs(value) <= 1.0e6)

    # Defaults are common and useful corpus anchors, but do not dominate custom
    # stripe/floor/glow projections.
    score += 0.5 * sum(1.0 for value in rotation if abs(value) <= 1.0e-7)
    score += 0.5 * sum(1.0 for value in translation if abs(value) <= 1.0e-7)
    return True, score


def _mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[row][k] * b[k][col] for k in range(4)) for col in range(4)]
        for row in range(4)
    ]


def _identity() -> list[list[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def rst_matrix(decoded: dict, rotation_unit: str = "degrees") -> list[list[float]]:
    """Build a column-vector matrix applying Euler XYZ, then scale, then translate.

    ``rotation_xyz`` means rotate around X, then Y, then Z.  For column vectors
    the rotation product is therefore ``Rz @ Ry @ Rx`` and the complete observed
    operation order is ``T @ S @ R``.
    """
    rx, ry, rz = decoded["rotation_xyz"]
    if rotation_unit == "degrees":
        rx, ry, rz = map(math.radians, (rx, ry, rz))
    sx, sy, sz = decoded["scale_xyz"]
    tx, ty, tz = decoded["translation_xyz"]

    cx, sxr = math.cos(rx), math.sin(rx)
    cy, syr = math.cos(ry), math.sin(ry)
    cz, szr = math.cos(rz), math.sin(rz)

    rot_x = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, cx, -sxr, 0.0],
        [0.0, sxr, cx, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    rot_y = [
        [cy, 0.0, syr, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [-syr, 0.0, cy, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    rot_z = [
        [cz, -szr, 0.0, 0.0],
        [szr, cz, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    scale = [
        [sx, 0.0, 0.0, 0.0],
        [0.0, sy, 0.0, 0.0],
        [0.0, 0.0, sz, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    translate = _identity()
    translate[0][3], translate[1][3], translate[2][3] = tx, ty, tz

    rotation = _mat_mul(rot_z, _mat_mul(rot_y, rot_x))
    return _mat_mul(translate, _mat_mul(scale, rotation))


def _load_records(paths: list[Path]) -> list[dict]:
    records: list[dict] = []
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        records.extend(_iter_projection_records(document, path))
    return records


def rank_offsets(records: list[dict], endian: str, top: int) -> list[dict]:
    if not records:
        return []
    max_offset = min(len(record["tail"]) for record in records) - FLOAT_BYTES
    if max_offset < 0:
        return []

    ranked = []
    for offset in range(max_offset + 1):
        scores: list[float] = []
        valid_count = 0
        mapping_codes: Counter[str] = Counter()
        examples = []
        for record in records:
            try:
                decoded = decode_rst(record["tail"], offset, endian)
            except (ValueError, struct.error):
                continue
            valid, score = _window_score(decoded)
            if not valid:
                continue
            valid_count += 1
            scores.append(score)
            mapping_codes[str(record.get("mapping_code_candidate"))] += 1
            if len(examples) < 3:
                examples.append(
                    {
                        "model_name": record.get("model_name"),
                        "texture_object": record.get("texture_object"),
                        **decoded,
                    }
                )
        if not valid_count:
            continue
        coverage = valid_count / len(records)
        mean_score = sum(scores) / valid_count
        # Coverage dominates.  A true fixed-layout field should decode sensibly
        # across most records, while a coincidental float window usually will not.
        corpus_score = coverage * 100.0 + mean_score
        ranked.append(
            {
                "offset": offset,
                "valid_count": valid_count,
                "record_count": len(records),
                "coverage": coverage,
                "mean_plausibility_score": mean_score,
                "corpus_score": corpus_score,
                "mapping_code_distribution": dict(mapping_codes),
                "examples": examples,
            }
        )
    ranked.sort(
        key=lambda item: (
            item["corpus_score"],
            item["coverage"],
            item["mean_plausibility_score"],
            -item["offset"],
        ),
        reverse=True,
    )
    return ranked[:top]


def decode_at_offset(
    records: list[dict], offset: int, endian: str, rotation_unit: str
) -> list[dict]:
    output = []
    for record in records:
        if offset + FLOAT_BYTES > len(record["tail"]):
            continue
        decoded = decode_rst(record["tail"], offset, endian)
        valid, score = _window_score(decoded)
        output.append(
            {
                key: value
                for key, value in record.items()
                if key != "tail"
            }
            | {
                **decoded,
                "plausible": valid,
                "plausibility_score": score if valid else None,
                "matrix_application_order": "Rxyz -> Sxyz -> Txyz",
                "matrix_convention": "column vectors; M = T @ S @ Rz @ Ry @ Rx",
                "rotation_unit": rotation_unit,
                "matrix4x4": rst_matrix(decoded, rotation_unit),
            }
        )
    return output


def _almost_equal(a: float, b: float, epsilon: float = 1.0e-6) -> bool:
    return abs(a - b) <= epsilon


def self_test() -> None:
    identity_decoded = {
        "rotation_xyz": [0.0, 0.0, 0.0],
        "scale_xyz": [1.0, 1.0, 1.0],
        "translation_xyz": [0.0, 0.0, 0.0],
    }
    assert rst_matrix(identity_decoded) == _identity()

    sample = {
        "rotation_xyz": [0.0, 0.0, 90.0],
        "scale_xyz": [2.0, 3.0, 4.0],
        "translation_xyz": [10.0, 20.0, 30.0],
    }
    matrix = rst_matrix(sample)
    expected = [
        [0.0, -2.0, 0.0, 10.0],
        [3.0, 0.0, 0.0, 20.0],
        [0.0, 0.0, 4.0, 30.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    for row in range(4):
        for col in range(4):
            assert _almost_equal(matrix[row][col], expected[row][col])

    prefix = b"sixbyt"
    packed = struct.pack(">9f", *(sample["rotation_xyz"] + sample["scale_xyz"] + sample["translation_xyz"]))
    tail = prefix + packed + bytes(DEFAULT_TAIL_BYTES - len(prefix) - len(packed))
    decoded = decode_rst(tail, len(prefix), "big")
    assert decoded["rotation_xyz"] == sample["rotation_xyz"]
    assert decoded["scale_xyz"] == sample["scale_xyz"]
    assert decoded["translation_xyz"] == sample["translation_xyz"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sidecars",
        nargs="*",
        type=Path,
        help="One or more scene.model_textures.json files",
    )
    parser.add_argument(
        "--offset",
        type=int,
        help="Decode a confirmed/candidate post-path TXMP byte offset instead of scanning",
    )
    parser.add_argument(
        "--endian",
        choices=("big", "little"),
        default="big",
        help="Float byte order (TXMP structural evidence currently favors big-endian)",
    )
    parser.add_argument(
        "--rotation-unit",
        choices=("degrees", "radians"),
        default="degrees",
        help="Unit used when constructing the diagnostic matrix",
    )
    parser.add_argument("--top", type=int, default=12, help="Number of ranked offsets")
    parser.add_argument("--json-out", type=Path, help="Optional JSON report path")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run source-independent decode/matrix checks before probing",
    )
    args = parser.parse_args()

    if args.self_test:
        self_test()
        if not args.sidecars:
            print(json.dumps({"self_test": "ok"}, indent=2))
            return 0
    if not args.sidecars:
        parser.error("provide at least one scene.model_textures.json or use --self-test")

    records = _load_records(args.sidecars)
    if not records:
        raise SystemExit("no projection records with txmp_tail_hex found")

    if args.offset is None:
        report = {
            "schema": "bz2-txmp-projection-offset-probe-v1",
            "sidecars": [str(path) for path in args.sidecars],
            "record_count": len(records),
            "endian": args.endian,
            "field_order_hypothesis": "rotation_xyz, scale_xyz, translation_xyz",
            "offset_semantics": "bytes from the first byte after the NUL-terminated TXMP picture path",
            "ranked_offsets": rank_offsets(records, args.endian, args.top),
            "warning": "Ranking is diagnostic evidence only; do not promote an offset without corpus/source correlation.",
        }
    else:
        report = {
            "schema": "bz2-txmp-projection-srt-v1",
            "sidecars": [str(path) for path in args.sidecars],
            "record_count": len(records),
            "offset": args.offset,
            "endian": args.endian,
            "rotation_unit": args.rotation_unit,
            "field_order": "rotation_xyz, scale_xyz, translation_xyz",
            "records": decode_at_offset(
                records, args.offset, args.endian, args.rotation_unit
            ),
        }

    text = json.dumps(report, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

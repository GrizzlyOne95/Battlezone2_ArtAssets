#!/usr/bin/env python3
"""Extract SI_Texture2D matrices and enclosing frame/mesh context from ASCII XSI.

This is a source-evidence tool for the Battlezone 2 art reversal. It does not
interpret matrix direction or projection composition; it preserves the authored
4x4 matrix exactly as serialized so binary TXMP hypotheses can be compared
against source-era dotXSI exports without guessing.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

FRAME_RE = re.compile(r"^\s*Frame\s+(?P<name>[^\s{]+)\s*\{", re.I)
MESH_RE = re.compile(r"^\s*Mesh\s+(?P<name>[^\s{]+)\s*\{", re.I)
TEXTURE_RE = re.compile(r"^\s*SI_Texture2D\s*\{", re.I)
FLOAT_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def _is_identity(matrix: list[list[float]] | None, epsilon: float = 1.0e-6) -> bool:
    if matrix is None:
        return False
    identity = (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    return all(
        abs(matrix[row][col] - identity[row][col]) <= epsilon
        for row in range(4)
        for col in range(4)
    )


def probe(path: Path) -> dict:
    lines = path.read_text(encoding="latin-1").splitlines()
    depth = 0
    contexts: list[dict] = []
    blocks: list[dict] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        frame_match = FRAME_RE.match(line)
        mesh_match = MESH_RE.match(line)
        if frame_match:
            contexts.append(
                {"kind": "frame", "name": frame_match.group("name"), "depth": depth}
            )
        elif mesh_match:
            contexts.append(
                {"kind": "mesh", "name": mesh_match.group("name"), "depth": depth}
            )

        if TEXTURE_RE.match(line):
            frame = next(
                (item["name"] for item in reversed(contexts) if item["kind"] == "frame"),
                None,
            )
            mesh = next(
                (item["name"] for item in reversed(contexts) if item["kind"] == "mesh"),
                None,
            )
            body: list[str] = []
            local_depth = 1
            cursor = index + 1
            while cursor < len(lines) and local_depth > 0:
                body.append(lines[cursor])
                local_depth += lines[cursor].count("{") - lines[cursor].count("}")
                cursor += 1

            picture = next(
                (
                    match.group(1)
                    for row in body
                    if (match := re.search(r'"([^"]+)"', row))
                ),
                None,
            )
            matrix = None
            for start in range(max(0, len(body) - 3)):
                rows = body[start : start + 4]
                values = [float(value) for value in FLOAT_RE.findall(" ".join(rows))]
                if len(values) == 16 and all("," in row for row in rows):
                    matrix = [values[row * 4 : (row + 1) * 4] for row in range(4)]
                    break

            blocks.append(
                {
                    "line": index + 1,
                    "frame": frame,
                    "mesh": mesh,
                    "picture": picture,
                    "matrix4x4": matrix,
                    "matrix_identity": _is_identity(matrix),
                }
            )
            index = cursor - 1

        depth += line.count("{") - line.count("}")
        contexts = [item for item in contexts if depth > item["depth"]]
        index += 1

    return {
        "schema": "bz2-ascii-xsi-si-texture2d-probe-v1",
        "source": str(path),
        "block_count": len(blocks),
        "blocks": blocks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xsi", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    result = probe(args.xsi)
    text = json.dumps(result, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

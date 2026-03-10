#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / "modelsdirectory"
OUTPUT_ROOT = ROOT / "artifacts"
REPORTS_DIR = OUTPUT_ROOT / "reports"
EXTRACT_DIR = OUTPUT_ROOT / "extracts"

TOOLS_DIR = ROOT / "tools" / "io_scene_bz2xsi"
if TOOLS_DIR.exists():
    sys.path.insert(0, str(TOOLS_DIR))

try:
    import bz2xsi  # type: ignore
except Exception as exc:  # pragma: no cover - runtime wiring
    bz2xsi = None
    BZ2XSI_IMPORT_ERROR = str(exc)
else:
    BZ2XSI_IMPORT_ERROR = None
    bz2xsi.ALLOW_PRINT = False

try:
    import OpenImageIO as oiio  # type: ignore
except Exception as exc:  # pragma: no cover - runtime wiring
    oiio = None
    OIIO_IMPORT_ERROR = str(exc)
else:
    OIIO_IMPORT_ERROR = None


def find_oiiotool() -> Path | None:
    exe = shutil.which("oiiotool")
    if exe:
        return Path(exe)

    user_base = Path.home() / "AppData" / "Roaming" / "Python" / f"Python{sys.version_info.major}{sys.version_info.minor}" / "Scripts" / "oiiotool.exe"
    if user_base.exists():
        return user_base
    return None


OIIO_TOOL = find_oiiotool()


IMAGE_EXTS = {".pic", ".tga", ".png", ".jpg", ".jpeg", ".bmp", ".gif"}
CONVERTIBLE_IMAGE_EXTS = {".pic", ".tga", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".psd"}
TEXT_SCENE_EXTS = {".dsc", ".sts", ".shd", ".chn"}
BINARY_EXTS = {".hrc", ".mtr", ".txt", ".ani", ".lig", ".cam", ".cls", ".t3d", ".pic"}
SERVER_PATH_RE = re.compile(rb"//Server/Battlezone/modelsdirectory/([^\x00]+)", re.IGNORECASE)
PRINTABLE_RE = re.compile(rb"[ -~]{4,}")
PICTURE_CHAPTERS = {"PICTURES"}
CHAPTER_EXTENSIONS = {
    "ANIMATION": ".ani",
    "CAMERAS": ".cam",
    "CAMERA_SHADERS": ".shd",
    "CLUSTERS": ".cls",
    "EXPRESSIONS": ".exp",
    "LIGHTS": ".lig",
    "MATERIALS": ".mtr",
    "MODELS": ".hrc",
    "OUTPUT_SHADERS": ".shd",
    "SETUP_CHANNELS": ".chn",
    "SETUP_SOFT": ".sts",
    "SHAPES": ".shp",
    "TEXTURES2D": ".txt",
    "TEXTURES3D": ".t3d",
    "VOLUME_SHADERS": ".shd",
    "WAVES": ".wav",
}
ANIMATION_TARGET_CHAPTERS = (
    "MODELS",
    "SHAPES",
    "MATERIALS",
    "TEXTURES2D",
    "TEXTURES3D",
    "LIGHTS",
    "CAMERAS",
    "CLUSTERS",
    "SCENES",
)
NAME_VERSION_RE = re.compile(r"^(.*?)(\.\d+-\d+)$")
MAX_POLYGON_CORNERS = 32
ISDF_SCAVENGER_BODY_OFFSET = [0.0, -7.886654, 1.047626]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def iter_files() -> Iterable[Path]:
    return (p for p in MODEL_ROOT.rglob("*") if p.is_file())


def build_inventory() -> dict:
    ext_counts: Counter[str] = Counter()
    dir_counts: Counter[str] = Counter()
    total_files = 0

    for path in iter_files():
        total_files += 1
        ext_counts[path.suffix.lower()] += 1
        top = path.relative_to(MODEL_ROOT).parts[0]
        dir_counts[top] += 1

    xsi_files = sorted(rel(p) for p in MODEL_ROOT.rglob("*.xsi"))
    dsc_files = sorted(rel(p) for p in MODEL_ROOT.rglob("*.dsc"))

    return {
        "root": rel(MODEL_ROOT),
        "total_files": total_files,
        "extension_counts": dict(sorted(ext_counts.items(), key=lambda item: (-item[1], item[0]))),
        "top_level_file_counts": dict(sorted(dir_counts.items(), key=lambda item: (-item[1], item[0]))),
        "xsi_files": xsi_files,
        "scene_files": dsc_files,
        "notes": {
            "xsi_count": len(xsi_files),
            "scene_count": len(dsc_files),
            "text_scene_exts": sorted(TEXT_SCENE_EXTS),
            "binary_exts": sorted(BINARY_EXTS),
        },
    }


def parse_dsc_file(path: Path) -> dict:
    text = path.read_text(encoding="latin-1", errors="replace")
    lines = text.splitlines()
    header = lines[0].strip() if lines else ""
    chapters = []
    current = None
    current_relation = None
    in_elements = False
    in_relations = False
    in_environment = False
    relations = []
    environment = []

    chapter_re = re.compile(r"^\s*CHAPTER\s+(\S+)\s+NBELEM\s+(\d+)")
    entry_re = re.compile(r"^\s*(.+?)\s*(ROOT)?\s*;\s*$")
    relation_chapter_re = re.compile(r"^\s*CHAPTER\s+(\S+)\s+CHAPTER\s+(\S+)\s*$")
    relation_entry_re = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s*;\s*$")
    environment_chapter_re = re.compile(r"^\s*CHAPTER\s+(\S+)\s*$")
    environment_entry_re = re.compile(r"^\s*(\d+)\s+(.*)\s*;\s*$")

    for raw_line in lines[1:]:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped == "ELEMENTS":
            in_elements = True
            in_relations = False
            in_environment = False
            current = None
            current_relation = None
            continue

        if stripped == "RELATIONS":
            in_elements = False
            in_relations = True
            in_environment = False
            current = None
            current_relation = None
            continue

        if stripped == "ENVIRONMENT":
            in_elements = False
            in_relations = False
            in_environment = True
            current = None
            current_relation = None
            continue

        if stripped.startswith("EndOf"):
            if stripped == "EndOfELEMENTS":
                in_elements = False
            elif stripped == "EndOfRELATIONS":
                in_relations = False
            elif stripped == "EndOfENVIRONMENT":
                in_environment = False

        if in_environment:
            match = environment_chapter_re.match(line)
            if match:
                current = {
                    "chapter": match.group(1),
                    "entries": [],
                }
                environment.append(current)
                continue

            if stripped == "EndOfCHAPTER":
                current = None
                continue

            if current is None:
                continue

            match = environment_entry_re.match(line)
            if not match:
                continue

            current["entries"].append(
                {
                    "index": int(match.group(1)),
                    "raw": match.group(2).strip(),
                }
            )
            continue

        if in_relations:
            match = relation_chapter_re.match(line)
            if match:
                current_relation = {
                    "source_chapter": match.group(1),
                    "target_chapter": match.group(2),
                    "entries": [],
                }
                relations.append(current_relation)
                continue

            if stripped == "EndOfCHAPTER":
                current_relation = None
                continue

            if current_relation is None:
                continue

            match = relation_entry_re.match(line)
            if not match:
                continue

            current_relation["entries"].append(
                {
                    "source_index": int(match.group(1)),
                    "target_index": int(match.group(2)),
                    "relation_code": int(match.group(3)),
                }
            )
            continue

        if not in_elements:
            continue

        match = chapter_re.match(line)
        if match:
            current = {
                "chapter": match.group(1),
                "declared_count": int(match.group(2)),
                "entries": [],
            }
            chapters.append(current)
            continue

        if line.strip() == "EndOfCHAPTER":
            current = None
            continue

        if current is None:
            continue

        match = entry_re.match(line)
        if not match:
            continue

        entry_name = match.group(1).strip()
        is_root = bool(match.group(2))
        current["entries"].append({"name": entry_name, "root": is_root})

    return {
        "path": rel(path),
        "header": header,
        "chapter_count": len(chapters),
        "chapters": [
            {
                "chapter": chapter["chapter"],
                "declared_count": chapter["declared_count"],
                "actual_count": len(chapter["entries"]),
                "root_count": sum(1 for entry in chapter["entries"] if entry["root"]),
                "entries": chapter["entries"],
            }
            for chapter in chapters
        ],
        "relations": relations,
        "environment": environment,
    }


def parse_all_dsc() -> dict:
    scenes = [parse_dsc_file(path) for path in sorted(MODEL_ROOT.rglob("*.dsc"))]
    by_chapter: Counter[str] = Counter()

    for scene in scenes:
        for chapter in scene["chapters"]:
            by_chapter[chapter["chapter"]] += chapter["actual_count"]

    return {
        "scene_count": len(scenes),
        "chapter_entry_totals": dict(sorted(by_chapter.items(), key=lambda item: (-item[1], item[0]))),
        "scenes": scenes,
    }


def decode_comment(data: bytes) -> str | None:
    if len(data) < 88:
        return None
    raw = data[8:88].split(b"\x00", 1)[0].strip()
    if not raw:
        return None
    return raw.decode("latin-1", errors="replace")


def first_marker(data: bytes) -> str | None:
    for marker in (b"HRCH", b"MTRL", b"TXMP", b"PROT"):
        pos = data.find(marker)
        if pos != -1:
            if marker in (b"HRCH", b"MTRL", b"TXMP"):
                tail = data[pos : pos + 96].split(b"\x00", 1)[0]
                return tail.decode("latin-1", errors="replace")
            return marker.decode("ascii")
    return None


def parse_texture_map(path: Path) -> dict:
    data = path.read_bytes()
    source = None
    match = SERVER_PATH_RE.search(data)
    if match:
        source = match.group(1).decode("latin-1", errors="replace").replace("\\", "/")
    marker = first_marker(data)
    strings = [s.decode("latin-1", errors="replace") for s in PRINTABLE_RE.findall(data[:2048])]
    return {
        "path": rel(path),
        "comment": decode_comment(data),
        "marker": marker,
        "source_picture": source,
        "strings": strings[:8],
    }


def parse_all_texture_maps() -> dict:
    entries = [parse_texture_map(path) for path in sorted(MODEL_ROOT.rglob("*.txt"))]
    return {
        "count": len(entries),
        "entries": entries,
    }


def fingerprint_binary_formats() -> dict:
    summary: dict[str, dict] = {}

    for ext in sorted(BINARY_EXTS):
        files = sorted(MODEL_ROOT.rglob(f"*{ext}"))
        if not files:
            continue

        magic_counts: Counter[str] = Counter()
        marker_counts: Counter[str] = Counter()
        samples = []

        for path in files:
            data = path.read_bytes()[:2048]
            magic = data[:4].hex(" ")
            marker = first_marker(data) or "<none>"
            magic_counts[magic] += 1
            marker_counts[marker] += 1

        for path in files[:10]:
            data = path.read_bytes()[:2048]
            samples.append(
                {
                    "path": rel(path),
                    "comment": decode_comment(data),
                    "marker": first_marker(data),
                    "strings": [
                        s.decode("latin-1", errors="replace")
                        for s in PRINTABLE_RE.findall(data)[:6]
                    ],
                }
            )

        summary[ext] = {
            "count": len(files),
            "magic_counts": dict(sorted(magic_counts.items(), key=lambda item: (-item[1], item[0]))),
            "marker_counts": dict(sorted(marker_counts.items(), key=lambda item: (-item[1], item[0]))),
            "samples": samples,
        }

    return summary


def build_texture_index() -> tuple[dict[str, list[Path]], dict[str, list[Path]]]:
    by_name: dict[str, list[Path]] = defaultdict(list)
    by_stem: dict[str, list[Path]] = defaultdict(list)
    for path in iter_files():
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        by_name[path.name.lower()].append(path)
        by_stem[path.stem.lower()].append(path)
    return by_name, by_stem


def build_server_picture_index() -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = defaultdict(list)
    for path in iter_files():
        if path.suffix.lower() not in CONVERTIBLE_IMAGE_EXTS | {".pct"}:
            continue
        relative = path.relative_to(MODEL_ROOT).with_suffix("").as_posix().lower()
        index[relative].append(path)
    return index


def resolve_texture(texture_name: str | None, source_dir: Path, by_name: dict[str, list[Path]], by_stem: dict[str, list[Path]]) -> Path | None:
    if not texture_name:
        return None

    name = Path(texture_name).name
    exact_local = source_dir / name
    if exact_local.exists():
        return exact_local

    candidates = by_name.get(name.lower(), [])
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        for candidate in candidates:
            if candidate.parent == source_dir:
                return candidate
        return candidates[0]

    stem = Path(name).stem.lower()
    stem_candidates = by_stem.get(stem, [])
    if len(stem_candidates) == 1:
        return stem_candidates[0]
    return stem_candidates[0] if stem_candidates else None


def resolve_server_picture(entry_name: str, picture_index: dict[str, list[Path]], by_name: dict[str, list[Path]], by_stem: dict[str, list[Path]]) -> Path | None:
    normalized = entry_name.replace("\\", "/").strip()
    normalized = re.sub(r"^//server/battlezone/modelsdirectory/", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.strip("/").lower()
    if not normalized:
        return None

    matches = picture_index.get(normalized, [])
    if matches:
        return matches[0]

    name = Path(normalized).name
    exact = by_name.get(name.lower(), [])
    if exact:
        return exact[0]

    stem_matches = by_stem.get(Path(normalized).stem.lower(), [])
    return stem_matches[0] if stem_matches else None


def sanitize_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "unnamed"


def matrix_to_rows(matrix: object | None) -> list[list[float]]:
    if matrix is None:
        return [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    return [list(row) for row in matrix.to_list()]


def mul_row_major(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    out = [[0.0] * 4 for _ in range(4)]
    for r in range(4):
        for c in range(4):
            out[r][c] = sum(a[r][k] * b[k][c] for k in range(4))
    return out


def transform_point(point: tuple[float, float, float], matrix: list[list[float]]) -> tuple[float, float, float]:
    x, y, z = point
    return (
        x * matrix[0][0] + y * matrix[1][0] + z * matrix[2][0] + matrix[3][0],
        x * matrix[0][1] + y * matrix[1][1] + z * matrix[2][1] + matrix[3][1],
        x * matrix[0][2] + y * matrix[1][2] + z * matrix[2][2] + matrix[3][2],
    )


def transform_vector(vector: tuple[float, float, float], matrix: list[list[float]]) -> tuple[float, float, float]:
    x, y, z = vector
    tx = x * matrix[0][0] + y * matrix[1][0] + z * matrix[2][0]
    ty = x * matrix[0][1] + y * matrix[1][1] + z * matrix[2][1]
    tz = x * matrix[0][2] + y * matrix[1][2] + z * matrix[2][2]
    mag = math.sqrt(tx * tx + ty * ty + tz * tz)
    if mag > 0:
        return (tx / mag, ty / mag, tz / mag)
    return (tx, ty, tz)


def matrix_translation_xyz(matrix: list[list[float]]) -> list[float]:
    return [round(matrix[3][0], 6), round(matrix[3][1], 6), round(matrix[3][2], 6)]


def row_major_to_gltf_matrix(matrix: list[list[float]]) -> list[float]:
    return [float(value) for row in matrix for value in row]


def normalize_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = vector
    mag = math.sqrt((x * x) + (y * y) + (z * z))
    if mag <= 0:
        return (0.0, 0.0, 0.0)
    return (x / mag, y / mag, z / mag)


def quaternion_from_euler_xyz(rotation_xyz: tuple[float, float, float], *, degrees: bool = False) -> list[float]:
    rx, ry, rz = rotation_xyz
    if degrees:
        rx = math.radians(rx)
        ry = math.radians(ry)
        rz = math.radians(rz)

    cx = math.cos(rx * 0.5)
    sx = math.sin(rx * 0.5)
    cy = math.cos(ry * 0.5)
    sy = math.sin(ry * 0.5)
    cz = math.cos(rz * 0.5)
    sz = math.sin(rz * 0.5)

    qw = (cx * cy * cz) + (sx * sy * sz)
    qx = (sx * cy * cz) - (cx * sy * sz)
    qy = (cx * sy * cz) + (sx * cy * sz)
    qz = (cx * cy * sz) - (sx * sy * cz)
    return [qx, qy, qz, qw]


def quaternion_from_two_vectors(
    src: tuple[float, float, float],
    dst: tuple[float, float, float],
) -> list[float]:
    ax, ay, az = normalize_vector(src)
    bx, by, bz = normalize_vector(dst)
    dot = max(-1.0, min(1.0, (ax * bx) + (ay * by) + (az * bz)))

    if dot < -0.999999:
        axis = normalize_vector((0.0, -az, ay))
        if axis == (0.0, 0.0, 0.0):
            axis = normalize_vector((-bz, 0.0, bx))
        return [axis[0], axis[1], axis[2], 0.0]

    cross = (
        (ay * bz) - (az * by),
        (az * bx) - (ax * bz),
        (ax * by) - (ay * bx),
    )
    qw = 1.0 + dot
    qx, qy, qz = cross
    mag = math.sqrt((qx * qx) + (qy * qy) + (qz * qz) + (qw * qw))
    if mag <= 0:
        return [0.0, 0.0, 0.0, 1.0]
    return [qx / mag, qy / mag, qz / mag, qw / mag]


def build_srt_matrix(
    scale_xyz: tuple[float, float, float] | list[float],
    rotation_xyz: tuple[float, float, float] | list[float],
    translation_xyz: tuple[float, float, float] | list[float],
) -> list[list[float]]:
    sx, sy, sz = scale_xyz
    rx, ry, rz = rotation_xyz
    tx, ty, tz = translation_xyz

    cx, sxn = math.cos(rx), math.sin(rx)
    cy, syn = math.cos(ry), math.sin(ry)
    cz, szn = math.cos(rz), math.sin(rz)

    rot_x = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, cx, sxn, 0.0],
        [0.0, -sxn, cx, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    rot_y = [
        [cy, 0.0, -syn, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [syn, 0.0, cy, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    rot_z = [
        [cz, szn, 0.0, 0.0],
        [-szn, cz, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    scale = [
        [sx, 0.0, 0.0, 0.0],
        [0.0, sy, 0.0, 0.0],
        [0.0, 0.0, sz, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    translate = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [tx, ty, tz, 1.0],
    ]
    return mul_row_major(scale, mul_row_major(rot_x, mul_row_major(rot_y, mul_row_major(rot_z, translate))))


def ensure_png_direct(source: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{source.stem}.png"
    if dest.exists():
        return dest

    if source.suffix.lower() == ".png":
        shutil.copy2(source, dest)
        return dest

    if oiio is None:
        raise RuntimeError(f"OpenImageIO is unavailable: {OIIO_IMPORT_ERROR}")

    image = oiio.ImageBuf(str(source))
    if image.has_error:
        if OIIO_TOOL is None:
            raise RuntimeError(image.geterror())
    else:
        if image.write(str(dest)):
            return dest
        if OIIO_TOOL is None:
            raise RuntimeError(image.geterror())

    result = subprocess.run(
        [str(OIIO_TOOL), str(source), "-o", str(dest)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0 or not dest.exists():
        raise RuntimeError((result.stderr or result.stdout).strip() or f"oiiotool failed for {source}")
    return dest


def ensure_png(source: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{source.stem}.png"
    if dest.exists():
        return dest

    if source.suffix.lower() == ".png":
        shutil.copy2(source, dest)
        return dest

    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "image-file", str(source), str(dest_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        if not detail:
            detail = f"image-file exited with code {result.returncode}"
        message = f"{detail} ({source})"
        raise RuntimeError(message)

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"image-file returned invalid JSON for {source}: {exc}") from exc

    png_path = Path(payload["png"])
    if not png_path.exists():
        raise RuntimeError(f"image-file did not produce {png_path}")
    return png_path


def pack_floats(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values) if values else b""


def pack_uints(values: list[int]) -> bytes:
    return struct.pack(f"<{len(values)}I", *values) if values else b""


def gltf_add_chunk(buffer: bytearray, payload: bytes) -> tuple[int, int]:
    offset = len(buffer)
    buffer.extend(payload)
    while len(buffer) % 4:
        buffer.append(0)
    return offset, len(payload)


def gltf_accessor(
    accessors: list[dict],
    buffer_views: list[dict],
    buffer: bytearray,
    payload: bytes,
    component_type: int,
    accessor_type: str,
    count: int,
    *,
    target: int | None = None,
    minimum: list[float] | None = None,
    maximum: list[float] | None = None,
) -> int:
    offset, length = gltf_add_chunk(buffer, payload)
    view = {"buffer": 0, "byteOffset": offset, "byteLength": length}
    if target is not None:
        view["target"] = target
    buffer_views.append(view)
    accessor = {
        "bufferView": len(buffer_views) - 1,
        "componentType": component_type,
        "count": count,
        "type": accessor_type,
    }
    if minimum is not None:
        accessor["min"] = minimum
    if maximum is not None:
        accessor["max"] = maximum
    accessors.append(accessor)
    return len(accessors) - 1


def walk_frames(frames: Iterable[object], parent_matrix: list[list[float]] | None = None) -> Iterable[tuple[object, list[list[float]]]]:
    parent_matrix = parent_matrix or matrix_to_rows(None)
    for frame in frames:
        local = matrix_to_rows(getattr(frame, "transform", None))
        world = mul_row_major(local, parent_matrix)
        yield frame, world
        yield from walk_frames(getattr(frame, "frames", []), world)


def export_xsi_file(path: Path, by_name: dict[str, list[Path]], by_stem: dict[str, list[Path]]) -> dict:
    if bz2xsi is None:
        raise RuntimeError(f"bz2xsi is unavailable: {BZ2XSI_IMPORT_ERROR}")

    scene = bz2xsi.read(str(path))
    scene_dir = EXTRACT_DIR / "xsi" / path.relative_to(MODEL_ROOT).with_suffix("")
    scene_dir.mkdir(parents=True, exist_ok=True)
    obj_path = scene_dir / f"{sanitize_name(path.stem)}.obj"
    mtl_path = scene_dir / f"{sanitize_name(path.stem)}.mtl"
    gltf_path = scene_dir / f"{sanitize_name(path.stem)}.gltf"
    gltf_bin_path = scene_dir / f"{sanitize_name(path.stem)}.bin"
    tex_dir = scene_dir / "textures"
    metadata_path = scene_dir / "scene.json"

    material_keys: dict[tuple, str] = {}
    material_defs: dict[str, dict] = {}
    material_order: list[str] = []
    texture_exports: dict[str, str] = {}

    v_offset = 1
    vt_offset = 1
    vn_offset = 1

    with obj_path.open("w", encoding="utf-8") as obj:
        obj.write(f"mtllib {mtl_path.name}\n")

        for frame, world in walk_frames(scene.frames):
            mesh = getattr(frame, "mesh", None)
            if not mesh or not mesh.vertices or not mesh.faces:
                continue

            obj.write(f"\no {sanitize_name(frame.name)}\n")

            transformed_vertices = [transform_point(tuple(v), world) for v in mesh.vertices]
            transformed_normals = [transform_vector(tuple(v), world) for v in mesh.normal_vertices]

            for vx, vy, vz in transformed_vertices:
                obj.write(f"v {vx:.6f} {vy:.6f} {vz:.6f}\n")

            for uv in mesh.uv_vertices:
                if len(uv) == 2:
                    obj.write(f"vt {uv[0]:.6f} {1.0 - uv[1]:.6f}\n")

            for nx, ny, nz in transformed_normals:
                obj.write(f"vn {nx:.6f} {ny:.6f} {nz:.6f}\n")

            for face_index, face in enumerate(mesh.faces):
                material = mesh.face_materials[face_index] if face_index < len(mesh.face_materials) else None
                mat_key = None
                if material is not None:
                    mat_key = (
                        tuple(material.diffuse),
                        float(material.hardness),
                        tuple(material.specular),
                        tuple(material.ambient),
                        tuple(material.emissive),
                        int(material.shading_type),
                        str(material.texture),
                    )
                    if mat_key not in material_keys:
                        mat_name = f"mat_{len(material_keys):03d}_{sanitize_name(frame.name)}"
                        material_keys[mat_key] = mat_name
                        material_order.append(mat_name)
                        tex_path = resolve_texture(material.texture, path.parent, by_name, by_stem)
                        png_path = None
                        if tex_path is not None:
                            png_path = ensure_png(tex_path, tex_dir)
                            texture_exports[rel(tex_path)] = rel(png_path)
                        material_defs[mat_name] = {
                            "diffuse": list(material.diffuse),
                            "hardness": float(material.hardness),
                            "specular": list(material.specular),
                            "ambient": list(material.ambient),
                            "emissive": list(material.emissive),
                            "shading_type": int(material.shading_type),
                            "texture_source": rel(tex_path) if tex_path else None,
                            "texture_png": png_path.name if png_path else None,
                        }
                    obj.write(f"usemtl {material_keys[mat_key]}\n")

                uv_face = mesh.uv_faces[face_index] if face_index < len(mesh.uv_faces) else None
                normal_face = mesh.normal_faces[face_index] if face_index < len(mesh.normal_faces) else None
                refs = []
                for corner, vertex_index in enumerate(face):
                    v_idx = v_offset + vertex_index
                    vt_idx = ""
                    vn_idx = ""
                    if uv_face and corner < len(uv_face):
                        vt_idx = str(vt_offset + uv_face[corner])
                    if normal_face and corner < len(normal_face):
                        vn_idx = str(vn_offset + normal_face[corner])

                    if vt_idx and vn_idx:
                        refs.append(f"{v_idx}/{vt_idx}/{vn_idx}")
                    elif vt_idx:
                        refs.append(f"{v_idx}/{vt_idx}")
                    elif vn_idx:
                        refs.append(f"{v_idx}//{vn_idx}")
                    else:
                        refs.append(str(v_idx))
                obj.write("f " + " ".join(refs) + "\n")

            v_offset += len(mesh.vertices)
            vt_offset += len(mesh.uv_vertices)
            vn_offset += len(mesh.normal_vertices)

    with mtl_path.open("w", encoding="utf-8") as mtl:
        for name, mat in material_defs.items():
            diffuse = mat["diffuse"]
            specular = mat["specular"]
            alpha = diffuse[3] if len(diffuse) >= 4 else 1.0
            mtl.write(f"newmtl {name}\n")
            mtl.write(f"Kd {diffuse[0]:.6f} {diffuse[1]:.6f} {diffuse[2]:.6f}\n")
            mtl.write(f"Ks {specular[0]:.6f} {specular[1]:.6f} {specular[2]:.6f}\n")
            mtl.write(f"d {alpha:.6f}\n")
            if mat["texture_png"]:
                mtl.write(f"map_Kd textures/{mat['texture_png']}\n")
            mtl.write("\n")

    images = []
    textures = []
    gltf_materials = []
    material_index_map: dict[str, int] = {}
    for mat_name in material_order:
        mat = material_defs[mat_name]
        material_json = {
            "name": mat_name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [
                    float(mat["diffuse"][0]),
                    float(mat["diffuse"][1]),
                    float(mat["diffuse"][2]),
                    float(mat["diffuse"][3] if len(mat["diffuse"]) >= 4 else 1.0),
                ],
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
            "doubleSided": True,
        }
        if mat["texture_png"]:
            images.append({"uri": f"textures/{mat['texture_png']}"})
            textures.append({"source": len(images) - 1})
            material_json["pbrMetallicRoughness"]["baseColorTexture"] = {"index": len(textures) - 1}
        gltf_materials.append(material_json)
        material_index_map[mat_name] = len(gltf_materials) - 1

    buffer = bytearray()
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    meshes = []
    nodes = []

    for frame, world in walk_frames(scene.frames):
        mesh = getattr(frame, "mesh", None)
        if not mesh or not mesh.vertices or not mesh.faces:
            continue

        primitive_vertices: dict[str | None, list[tuple[tuple[float, float, float], tuple[float, float] | None, tuple[float, float, float] | None]]] = defaultdict(list)
        primitive_indices: dict[str | None, list[int]] = defaultdict(list)
        primitive_maps: dict[str | None, dict[tuple, int]] = defaultdict(dict)

        for face_index, face in enumerate(mesh.faces):
            mat_name = None
            if face_index < len(mesh.face_materials) and mesh.face_materials[face_index] is not None:
                material = mesh.face_materials[face_index]
                mat_key = (
                    tuple(material.diffuse),
                    float(material.hardness),
                    tuple(material.specular),
                    tuple(material.ambient),
                    tuple(material.emissive),
                    int(material.shading_type),
                    str(material.texture),
                )
                mat_name = material_keys.get(mat_key)

            uv_face = mesh.uv_faces[face_index] if face_index < len(mesh.uv_faces) else None
            normal_face = mesh.normal_faces[face_index] if face_index < len(mesh.normal_faces) else None
            face_corner_indices = []

            for corner, vertex_index in enumerate(face):
                pos = transform_point(tuple(mesh.vertices[vertex_index]), world)
                uv = None
                if uv_face and corner < len(uv_face):
                    src_uv = mesh.uv_vertices[uv_face[corner]]
                    uv = (float(src_uv[0]), float(1.0 - src_uv[1]))
                normal = None
                if normal_face and corner < len(normal_face):
                    normal = transform_vector(tuple(mesh.normal_vertices[normal_face[corner]]), world)
                key = (pos, uv, normal)
                if key not in primitive_maps[mat_name]:
                    primitive_maps[mat_name][key] = len(primitive_vertices[mat_name])
                    primitive_vertices[mat_name].append(key)
                face_corner_indices.append(primitive_maps[mat_name][key])

            for i in range(1, len(face_corner_indices) - 1):
                primitive_indices[mat_name].extend(
                    [face_corner_indices[0], face_corner_indices[i], face_corner_indices[i + 1]]
                )

        gltf_primitives = []
        for mat_name, verts in primitive_vertices.items():
            if not verts:
                continue

            positions = [coord for pos, _, _ in verts for coord in pos]
            pos_tuples = [pos for pos, _, _ in verts]
            pos_min = [min(p[i] for p in pos_tuples) for i in range(3)]
            pos_max = [max(p[i] for p in pos_tuples) for i in range(3)]
            position_accessor = gltf_accessor(
                accessors,
                buffer_views,
                buffer,
                pack_floats(positions),
                5126,
                "VEC3",
                len(verts),
                target=34962,
                minimum=pos_min,
                maximum=pos_max,
            )

            attributes = {"POSITION": position_accessor}

            if any(uv is not None for _, uv, _ in verts):
                texcoords = []
                for _, uv, _ in verts:
                    uv = uv or (0.0, 0.0)
                    texcoords.extend([uv[0], uv[1]])
                attributes["TEXCOORD_0"] = gltf_accessor(
                    accessors,
                    buffer_views,
                    buffer,
                    pack_floats(texcoords),
                    5126,
                    "VEC2",
                    len(verts),
                    target=34962,
                )

            if any(normal is not None for _, _, normal in verts):
                normals = []
                for _, _, normal in verts:
                    normal = normal or (0.0, 0.0, 1.0)
                    normals.extend([normal[0], normal[1], normal[2]])
                attributes["NORMAL"] = gltf_accessor(
                    accessors,
                    buffer_views,
                    buffer,
                    pack_floats(normals),
                    5126,
                    "VEC3",
                    len(verts),
                    target=34962,
                )

            index_list = primitive_indices[mat_name]
            primitive = {
                "attributes": attributes,
                "indices": gltf_accessor(
                    accessors,
                    buffer_views,
                    buffer,
                    pack_uints(index_list),
                    5125,
                    "SCALAR",
                    len(index_list),
                    target=34963,
                ),
            }
            if mat_name is not None and mat_name in material_index_map:
                primitive["material"] = material_index_map[mat_name]
            gltf_primitives.append(primitive)

        if gltf_primitives:
            meshes.append({"name": frame.name, "primitives": gltf_primitives})
            nodes.append({"name": frame.name, "mesh": len(meshes) - 1})

    gltf = {
        "asset": {"version": "2.0", "generator": "bz2_extract.py"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "buffers": [{"byteLength": len(buffer), "uri": gltf_bin_path.name}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "materials": gltf_materials,
        "textures": textures,
        "images": images,
    }
    gltf_path.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    gltf_bin_path.write_bytes(buffer)

    metadata = {
        "source": rel(path),
        "obj": rel(obj_path),
        "mtl": rel(mtl_path),
        "gltf": rel(gltf_path),
        "gltf_bin": rel(gltf_bin_path),
        "textures": texture_exports,
        "mesh_frame_count": sum(1 for frame in scene.get_all_frames() if getattr(frame, "mesh", None)),
        "total_frame_count": sum(1 for _ in scene.get_all_frames()),
        "camera_count": len(scene.cameras),
        "light_count": len(scene.lights),
        "animated_frame_count": sum(1 for _ in scene.get_animated_frames()),
        "cameras": [
            {
                "name": camera.name,
                "position": list(camera.transform.posit[:3]),
                "target": list(camera.target.posit[:3]),
                "roll": camera.roll,
                "near_plane": camera.near_plane,
                "far_plane": camera.far_plane,
            }
            for camera in scene.cameras
        ],
        "lights": [
            {
                "name": light.name,
                "rgb": list(light.rgb),
                "position": list(light.transform.posit[:3]),
            }
            for light in scene.lights
        ],
        "materials": material_defs,
    }
    write_json(metadata_path, metadata)
    return metadata


def export_all_xsi() -> dict:
    by_name, by_stem = build_texture_index()
    outputs = []
    for path in sorted(MODEL_ROOT.rglob("*.xsi")):
        outputs.append(export_xsi_file(path, by_name, by_stem))
    return {
        "count": len(outputs),
        "entries": outputs,
    }


def convert_image_batch(image_paths: list[str]) -> dict:
    entries = []
    failures = []
    for raw_path in image_paths:
        path = Path(raw_path)
        out_dir = EXTRACT_DIR / "images" / path.relative_to(MODEL_ROOT).parent
        try:
            png_path = ensure_png(path, out_dir)
            entries.append({"source": rel(path), "png": rel(png_path)})
        except Exception as exc:
            failures.append({"source": rel(path), "error": str(exc)})

    return {"entries": entries, "failures": failures}


def convert_all_images() -> dict:
    files = [
        str(path)
        for path in sorted(iter_files())
        if path.suffix.lower() in CONVERTIBLE_IMAGE_EXTS
    ]
    batch_size = 800
    entries = []
    failures = []
    batch_dir = OUTPUT_ROOT / "tmp" / "image_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)

    for batch_index, start in enumerate(range(0, len(files), batch_size)):
        batch_paths = files[start : start + batch_size]
        batch_file = batch_dir / f"batch_{batch_index:04d}.txt"
        batch_file.write_text("\n".join(batch_paths), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(Path(__file__)), "images-batch", str(batch_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Image batch {batch_index} failed")
        result_file = batch_file.with_suffix(".json")
        payload = json.loads(result_file.read_text(encoding="utf-8"))
        entries.extend(payload.get("entries", []))
        failures.extend(payload.get("failures", []))

    archive_entries = []
    for zip_path in sorted(MODEL_ROOT.rglob("*.zip")):
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                suffix = Path(member.filename).suffix.lower()
                if suffix not in CONVERTIBLE_IMAGE_EXTS:
                    continue
                archive_dir = EXTRACT_DIR / "archives" / zip_path.relative_to(MODEL_ROOT).with_suffix("")
                extracted_path = archive_dir / member.filename
                extracted_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, extracted_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                out_dir = EXTRACT_DIR / "images" / "_archives" / zip_path.relative_to(MODEL_ROOT).with_suffix("") / Path(member.filename).parent
                try:
                    png_path = ensure_png(extracted_path, out_dir)
                    archive_entries.append(
                        {
                            "archive": rel(zip_path),
                            "member": member.filename.replace("\\", "/"),
                            "extracted": rel(extracted_path),
                            "png": rel(png_path),
                        }
                    )
                except Exception as exc:
                    failures.append(
                        {
                            "archive": rel(zip_path),
                            "member": member.filename.replace("\\", "/"),
                            "error": str(exc),
                        }
                    )

    pct_files = [rel(path) for path in sorted(MODEL_ROOT.rglob("*.pct"))]
    return {
        "converted_count": len(entries),
        "archive_converted_count": len(archive_entries),
        "failed_count": len(failures),
        "pct_count": len(pct_files),
        "pct_files": pct_files,
        "entries": entries,
        "archive_entries": archive_entries,
        "failures": failures,
    }


def scene_entry_aliases(entry_name: str) -> list[str]:
    aliases = [entry_name]
    for source, target in (
        ("tractor_r__h", "tractor_l__h"),
        ("tread_r", "tread_l"),
        ("suspension_r", "suspension_l"),
        ("sidewall_r", "sidewall_l"),
    ):
        if source in entry_name:
            aliases.append(entry_name.replace(source, target))
    return aliases


def resolve_scene_entry(scene_path: Path, chapter: str, entry_name: str, picture_index: dict[str, list[Path]], by_name: dict[str, list[Path]], by_stem: dict[str, list[Path]]) -> Path | None:
    if chapter in PICTURE_CHAPTERS:
        return resolve_server_picture(entry_name, picture_index, by_name, by_stem)

    ext = CHAPTER_EXTENSIONS.get(chapter)
    if not ext:
        return None

    scene_root = scene_path.parent.parent
    chapter_dir = scene_root / chapter
    if not chapter_dir.exists():
        return None

    for candidate_name in scene_entry_aliases(entry_name):
        exact = chapter_dir / f"{candidate_name}{ext}"
        if exact.exists():
            return exact

        globbed = list(chapter_dir.glob(f"{candidate_name}*{ext}"))
        if globbed:
            return sorted(globbed, key=lambda item: (len(item.stem), item.name.lower()))[0]

        version_match = NAME_VERSION_RE.match(candidate_name)
        if version_match:
            base_name, version_suffix = version_match.groups()
            version_glob = list(chapter_dir.glob(f"{base_name}*{version_suffix}{ext}"))
            if version_glob:
                return sorted(version_glob, key=lambda item: (len(item.stem), item.name.lower()))[0]
    return None


def build_scene_dependencies() -> dict:
    by_name, by_stem = build_texture_index()
    picture_index = build_server_picture_index()

    image_map = {}
    image_report_path = REPORTS_DIR / "images.json"
    if image_report_path.exists():
        image_report = json.loads(image_report_path.read_text(encoding="utf-8"))
        for item in image_report.get("entries", []):
            image_map[item["source"]] = item["png"]
        for item in image_report.get("archive_entries", []):
            image_map[item["extracted"]] = item["png"]

    texture_map_lookup = {}
    texture_report_path = REPORTS_DIR / "texture_maps.json"
    if texture_report_path.exists():
        texture_report = json.loads(texture_report_path.read_text(encoding="utf-8"))
        for item in texture_report.get("entries", []):
            texture_map_lookup[item["path"]] = item

    entries = []
    resolved = 0
    missing = 0

    for path in sorted(MODEL_ROOT.rglob("*.dsc")):
        scene = parse_dsc_file(path)
        resolved_chapters = []
        chapter_lookup = {}
        for chapter in scene["chapters"]:
            resolved_entries = []
            for item in chapter["entries"]:
                asset_path = resolve_scene_entry(path, chapter["chapter"], item["name"], picture_index, by_name, by_stem)
                if asset_path is not None:
                    resolved += 1
                else:
                    missing += 1
                resolved_entries.append(
                    {
                        **item,
                        "resolved_path": rel(asset_path) if asset_path else None,
                        "converted_png": image_map.get(rel(asset_path)) if asset_path else None,
                    }
                )
            chapter_lookup[chapter["chapter"]] = resolved_entries
            resolved_chapters.append(
                {
                    "chapter": chapter["chapter"],
                    "declared_count": chapter["declared_count"],
                    "actual_count": chapter["actual_count"],
                    "entries": resolved_entries,
                }
            )

        resolved_relations = []
        model_links = []
        for relation in scene.get("relations", []):
            source_entries = chapter_lookup.get(relation["source_chapter"], [])
            target_entries = chapter_lookup.get(relation["target_chapter"], [])
            resolved_edges = []
            for edge in relation["entries"]:
                source_entry = source_entries[edge["source_index"]] if edge["source_index"] < len(source_entries) else None
                target_entry = target_entries[edge["target_index"]] if edge["target_index"] < len(target_entries) else None
                resolved_edge = {
                    **edge,
                    "source_name": source_entry["name"] if source_entry else None,
                    "target_name": target_entry["name"] if target_entry else None,
                    "source_resolved_path": source_entry.get("resolved_path") if source_entry else None,
                    "target_resolved_path": target_entry.get("resolved_path") if target_entry else None,
                    "target_converted_png": target_entry.get("converted_png") if target_entry else None,
                }
                resolved_edges.append(resolved_edge)
            resolved_relations.append(
                {
                    "source_chapter": relation["source_chapter"],
                    "target_chapter": relation["target_chapter"],
                    "entries": resolved_edges,
                }
            )

        material_to_texture = {}
        for relation in resolved_relations:
            if relation["source_chapter"] == "MATERIALS" and relation["target_chapter"] == "TEXTURES2D":
                for edge in relation["entries"]:
                    if edge["source_name"]:
                        material_to_texture[edge["source_name"]] = edge

        for relation in resolved_relations:
            if relation["source_chapter"] == "MODELS" and relation["target_chapter"] == "MATERIALS":
                for edge in relation["entries"]:
                    material_edge = material_to_texture.get(edge["target_name"])
                    texture_entry = texture_map_lookup.get(material_edge["target_resolved_path"]) if material_edge else None
                    source_picture_path = None
                    source_picture_png = None
                    if texture_entry and texture_entry.get("source_picture"):
                        source_picture_rel = f"modelsdirectory/{texture_entry['source_picture']}.pic"
                        source_picture_path = source_picture_rel
                        source_picture_png = image_map.get(source_picture_rel)
                    model_links.append(
                        {
                            "model_name": edge["source_name"],
                            "model_resolved_path": edge["source_resolved_path"],
                            "material_name": edge["target_name"],
                            "material_resolved_path": edge["target_resolved_path"],
                            "texture_name": material_edge["target_name"] if material_edge else None,
                            "texture_resolved_path": material_edge["target_resolved_path"] if material_edge else None,
                            "texture_converted_png": material_edge["target_converted_png"] if material_edge else None,
                            "texture_source_picture": texture_entry.get("source_picture") if texture_entry else None,
                            "texture_source_picture_path": source_picture_path,
                            "texture_source_picture_png": source_picture_png,
                            "model_material_relation_code": edge["relation_code"],
                            "material_texture_relation_code": material_edge["relation_code"] if material_edge else None,
                        }
                    )

        environment_lookup = {}
        for chapter in scene.get("environment", []):
            resolved_entries = []
            chapter_entries = chapter_lookup.get(chapter["chapter"], [])
            for item in chapter.get("entries", []):
                resolved_entry = {
                    "index": item["index"],
                    "raw": item["raw"],
                    "name": chapter_entries[item["index"]]["name"] if item["index"] < len(chapter_entries) else None,
                    "resolved_path": chapter_entries[item["index"]].get("resolved_path") if item["index"] < len(chapter_entries) else None,
                }
                srt_match = re.search(
                    r"\bSRT\s+"
                    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
                    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
                    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)",
                    item["raw"],
                )
                if srt_match:
                    resolved_entry["srt"] = {
                        "scale": [float(srt_match.group(i)) for i in range(1, 4)],
                        "rotation_xyz": [float(srt_match.group(i)) for i in range(4, 7)],
                        "translation_xyz": [float(srt_match.group(i)) for i in range(7, 10)],
                    }
                resolved_entries.append(resolved_entry)
                if chapter["chapter"] == "MODELS" and resolved_entry.get("name"):
                    environment_lookup[resolved_entry["name"]] = resolved_entry

        entries.append(
            {
                "path": scene["path"],
                "header": scene["header"],
                "chapters": resolved_chapters,
                "relations": resolved_relations,
                "environment": environment_lookup,
                "model_material_texture_links": model_links,
            }
        )

    return {
        "scene_count": len(entries),
        "resolved_entry_count": resolved,
        "missing_entry_count": missing,
        "entries": entries,
    }


def parse_expression_files() -> dict:
    entries = []
    expression_count = 0
    for path in sorted(MODEL_ROOT.rglob("*.exp")):
        text = path.read_text(encoding="latin-1", errors="replace")
        names = re.findall(r"(?m)^EXPRESSION\s+(\S+)", text)
        expression_count += len(names)
        entries.append({"path": rel(path), "expression_count": len(names), "names": names})
    return {"file_count": len(entries), "expression_count": expression_count, "entries": entries}


def parse_animation_target_table(data: bytes) -> dict | None:
    section_name = None
    section_pos = -1
    for chapter in ANIMATION_TARGET_CHAPTERS:
        pos = data.find(chapter.encode("ascii"))
        if pos == -1:
            continue
        if section_pos == -1 or pos < section_pos:
            section_name = chapter
            section_pos = pos
    if section_name is None:
        return None

    cursor = section_pos + len(section_name)
    while cursor < len(data) and data[cursor] == 0:
        cursor += 1

    entries = []
    while cursor < len(data) and data[cursor] == 1:
        cursor += 1
        name_end = data.find(b"\x00\x00\x02", cursor)
        if name_end == -1:
            break

        raw_name = data[cursor:name_end]
        if not raw_name or any(byte < 0x20 or byte > 0x7E for byte in raw_name):
            break

        meta = data[name_end + 3 : name_end + 6]
        if len(meta) < 3:
            break

        entries.append(
            {
                "name": raw_name.decode("latin-1", errors="replace"),
                "meta_hex": meta.hex(),
                "meta_u16_be": int.from_bytes(meta[:2], "big"),
                "meta_s16_be": int.from_bytes(meta[:2], "big", signed=True),
                "meta_u8": meta[2],
            }
        )
        cursor = name_end + 6

    return {
        "target_chapter": section_name,
        "targets": entries,
        "target_stream_offset": cursor,
    }


def parse_animation_files() -> dict:
    entries = []
    total_refs = 0
    target_chapter_counts: Counter[str] = Counter()
    marker_value_counts: Counter[float] = Counter()
    tag_counts: Counter[int] = Counter()
    tagset_counts: Counter[str] = Counter()
    tagset_counts_by_chapter: dict[str, Counter[str]] = {}
    for path in sorted(MODEL_ROOT.rglob("*.ani")):
        data = path.read_bytes()
        parsed = parse_animation_target_table(data)
        targets = parsed["targets"] if parsed else []
        total_refs += len(targets)

        entry = {
            "path": rel(path),
            "target_count": len(targets),
            "targets": targets,
        }
        if parsed:
            entry["target_chapter"] = parsed["target_chapter"]
            entry["target_stream_offset"] = parsed["target_stream_offset"]
            entry.update(summarize_animation_stream(data, parsed["target_stream_offset"]))
            target_chapter_counts[parsed["target_chapter"]] += 1
            if parsed["target_chapter"] == "MODELS":
                entry["model_refs"] = [target["name"] for target in targets]

            for marker_item in entry.get("marker_0007_0001_float_counts", []):
                marker_value_counts[marker_item["value"]] += marker_item["count"]

            for tag, payload in entry.get("u32_tag_float_samples", {}).items():
                tag_counts[int(tag)] += payload.get("count", 0)

            for candidate in entry.get("frame_candidates", []):
                tagset = ",".join(candidate.get("channel_values", {}).keys())
                if not tagset:
                    continue
                tagset_counts[tagset] += 1
                chapter = parsed["target_chapter"]
                if chapter not in tagset_counts_by_chapter:
                    tagset_counts_by_chapter[chapter] = Counter()
                tagset_counts_by_chapter[chapter][tagset] += 1

        entries.append(entry)

    marker_value_summary = [
        {"value": value, "count": count}
        for value, count in sorted(marker_value_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    tag_counts_summary = {str(tag): tag_counts[tag] for tag in sorted(tag_counts)}
    tagset_summary = [
        {"tagset": tagset, "count": count}
        for tagset, count in tagset_counts.most_common(20)
    ]
    tagset_summary_by_chapter = {
        chapter: [
            {"tagset": tagset, "count": count}
            for tagset, count in counter.most_common(12)
        ]
        for chapter, counter in sorted(tagset_counts_by_chapter.items())
    }

    return {
        "file_count": len(entries),
        "target_count": total_refs,
        "target_chapter_counts": dict(sorted(target_chapter_counts.items())),
        "stream_marker_value_counts": marker_value_summary,
        "stream_tag_counts": tag_counts_summary,
        "frame_candidate_tagsets": tagset_summary,
        "frame_candidate_tagsets_by_chapter": tagset_summary_by_chapter,
        "entries": entries,
    }


def unpack_be_floats(data: bytes, count: int) -> list[float]:
    size = count * 4
    if len(data) < size:
        return []
    return list(struct.unpack(f">{count}f", data[:size]))


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def phong_exponent_to_roughness(exponent: float) -> float:
    exponent = max(0.0, float(exponent))
    return clamp01(math.sqrt(2.0 / (exponent + 2.0)))


def classify_hrc_payload(class_id: int, subtype_id: int) -> str:
    if class_id == 0 and subtype_id == 0:
        return "transform_node"
    if class_id == 4:
        return "mesh_like"
    if class_id == 6:
        return "spline_like"
    if class_id == 9:
        return "nurbs_like"
    if class_id == 10:
        return "surface_or_fx"
    if class_id == 2:
        return "face_or_billboard"
    return "unknown"


def parse_hrc_headers() -> dict:
    entries = []
    class_counts: Counter[str] = Counter()

    for path in sorted(MODEL_ROOT.rglob("*.hrc")):
        data = path.read_bytes()
        marker = data.find(b"HRCH")
        if marker == -1:
            continue

        name_end = data.find(b"\x00", marker)
        if name_end == -1:
            continue

        payload = data[name_end + 1 :]
        if len(payload) < 16:
            continue

        class_id = int.from_bytes(payload[0:2], "big")
        subtype_id = int.from_bytes(payload[2:4], "big")
        param_be = struct.unpack(">f", payload[4:8])[0]
        index_be = int.from_bytes(payload[8:12], "big")
        float_window = unpack_be_floats(payload, 12)
        kind = classify_hrc_payload(class_id, subtype_id)
        class_counts[kind] += 1

        entry = {
            "path": rel(path),
            "node_name": data[marker + 4 : name_end].decode("latin-1", errors="replace"),
            "class_id": class_id,
            "subtype_id": subtype_id,
            "param_be_float": round(param_be, 6),
            "index_be_uint": index_be,
            "payload_kind": kind,
            "payload_prefix_hex": payload[:16].hex(" "),
            "float_window_be": [round(value, 6) for value in float_window],
        }

        if kind == "transform_node" and len(float_window) >= 8:
            entry["transform_hint"] = {
                "scale_xyz": [round(value, 6) for value in float_window[1:4]],
                "rotation_xyz_hint": [round(value, 6) for value in float_window[4:7]],
                "translation_xyz_hint": [round(value, 6) for value in float_window[7:10]],
                "tail_hint": [round(value, 6) for value in float_window[10:12]],
                "notes": [
                    "field grouping is inferred from repeated transform_node samples",
                    "scale/rotation/translation layout matches multiple null and dummyroot cases",
                ],
            }

        entries.append(entry)

    return {
        "count": len(entries),
        "payload_kind_counts": dict(sorted(class_counts.items(), key=lambda item: (-item[1], item[0]))),
        "entries": entries,
    }


def parse_binary_materials() -> dict:
    entries = []
    shading_type_counts: Counter[int] = Counter()
    for path in sorted(MODEL_ROOT.rglob("*.mtr")):
        data = path.read_bytes()
        marker = data.find(b"MTRL")
        if marker == -1:
            continue

        name_end = data.find(b"\x00", marker)
        if name_end == -1:
            continue

        # Observed on multiple samples: the stable float window begins 13 bytes
        # after the terminating NUL of the MTRL name.
        start = name_end + 13
        if start + 48 > len(data):
            continue

        float_window = unpack_be_floats(data[start:], 12)
        shading_offset = name_end + 12 + (15 * 4)
        shading_type_hint = None
        if shading_offset + 4 <= len(data):
            shading_type_hint = int(round(struct.unpack(">f", data[shading_offset : shading_offset + 4])[0]))
            shading_type_counts[shading_type_hint] += 1

        decoded_fields = {}
        if len(float_window) >= 12:
            alpha_hint = 1.0
            if 0.0 <= float_window[11] <= 1.0:
                alpha_hint = clamp01(float_window[11])

            ior_hint = None
            if 1.0 < float_window[11] <= 4.0:
                ior_hint = round(float_window[11], 6)

            decoded_fields = {
                "diffuse_rgb": [round(clamp01(value), 6) for value in float_window[2:5]],
                "specular_rgb": [round(max(0.0, value), 6) for value in float_window[5:8]],
                "shininess": round(max(0.0, float_window[8]), 6),
                "roughness_hint": round(phong_exponent_to_roughness(float_window[8]), 6),
                "alpha_hint": round(alpha_hint, 6),
                "shading_type": shading_type_hint,
                "transparency_hint": round(float_window[9], 6),
                "reflection_hint": round(float_window[10], 6),
                "ior_hint": ior_hint,
                "prefix_color_hint_rg": [round(float_window[0], 6), round(float_window[1], 6)],
            }

        entries.append(
            {
                "path": rel(path),
                "material_name": data[marker + 4 : name_end].decode("latin-1", errors="replace"),
                "float_window_be": [round(value, 6) for value in float_window],
                "decoded_fields": decoded_fields,
                "likely_fields": {
                    "color_hint_a": [round(value, 6) for value in float_window[0:2]],
                    "color_hint_b": [round(value, 6) for value in float_window[2:5]],
                    "specular_hint": [round(value, 6) for value in float_window[5:8]],
                    "hardness_hint": round(float_window[8], 6),
                    "tail_hint": [round(value, 6) for value in float_window[9:12]],
                    "shading_type_hint": shading_type_hint,
                },
                "notes": [
                    "float_window_be is confirmed to be stable across samples",
                    "likely_fields are inferred from comparison with text XSI materials and are not yet authoritative",
                ],
            }
        )

    return {
        "count": len(entries),
        "shading_type_counts": dict(sorted(shading_type_counts.items())),
        "entries": entries,
    }


def summarize_animation_stream(data: bytes, offset: int) -> dict:
    tail = data[offset:]
    prefix = tail[:64]
    u16_prefix = []
    for index in range(0, min(len(prefix) // 2 * 2, 32), 2):
        u16_prefix.append(int.from_bytes(prefix[index : index + 2], "big"))

    float_hints = []
    seen_float_hints: set[float] = set()
    scan_limit = min(len(tail), 256)
    for index in range(0, max(0, scan_limit - 8), 2):
        if tail[index : index + 4] != b"\x00\x07\x00\x01":
            continue
        next_tag = int.from_bytes(tail[index + 8 : index + 10], "big")
        if next_tag not in {0, 1, 2, 3, 4, 5, 6, 7}:
            continue
        value = struct.unpack(">f", tail[index + 4 : index + 8])[0]
        rounded = round(value, 6)
        if not math.isfinite(value):
            continue
        if abs(value) < 0.01 or abs(value) > 10000.0:
            continue
        if rounded in seen_float_hints:
            continue
        seen_float_hints.add(rounded)
        float_hints.append(rounded)
        if len(float_hints) >= 12:
            break

    marker_counts: Counter[float] = Counter()
    marker_scan_limit = min(len(tail), 2048)
    for index in range(0, max(0, marker_scan_limit - 8)):
        if tail[index : index + 4] != b"\x00\x07\x00\x01":
            continue
        value = struct.unpack(">f", tail[index + 4 : index + 8])[0]
        if not math.isfinite(value):
            continue
        if abs(value) > 10000.0:
            continue
        marker_counts[round(value, 6)] += 1

    marker_candidates = [
        {"value": value, "count": count}
        for value, count in sorted(marker_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    tag_counts: Counter[int] = Counter()
    tag_samples: dict[int, list[float]] = {tag: [] for tag in range(1, 9)}
    for index in range(0, max(0, marker_scan_limit - 8)):
        tag = int.from_bytes(tail[index : index + 4], "big")
        if not 1 <= tag <= 8:
            continue
        value = struct.unpack(">f", tail[index + 4 : index + 8])[0]
        if not math.isfinite(value):
            continue
        if abs(value) > 1_000_000.0:
            continue
        tag_counts[tag] += 1
        samples = tag_samples[tag]
        if len(samples) < 8:
            samples.append(round(value, 6))

    tag_float_samples = {
        str(tag): {"count": tag_counts[tag], "samples": tag_samples[tag]}
        for tag in range(1, 9)
        if tag_counts[tag]
    }

    frame_candidates = []
    marker = b"\x00\x07\x00\x01"
    frame_scan_limit = min(len(tail), 4096)
    index = 0
    while index < max(0, frame_scan_limit - 8) and len(frame_candidates) < 3:
        if tail[index : index + 4] != marker:
            index += 1
            continue
        time_value = struct.unpack(">f", tail[index + 4 : index + 8])[0]
        if not math.isfinite(time_value) or abs(time_value) > 10000.0:
            index += 1
            continue

        channel_values: dict[int, float] = {}
        cursor = index + 8
        window_end = min(frame_scan_limit, index + 256)
        while cursor + 8 <= window_end:
            if tail[cursor : cursor + 4] == marker:
                break
            tag = int.from_bytes(tail[cursor : cursor + 4], "big")
            if 1 <= tag <= 8:
                value = struct.unpack(">f", tail[cursor + 4 : cursor + 8])[0]
                if math.isfinite(value) and abs(value) <= 1_000_000.0:
                    channel_values.setdefault(tag, round(value, 6))
            cursor += 1

        if channel_values:
            frame_candidates.append(
                {
                    "offset": index,
                    "time_hint": round(time_value, 6),
                    "channel_values": {str(tag): value for tag, value in sorted(channel_values.items())},
                }
            )
        index += 1

    return {
        "stream_byte_count": len(tail),
        "stream_prefix_hex": prefix.hex(" "),
        "stream_u16_prefix_be": u16_prefix,
        "plausible_be_float_hints": float_hints,
        "marker_0007_0001_float_counts": marker_candidates,
        "u32_tag_float_samples": tag_float_samples,
        "frame_candidates": frame_candidates,
    }


def extract_animation_frames(
    tail: bytes, *, max_frames: int = 60, scan_limit: int = 65536
) -> list[dict]:
    marker = b"\x00\x07\x00\x01"
    frames = []
    limit = min(len(tail), scan_limit)
    index = 0
    while index < max(0, limit - 8) and len(frames) < max_frames:
        pos = tail.find(marker, index, limit - 4)
        if pos == -1:
            break
        time_value = struct.unpack(">f", tail[pos + 4 : pos + 8])[0]
        if not math.isfinite(time_value) or abs(time_value) > 10000.0:
            index = pos + 1
            continue

        channel_values: dict[int, float] = {}
        window_end = min(limit, pos + 1024)
        cursor = pos + 8
        next_marker = tail.find(marker, cursor, window_end)
        if next_marker != -1:
            window_end = next_marker

        while cursor + 8 <= window_end:
            tag = int.from_bytes(tail[cursor : cursor + 4], "big")
            if 1 <= tag <= 8:
                value = struct.unpack(">f", tail[cursor + 4 : cursor + 8])[0]
                if math.isfinite(value) and abs(value) <= 1_000_000.0:
                    channel_values.setdefault(tag, round(value, 6))
            cursor += 1

        if channel_values:
            frames.append(
                {
                    "offset": pos,
                    "time_hint": round(time_value, 6),
                    "channel_values": {str(tag): value for tag, value in sorted(channel_values.items())},
                }
            )

        index = max(pos + 1, window_end)

    return frames


def probe_animation_streams(
    *,
    per_chapter_limit: int = 12,
    max_frames: int = 60,
    scan_limit: int = 65536,
) -> dict:
    chapter_groups: dict[str, list[tuple[int, Path, int, int]]] = defaultdict(list)
    for path in sorted(MODEL_ROOT.rglob("*.ani")):
        data = path.read_bytes()
        parsed = parse_animation_target_table(data)
        if not parsed:
            continue
        offset = parsed["target_stream_offset"]
        stream_len = max(0, len(data) - offset)
        chapter = parsed["target_chapter"]
        chapter_groups[chapter].append((stream_len, path, offset, len(parsed["targets"])))

    entries = []
    chapter_counts: Counter[str] = Counter()
    delta_counts: Counter[float] = Counter()
    tagset_counts: Counter[str] = Counter()

    for chapter, items in sorted(chapter_groups.items()):
        items.sort(key=lambda item: (item[0], str(item[1])))
        for stream_len, path, offset, target_count in items[:per_chapter_limit]:
            data = path.read_bytes()
            tail = data[offset:]
            frames = extract_animation_frames(tail, max_frames=max_frames, scan_limit=scan_limit)
            time_values = [frame["time_hint"] for frame in frames]
            delta_counts_local: Counter[float] = Counter()
            for prev, curr in zip(time_values, time_values[1:]):
                delta = round(curr - prev, 6)
                delta_counts_local[delta] += 1
                delta_counts[delta] += 1

            tagset_counts_local: Counter[str] = Counter()
            for frame in frames:
                tagset = ",".join(frame["channel_values"].keys())
                if tagset:
                    tagset_counts_local[tagset] += 1
                    tagset_counts[tagset] += 1

            entries.append(
                {
                    "path": rel(path),
                    "target_chapter": chapter,
                    "target_count": target_count,
                    "stream_byte_count": stream_len,
                    "frame_count": len(frames),
                    "frame_time_values": time_values[:12],
                    "frame_time_delta_counts": [
                        {"delta": delta, "count": count}
                        for delta, count in delta_counts_local.most_common(8)
                    ],
                    "frame_tagset_counts": [
                        {"tagset": tagset, "count": count}
                        for tagset, count in tagset_counts_local.most_common(8)
                    ],
                    "frame_samples": frames[:6],
                }
            )
            chapter_counts[chapter] += 1

    return {
        "per_chapter_limit": per_chapter_limit,
        "max_frames": max_frames,
        "scan_limit": scan_limit,
        "file_count": len(entries),
        "chapter_counts": dict(sorted(chapter_counts.items())),
        "frame_time_delta_counts": [
            {"delta": delta, "count": count}
            for delta, count in delta_counts.most_common(20)
        ],
        "frame_tagset_counts": [
            {"tagset": tagset, "count": count}
            for tagset, count in tagset_counts.most_common(20)
        ],
        "entries": entries,
    }


def probe_camera_animation_links(
    *,
    max_frames: int = 120,
    scan_limit: int = 65536,
    max_entries: int | None = None,
) -> dict:
    cameras = load_cached_report("binary_cameras.json")
    if cameras is None:
        cameras = parse_binary_cameras()
    camera_by_name = {Path(entry["path"]).stem: entry for entry in cameras.get("entries", [])}

    field_labels = [
        "position_xyz",
        "rotation_xyz_hint",
        "clip_near",
        "clip_far",
        "yfov_radians",
        "focal_length_hint",
        "f_stop_hint",
        "focus_distance_hint",
    ]

    def gather_field_values(entry: dict) -> dict[str, list[float]]:
        values: dict[str, list[float]] = {}
        for label in field_labels:
            raw = entry.get(label)
            if raw is None:
                continue
            if isinstance(raw, list):
                values[label] = [float(value) for value in raw]
            else:
                values[label] = [float(raw)]
        return values

    def match_field_candidates(tag_values: list[float], field_values: dict[str, list[float]]) -> list[dict]:
        matches = []
        if not tag_values:
            return matches
        for label, values in field_values.items():
            best = None
            for tag_value in tag_values:
                for field_value in values:
                    delta = abs(tag_value - field_value)
                    threshold = max(0.05, abs(field_value) * 0.02)
                    if delta <= threshold and (best is None or delta < best["delta"]):
                        best = {
                            "field": label,
                            "field_value": round(field_value, 6),
                            "tag_value": round(tag_value, 6),
                            "delta": round(delta, 6),
                        }
            if best is not None:
                matches.append(best)
        return matches

    entries = []
    match_counts: Counter[str] = Counter()
    ani_paths = sorted(MODEL_ROOT.rglob("*.ani"))
    if max_entries is not None:
        ani_paths = ani_paths[: max_entries]

    for path in ani_paths:
        data = path.read_bytes()
        parsed = parse_animation_target_table(data)
        if not parsed or parsed["target_chapter"] != "CAMERAS":
            continue
        offset = parsed["target_stream_offset"]
        tail = data[offset:]
        frames = extract_animation_frames(tail, max_frames=max_frames, scan_limit=scan_limit)
        if not frames:
            continue
        time_values = [frame["time_hint"] for frame in frames]

        tag_values: dict[str, list[float]] = defaultdict(list)
        for frame in frames:
            for tag, value in frame["channel_values"].items():
                tag_values[tag].append(float(value))

        tag_stats = {}
        for tag, values in sorted(tag_values.items(), key=lambda item: int(item[0])):
            tag_stats[tag] = {
                "count": len(values),
                "min": round(min(values), 6),
                "max": round(max(values), 6),
                "mean": round(sum(values) / len(values), 6),
                "samples": [round(value, 6) for value in values[:6]],
            }

        target_names = [target["name"] for target in parsed["targets"]]
        for target_name in target_names:
            cam_entry = camera_by_name.get(target_name)
            if cam_entry is None:
                continue
            field_values = gather_field_values(cam_entry)
            tag_matches = []
            for tag, values in tag_values.items():
                matches = match_field_candidates(values, field_values)
                if matches:
                    for match in matches:
                        match_counts[f"tag_{tag}->{match['field']}"] += 1
                    tag_matches.append({"tag": tag, "matches": matches})

            entries.append(
                {
                    "ani_path": rel(path),
                    "camera_target": target_name,
                    "camera_path": cam_entry.get("path"),
                    "frame_count": len(frames),
                    "time_values_head": time_values[:12],
                    "tag_stats": tag_stats,
                    "tag_matches": tag_matches,
                }
            )

    match_summary = [
        {"mapping": mapping, "count": count}
        for mapping, count in match_counts.most_common(25)
    ]

    return {
        "max_frames": max_frames,
        "scan_limit": scan_limit,
        "entry_count": len(entries),
        "match_summary": match_summary,
        "entries": entries,
    }


def read_f32_be_at(data: bytes, offset: int) -> float | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack(">f", data[offset : offset + 4])[0]


def read_vec3_be_at(data: bytes, offset: int) -> list[float] | None:
    if offset < 0 or offset + 12 > len(data):
        return None
    return [round(value, 6) for value in struct.unpack(">fff", data[offset : offset + 12])]


def find_marker_payload(data: bytes, marker: bytes, size: int, *, start: int = 88) -> bytes | None:
    pos = data.find(marker, start)
    if pos == -1:
        return None
    payload_start = pos + len(marker)
    payload_end = payload_start + size
    if payload_end > len(data):
        return None
    return data[payload_start:payload_end]


def parse_binary_cameras() -> dict:
    entries = []
    for path in sorted(MODEL_ROOT.rglob("*.cam")):
        data = path.read_bytes()

        position_raw = find_marker_payload(data, b"\x00\x03\x00\x01", 12)
        rotation_raw = find_marker_payload(data, b"\x00\x04\x00\x01", 12)
        clip_near_raw = find_marker_payload(data, b"\x00\x07\x00\x01", 4)
        clip_far_raw = find_marker_payload(data, b"\x00\x08\x00\x01", 4)
        focal_length_raw = find_marker_payload(data, b"\x00\x09\x00\x01", 4)
        f_stop_raw = find_marker_payload(data, b"\x00\x0A\x00\x01", 4)
        focus_distance_raw = find_marker_payload(data, b"\x00\x0B\x00\x01", 4)
        yfov_raw = find_marker_payload(data, b"\x00\x0C\x00\x01", 4)

        position = [round(v, 6) for v in struct.unpack(">fff", position_raw)] if position_raw else None
        rotation_xyz = [round(v, 6) for v in struct.unpack(">fff", rotation_raw)] if rotation_raw else None
        clip_near = round(struct.unpack(">f", clip_near_raw)[0], 6) if clip_near_raw else None
        clip_far = round(struct.unpack(">f", clip_far_raw)[0], 6) if clip_far_raw else None
        focal_length = round(struct.unpack(">f", focal_length_raw)[0], 6) if focal_length_raw else None
        f_stop = round(struct.unpack(">f", f_stop_raw)[0], 6) if f_stop_raw else None
        focus_distance = round(struct.unpack(">f", focus_distance_raw)[0], 6) if focus_distance_raw else None
        yfov_radians = round(struct.unpack(">f", yfov_raw)[0], 6) if yfov_raw else None

        rotation_degrees = None
        rotation_quaternion = None
        if rotation_xyz is not None:
            rotation_degrees = any(abs(value) > (math.tau * 1.25) for value in rotation_xyz)
            rotation_quaternion = quaternion_from_euler_xyz(tuple(rotation_xyz), degrees=rotation_degrees)

        entries.append(
            {
                "path": rel(path),
                "position_xyz": position,
                "rotation_xyz_hint": rotation_xyz,
                "rotation_unit_hint": "degrees" if rotation_degrees else "radians",
                "rotation_quaternion_hint": [round(value, 6) for value in rotation_quaternion] if rotation_quaternion else None,
                "clip_near": clip_near,
                "clip_far": clip_far,
                "yfov_radians": yfov_radians,
                "focal_length_hint": focal_length,
                "f_stop_hint": f_stop,
                "focus_distance_hint": focus_distance,
                "notes": [
                    "position_xyz and rotation_xyz_hint come from stable tagged fields 3 and 4",
                    "rotation_unit_hint is inferred heuristically from observed value ranges",
                    "yfov_radians is taken from stable tagged field 12 and maps cleanly to common camera FOV values",
                ],
            }
        )

    return {"count": len(entries), "entries": entries}


def parse_binary_lights() -> dict:
    entries = []
    kind_counts: Counter[str] = Counter()

    for path in sorted(MODEL_ROOT.rglob("*.lig")):
        data = path.read_bytes()
        name = None
        name_marker = data.find(b"\x00\x01", 88)
        if name_marker != -1:
            name_end = data.find(b"\x00\x00\x02", name_marker + 2)
            if name_end != -1:
                name = data[name_marker + 2 : name_end].decode("latin-1", errors="replace")

        color_raw = find_marker_payload(data, b"\x00\x03\x00\x01", 12)
        intensity_raw = find_marker_payload(data, b"\x00\x04\x00\x01", 4)
        range_raw = find_marker_payload(data, b"\x00\x05\x00\x01", 4)
        cone_scale_raw = find_marker_payload(data, b"\x00\x06\x00\x01", 4)
        position_raw = find_marker_payload(data, b"\x00\x07\x00\x01", 12)
        direction_raw = find_marker_payload(data, b"\x00\x08\x00\x01", 12)
        inner_cone_raw = find_marker_payload(data, b"\x00\x09\x00\x01", 4)
        outer_cone_raw = find_marker_payload(data, b"\x00\x0A\x00\x01", 4)

        color = [round(v, 6) for v in struct.unpack(">fff", color_raw)] if color_raw else None
        intensity = round(struct.unpack(">f", intensity_raw)[0], 6) if intensity_raw else None
        range_hint = round(struct.unpack(">f", range_raw)[0], 6) if range_raw else None
        cone_scale = round(struct.unpack(">f", cone_scale_raw)[0], 6) if cone_scale_raw else None
        position = [round(v, 6) for v in struct.unpack(">fff", position_raw)] if position_raw else None
        direction = [round(v, 6) for v in struct.unpack(">fff", direction_raw)] if direction_raw else None
        inner_cone = round(struct.unpack(">f", inner_cone_raw)[0], 6) if inner_cone_raw else None
        outer_cone = round(struct.unpack(">f", outer_cone_raw)[0], 6) if outer_cone_raw else None

        light_kind = "point"
        if name and "sun" in name.lower():
            light_kind = "directional"
        elif (
            (name and "spot" in name.lower())
            or (inner_cone is not None)
            or (outer_cone is not None)
            or (direction and any(abs(value) > 1e-6 for value in direction))
        ):
            light_kind = "spot"
        kind_counts[light_kind] += 1

        rotation_quaternion = None
        if direction and any(abs(value) > 1e-6 for value in direction):
            rotation_quaternion = quaternion_from_two_vectors((0.0, 0.0, -1.0), tuple(direction))

        entries.append(
            {
                "path": rel(path),
                "light_name": name,
                "light_kind_hint": light_kind,
                "color_rgb": color,
                "intensity_hint": intensity,
                "range_hint": range_hint,
                "cone_scale_hint": cone_scale,
                "position_xyz": position,
                "direction_xyz_hint": direction,
                "rotation_quaternion_hint": [round(value, 6) for value in rotation_quaternion] if rotation_quaternion else None,
                "spot_inner_cone_angle": inner_cone,
                "spot_outer_cone_angle": outer_cone,
                "notes": [
                    "color, position, and direction come from stable tagged fields in the light object",
                    "light_kind_hint is inferred from the light name and whether a direction vector is present",
                    "range_hint is omitted from glTF export when the source value looks like an infinite/default sentinel",
                ],
            }
        )

    return {
        "count": len(entries),
        "light_kind_counts": dict(sorted(kind_counts.items())),
        "entries": entries,
    }


def analyze_nurbs_usage() -> dict:
    hrc_report = load_cached_report("hrc_headers.json")
    if hrc_report is None:
        hrc_report = parse_hrc_headers()

    scene_report = load_cached_report("scene_dependencies.json")
    if scene_report is None:
        scene_report = build_scene_dependencies()

    hrc_lookup = {entry["path"]: entry for entry in hrc_report.get("entries", []) if entry.get("path")}
    payload_counts: Counter[str] = Counter()
    scene_counts: Counter[str] = Counter()
    scenes_with_nurbs = 0
    scene_entries = []

    for scene in scene_report.get("entries", []):
        scene_payloads: Counter[str] = Counter()
        nurbs_entries = []
        related_entries = []
        for chapter in scene.get("chapters", []):
            if chapter.get("chapter") != "MODELS":
                continue
            for item in chapter.get("entries", []):
                resolved_path = item.get("resolved_path")
                if not resolved_path or resolved_path not in hrc_lookup:
                    continue
                header = hrc_lookup[resolved_path]
                payload_kind = header.get("payload_kind")
                if payload_kind not in {"nurbs_like", "surface_or_fx", "spline_like"}:
                    continue
                payload_counts[payload_kind] += 1
                scene_payloads[payload_kind] += 1
                info = {
                    "model_name": item.get("name"),
                    "resolved_path": resolved_path,
                    "payload_kind": payload_kind,
                    "class_id": header.get("class_id"),
                    "subtype_id": header.get("subtype_id"),
                    "node_name": header.get("node_name"),
                }
                related_entries.append(info)
                if payload_kind == "nurbs_like":
                    nurbs_entries.append(info)

        if not related_entries:
            continue
        if nurbs_entries:
            scenes_with_nurbs += 1
        for kind, count in scene_payloads.items():
            scene_counts[kind] += 1
        scene_entries.append(
            {
                "path": scene["path"],
                "nurbs_model_count": len(nurbs_entries),
                "related_model_count": len(related_entries),
                "payload_kind_counts": dict(sorted(scene_payloads.items())),
                "nurbs_models": nurbs_entries,
                "related_models": related_entries,
            }
        )

    return {
        "scene_count_with_related_models": len(scene_entries),
        "scene_count_with_nurbs_models": scenes_with_nurbs,
        "payload_kind_counts": dict(sorted(payload_counts.items())),
        "scene_payload_kind_counts": dict(sorted(scene_counts.items())),
        "entries": scene_entries,
    }


def vertex_bounds(vertices: list[tuple[float, float, float]]) -> dict[str, list[float]] | None:
    if not vertices:
        return None
    xs = [vertex[0] for vertex in vertices]
    ys = [vertex[1] for vertex in vertices]
    zs = [vertex[2] for vertex in vertices]
    return {
        "min": [round(min(xs), 6), round(min(ys), 6), round(min(zs), 6)],
        "max": [round(max(xs), 6), round(max(ys), 6), round(max(zs), 6)],
    }


def write_obj(
    path: Path,
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int]],
    face_texcoords: list[list[tuple[float, float]]] | None = None,
    face_normals: list[list[tuple[float, float, float]]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for x, y, z in vertices:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")

    vt_indices: list[tuple[int, int, int]] = []
    vn_indices: list[tuple[int, int, int]] = []
    next_vt = 1
    next_vn = 1

    if face_texcoords:
        for triangle_uvs in face_texcoords:
            tri_indices = []
            for u, v in triangle_uvs:
                lines.append(f"vt {u:.6f} {v:.6f}")
                tri_indices.append(next_vt)
                next_vt += 1
            vt_indices.append(tuple(tri_indices))

    if face_normals:
        for triangle_normals in face_normals:
            tri_indices = []
            for nx, ny, nz in triangle_normals:
                lines.append(f"vn {nx:.6f} {ny:.6f} {nz:.6f}")
                tri_indices.append(next_vn)
                next_vn += 1
            vn_indices.append(tuple(tri_indices))

    for face_index, (a, b, c) in enumerate(faces):
        if face_texcoords and face_normals and face_index < len(vt_indices) and face_index < len(vn_indices):
            ta, tb, tc = vt_indices[face_index]
            na, nb, nc = vn_indices[face_index]
            lines.append(f"f {a + 1}/{ta}/{na} {b + 1}/{tb}/{nb} {c + 1}/{tc}/{nc}")
        elif face_texcoords and face_index < len(vt_indices):
            ta, tb, tc = vt_indices[face_index]
            lines.append(f"f {a + 1}/{ta} {b + 1}/{tb} {c + 1}/{tc}")
        else:
            lines.append(f"f {a + 1} {b + 1} {c + 1}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_counted_polygons(payload: bytes, vertex_count: int, start: int) -> tuple[int, list[dict], int]:
    if start + 4 > len(payload):
        return 0, [], start

    polygon_count = int.from_bytes(payload[start : start + 4], "big")
    if polygon_count <= 0:
        return 0, [], start

    polygons = []
    offset = start + 4

    for polygon_index in range(polygon_count):
        if offset + 2 > len(payload):
            return polygon_count, polygons, offset

        corner_count = int.from_bytes(payload[offset : offset + 2], "big")
        if corner_count < 3 or corner_count > MAX_POLYGON_CORNERS:
            return polygon_count, polygons, offset

        rec_offset = offset + 2
        corners = []
        for _ in range(corner_count):
            rec = payload[rec_offset : rec_offset + 28]
            if len(rec) < 28:
                return polygon_count, polygons, rec_offset

            vertex_index = int.from_bytes(rec[0:4], "big")
            if vertex_index >= vertex_count:
                return polygon_count, polygons, rec_offset

            nx, ny, nz = struct.unpack(">fff", rec[4:16])
            magnitude = math.sqrt(nx * nx + ny * ny + nz * nz)
            if not math.isfinite(magnitude) or magnitude > 1.25:
                return polygon_count, polygons, rec_offset

            u, v = struct.unpack(">ff", rec[16:24])
            color_bytes = list(rec[24:28])
            corners.append(
                {
                    "vertex_index": vertex_index,
                    "normal": [round(nx, 6), round(ny, 6), round(nz, 6)],
                    "uv": [round(u, 6), round(v, 6)],
                    "color_bytes": color_bytes,
                    "color_u32_be": int.from_bytes(rec[24:28], "big"),
                }
            )
            rec_offset += 28

        if rec_offset + 4 > len(payload):
            return polygon_count, polygons, rec_offset

        meta_raw = payload[rec_offset : rec_offset + 4]
        polygons.append(
            {
                "polygon_index": polygon_index,
                "corner_count": corner_count,
                "corners": corners,
                "polygon_meta_u32": int.from_bytes(meta_raw, "big"),
                "polygon_meta_words_be": [
                    int.from_bytes(meta_raw[0:2], "big"),
                    int.from_bytes(meta_raw[2:4], "big"),
                ],
            }
        )
        offset = rec_offset + 4

    return polygon_count, polygons, offset


def parse_polygon_groups(payload: bytes, vertex_count: int, start: int) -> tuple[list[dict], int]:
    groups = []
    offset = start

    while offset + 6 <= len(payload):
        group_id = int.from_bytes(payload[offset : offset + 4], "big")
        corner_count = int.from_bytes(payload[offset + 4 : offset + 6], "big")
        if corner_count < 3 or corner_count > MAX_POLYGON_CORNERS:
            break

        rec_offset = offset + 6
        corners = []
        valid = True
        for _ in range(corner_count):
            rec = payload[rec_offset : rec_offset + 28]
            if len(rec) < 28:
                valid = False
                break

            vertex_index = int.from_bytes(rec[0:4], "big")
            if vertex_index >= vertex_count:
                valid = False
                break

            nx, ny, nz = struct.unpack(">fff", rec[4:16])
            u, v = struct.unpack(">ff", rec[16:24])
            color = int.from_bytes(rec[24:28], "big")
            magnitude = math.sqrt(nx * nx + ny * ny + nz * nz)
            if not math.isfinite(magnitude) or magnitude > 1.25:
                valid = False
                break

            corners.append(
                {
                    "vertex_index": vertex_index,
                    "normal": [round(nx, 6), round(ny, 6), round(nz, 6)],
                    "uv": [round(u, 6), round(v, 6)],
                    "color": color,
                }
            )
            rec_offset += 28

        if not valid:
            break

        groups.append({"group_id": group_id, "corner_count": corner_count, "corners": corners})
        offset = rec_offset

    return groups, offset


def triangulate_group(corners: list[dict]) -> list[tuple[int, int, int]]:
    indices = [corner["vertex_index"] for corner in corners]
    if len(indices) < 3:
        return []
    if len(indices) == 3:
        return [tuple(indices)]

    faces = []
    root = indices[0]
    for i in range(1, len(indices) - 1):
        faces.append((root, indices[i], indices[i + 1]))
    return faces


def triangulate_polygon_corners(corners: list[dict]) -> list[tuple[dict, dict, dict]]:
    if len(corners) < 3:
        return []
    if len(corners) == 3:
        return [(corners[0], corners[1], corners[2])]

    triangles = []
    root = corners[0]
    for index in range(1, len(corners) - 1):
        triangles.append((root, corners[index], corners[index + 1]))
    return triangles


def parse_mesh_like_hrc() -> dict:
    entries = []
    decoded_mesh_count = 0

    for path in sorted(MODEL_ROOT.rglob("*.hrc")):
        data = path.read_bytes()
        marker = data.find(b"HRCH")
        if marker == -1:
            continue

        name_end = data.find(b"\x00", marker)
        if name_end == -1:
            continue

        payload = data[name_end + 1 :]
        if len(payload) < 12:
            continue

        class_id = int.from_bytes(payload[0:2], "big")
        subtype_id = int.from_bytes(payload[2:4], "big")
        if class_id != 4:
            continue

        param_be = struct.unpack(">f", payload[4:8])[0]
        vertex_count = int.from_bytes(payload[8:12], "big")
        vertex_bytes = vertex_count * 14
        if vertex_count <= 0 or 12 + vertex_bytes > len(payload):
            continue

        vertices = []
        tails = []
        valid = True
        for index in range(vertex_count):
            rec = payload[12 + (index * 14) : 12 + ((index + 1) * 14)]
            if len(rec) < 14:
                valid = False
                break
            vertices.append(struct.unpack(">fff", rec[:12]))
            tails.append(int.from_bytes(rec[12:14], "big"))

        if not valid:
            continue

        entry = {
            "path": rel(path),
            "node_name": data[marker + 4 : name_end].decode("latin-1", errors="replace"),
            "subtype_id": subtype_id,
            "param_be_float": round(param_be, 6),
            "vertex_count": vertex_count,
            "vertex_tail_values": sorted(set(tails)),
            "bounds": vertex_bounds(vertices),
            "vertex_preview": [[round(x, 6), round(y, 6), round(z, 6)] for x, y, z in vertices[:8]],
        }

        polygon_count, polygons, polygon_end = parse_counted_polygons(payload, vertex_count, 12 + vertex_bytes)
        if polygons and len(polygons) == polygon_count:
            entry["polygon_count_declared"] = polygon_count
            entry["polygon_group_count"] = len(polygons)
            entry["polygon_groups_preview"] = [
                {
                    "polygon_index": polygon["polygon_index"],
                    "corner_count": polygon["corner_count"],
                    "vertex_indices": [corner["vertex_index"] for corner in polygon["corners"]],
                    "uv_preview": [corner["uv"] for corner in polygon["corners"][:4]],
                    "color_values": sorted({corner["color_u32_be"] for corner in polygon["corners"]}),
                    "polygon_meta_u32": polygon["polygon_meta_u32"],
                    "polygon_meta_words_be": polygon["polygon_meta_words_be"],
                }
                for polygon in polygons[:12]
            ]

            faces = []
            face_texcoords = []
            face_normals = []
            for polygon in polygons:
                triangles = triangulate_polygon_corners(polygon["corners"])
                for tri in triangles:
                    faces.append(tuple(corner["vertex_index"] for corner in tri))
                    face_texcoords.append([tuple(corner["uv"]) for corner in tri])
                    face_normals.append([tuple(corner["normal"]) for corner in tri])

            if faces:
                obj_path = EXTRACT_DIR / "hrc_mesh_like" / path.relative_to(MODEL_ROOT).with_suffix(".obj")
                write_obj(obj_path, vertices, faces, face_texcoords=face_texcoords, face_normals=face_normals)
                entry["decoded_vertices"] = [[round(x, 6), round(y, 6), round(z, 6)] for x, y, z in vertices]
                entry["decoded_faces"] = [list(face) for face in faces]
                entry["decoded_obj"] = rel(obj_path)
                entry["decoded_polygon_bytes_end"] = polygon_end
                decoded_mesh_count += 1
        else:
            polygon_groups, polygon_end = parse_polygon_groups(payload, vertex_count, 12 + vertex_bytes)
            if polygon_groups:
                entry["polygon_group_count"] = len(polygon_groups)
                entry["polygon_groups_preview"] = [
                    {
                        "group_id": group["group_id"],
                        "corner_count": group["corner_count"],
                        "vertex_indices": [corner["vertex_index"] for corner in group["corners"]],
                        "uv_preview": [corner["uv"] for corner in group["corners"][:4]],
                        "color_values": sorted({corner["color"] for corner in group["corners"]}),
                    }
                    for group in polygon_groups[:12]
                ]

                faces = []
                face_texcoords = []
                face_normals = []
                for group in polygon_groups:
                    triangles = triangulate_polygon_corners(group["corners"])
                    for tri in triangles:
                        faces.append(tuple(corner["vertex_index"] for corner in tri))
                        face_texcoords.append([tuple(corner["uv"]) for corner in tri])
                        face_normals.append([tuple(corner["normal"]) for corner in tri])

                if faces:
                    obj_path = EXTRACT_DIR / "hrc_mesh_like" / path.relative_to(MODEL_ROOT).with_suffix(".obj")
                    write_obj(obj_path, vertices, faces, face_texcoords=face_texcoords, face_normals=face_normals)
                    entry["decoded_vertices"] = [[round(x, 6), round(y, 6), round(z, 6)] for x, y, z in vertices]
                    entry["decoded_faces"] = [list(face) for face in faces]
                    entry["decoded_obj"] = rel(obj_path)
                    entry["decoded_polygon_bytes_end"] = polygon_end
                    decoded_mesh_count += 1

        entries.append(entry)

    return {
        "count": len(entries),
        "decoded_simple_mesh_count": decoded_mesh_count,
        "entries": entries,
    }


def parse_obj_geometry(path: Path) -> dict:
    vertices: list[tuple[float, float, float]] = []
    texcoords: list[tuple[float, float]] = []
    normals: list[tuple[float, float, float]] = []
    faces: list[list[tuple[int, int | None, int | None]]] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("v "):
            _, x, y, z = line.split()
            vertices.append((float(x), float(y), float(z)))
            continue
        if line.startswith("vt "):
            _, u, v = line.split()[:3]
            texcoords.append((float(u), float(v)))
            continue
        if line.startswith("vn "):
            _, nx, ny, nz = line.split()
            normals.append((float(nx), float(ny), float(nz)))
            continue
        if not line.startswith("f "):
            continue

        refs = []
        for token in line[2:].split():
            parts = token.split("/")
            v_index = int(parts[0])
            vt_index = int(parts[1]) if len(parts) >= 2 and parts[1] else None
            vn_index = int(parts[2]) if len(parts) >= 3 and parts[2] else None
            refs.append((v_index, vt_index, vn_index))
        faces.append(refs)

    return {
        "vertices": vertices,
        "texcoords": texcoords,
        "normals": normals,
        "faces": faces,
    }


def connected_obj_components(geometry: dict) -> list[dict]:
    faces = geometry["faces"]
    if not faces:
        return []

    vertex_to_faces: dict[int, list[int]] = {}
    for face_index, refs in enumerate(faces):
        for vertex_index, _, _ in refs:
            vertex_to_faces.setdefault(vertex_index, []).append(face_index)

    adjacency = [set() for _ in faces]
    for linked_faces in vertex_to_faces.values():
        linked = set(linked_faces)
        for face_index in linked:
            adjacency[face_index].update(linked)

    components: list[list[int]] = []
    seen: set[int] = set()
    for face_index in range(len(faces)):
        if face_index in seen:
            continue
        stack = [face_index]
        seen.add(face_index)
        component = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(component)

    extracted = []
    for face_indices in sorted(components, key=len, reverse=True):
        used_vertices = sorted({refs[0] for face_index in face_indices for refs in faces[face_index]})
        used_texcoords = sorted(
            {
                refs[1]
                for face_index in face_indices
                for refs in faces[face_index]
                if refs[1] is not None
            }
        )
        used_normals = sorted(
            {
                refs[2]
                for face_index in face_indices
                for refs in faces[face_index]
                if refs[2] is not None
            }
        )

        vertex_map = {old_index: new_index for new_index, old_index in enumerate(used_vertices, start=1)}
        texcoord_map = {old_index: new_index for new_index, old_index in enumerate(used_texcoords, start=1)}
        normal_map = {old_index: new_index for new_index, old_index in enumerate(used_normals, start=1)}

        extracted_faces = []
        for face_index in face_indices:
            face = []
            for vertex_index, texcoord_index, normal_index in faces[face_index]:
                face.append(
                    (
                        vertex_map[vertex_index],
                        texcoord_map.get(texcoord_index) if texcoord_index is not None else None,
                        normal_map.get(normal_index) if normal_index is not None else None,
                    )
                )
            extracted_faces.append(face)

        extracted.append(
            {
                "vertices": [geometry["vertices"][index - 1] for index in used_vertices],
                "texcoords": [geometry["texcoords"][index - 1] for index in used_texcoords],
                "normals": [geometry["normals"][index - 1] for index in used_normals],
                "faces": extracted_faces,
            }
        )

    return extracted


def face_tokens_with_offsets(
    refs: list[tuple[int, int | None, int | None]],
    *,
    vertex_offset: int,
    texcoord_offset: int,
    normal_offset: int,
) -> list[str]:
    tokens = []
    for v_index, vt_index, vn_index in refs:
        v_out = v_index + vertex_offset
        vt_out = vt_index + texcoord_offset if vt_index is not None else None
        vn_out = vn_index + normal_offset if vn_index is not None else None
        if vt_out is not None and vn_out is not None:
            tokens.append(f"{v_out}/{vt_out}/{vn_out}")
        elif vt_out is not None:
            tokens.append(f"{v_out}/{vt_out}")
        elif vn_out is not None:
            tokens.append(f"{v_out}//{vn_out}")
        else:
            tokens.append(str(v_out))
    return tokens


def triangulate_obj_face_refs(
    refs: list[tuple[int, int | None, int | None]],
) -> list[tuple[tuple[int, int | None, int | None], tuple[int, int | None, int | None], tuple[int, int | None, int | None]]]:
    if len(refs) < 3:
        return []
    if len(refs) == 3:
        return [(refs[0], refs[1], refs[2])]

    triangles = []
    root = refs[0]
    for index in range(1, len(refs) - 1):
        triangles.append((root, refs[index], refs[index + 1]))
    return triangles


def write_gltf_scene_bundle(
    gltf_path: Path,
    gltf_bin_path: Path,
    objects: list[dict],
    materials: dict[str, dict],
    *,
    cameras: list[dict] | None = None,
    lights: list[dict] | None = None,
    extra_nodes: list[dict] | None = None,
    node_defs: list[dict] | None = None,
) -> None:
    cameras = cameras or []
    lights = lights or []
    extra_nodes = extra_nodes or []
    node_defs = node_defs or []
    images = []
    textures = []
    gltf_materials = []
    image_index_by_uri: dict[str, int] = {}
    texture_index_by_uri: dict[str, int] = {}
    material_index_by_name: dict[str, int] = {}

    for material_name, material in materials.items():
        diffuse = material.get("diffuse") or [1.0, 1.0, 1.0]
        alpha = float(material.get("alpha", 1.0))
        gltf_material = {
            "name": material_name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [
                    float(diffuse[0]),
                    float(diffuse[1]),
                    float(diffuse[2]),
                    alpha,
                ],
                "metallicFactor": 0.0,
                "roughnessFactor": float(material.get("roughness_factor", 1.0)),
            },
            "doubleSided": True,
        }
        if alpha < 0.999:
            gltf_material["alphaMode"] = "BLEND"

        texture_uri = material.get("texture_uri")
        if texture_uri:
            if texture_uri not in image_index_by_uri:
                image_index_by_uri[texture_uri] = len(images)
                images.append({"uri": texture_uri})
            if texture_uri not in texture_index_by_uri:
                texture_index_by_uri[texture_uri] = len(textures)
                textures.append({"source": image_index_by_uri[texture_uri]})
            gltf_material["pbrMetallicRoughness"]["baseColorTexture"] = {
                "index": texture_index_by_uri[texture_uri]
            }

        material_index_by_name[material_name] = len(gltf_materials)
        gltf_materials.append(gltf_material)

    buffer = bytearray()
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    meshes = []
    nodes = []
    node_index_by_name: dict[str, int] = {}
    pending_parent_links: list[tuple[int, str]] = []
    gltf_cameras = []
    gltf_lights = []

    def add_node(
        name: str,
        *,
        matrix: list[float] | None = None,
        extras: dict | None = None,
        parent_name: str | None = None,
    ) -> int:
        if name in node_index_by_name:
            index = node_index_by_name[name]
            node = nodes[index]
            if matrix is not None and "matrix" not in node:
                node["matrix"] = matrix
            if extras:
                node.setdefault("extras", {}).update(extras)
            if parent_name:
                pending_parent_links.append((index, parent_name))
            return index

        node = {"name": name}
        if matrix is not None:
            node["matrix"] = matrix
        if extras:
            node["extras"] = extras
        index = len(nodes)
        nodes.append(node)
        node_index_by_name[name] = index
        if parent_name:
            pending_parent_links.append((index, parent_name))
        return index

    for node_def in node_defs:
        add_node(
            node_def["name"],
            matrix=node_def.get("matrix"),
            extras=node_def.get("extras"),
            parent_name=node_def.get("parent_name"),
        )

    for obj in objects:
        geometry = obj["geometry"]
        vertices = geometry["vertices"]
        faces = geometry["faces"]
        if not vertices or not faces:
            continue

        primitive_vertices: list[
            tuple[
                tuple[float, float, float],
                tuple[float, float] | None,
                tuple[float, float, float] | None,
            ]
        ] = []
        primitive_vertex_map: dict[
            tuple[
                tuple[float, float, float],
                tuple[float, float] | None,
                tuple[float, float, float] | None,
            ],
            int,
        ] = {}
        indices = []
        texcoords = geometry.get("texcoords") or []
        normals = geometry.get("normals") or []
        for refs in faces:
            for tri in triangulate_obj_face_refs(refs):
                for vertex_index, texcoord_index, normal_index in tri:
                    pos = tuple(vertices[vertex_index - 1])
                    uv = tuple(texcoords[texcoord_index - 1]) if texcoord_index is not None else None
                    normal = tuple(normals[normal_index - 1]) if normal_index is not None else None
                    key = (pos, uv, normal)
                    if key not in primitive_vertex_map:
                        primitive_vertex_map[key] = len(primitive_vertices)
                        primitive_vertices.append(key)
                    indices.append(primitive_vertex_map[key])

        if not indices or not primitive_vertices:
            continue

        position_values = [pos for pos, _, _ in primitive_vertices]
        positions = [coord for vertex in position_values for coord in vertex]
        pos_min = [min(vertex[i] for vertex in position_values) for i in range(3)]
        pos_max = [max(vertex[i] for vertex in position_values) for i in range(3)]
        attributes = {
            "POSITION": gltf_accessor(
                accessors,
                buffer_views,
                buffer,
                pack_floats(positions),
                5126,
                "VEC3",
                len(primitive_vertices),
                target=34962,
                minimum=pos_min,
                maximum=pos_max,
            )
        }

        if any(uv is not None for _, uv, _ in primitive_vertices):
            attributes["TEXCOORD_0"] = gltf_accessor(
                accessors,
                buffer_views,
                buffer,
                pack_floats(
                    [coord for _, uv, _ in primitive_vertices for coord in (uv or (0.0, 0.0))]
                ),
                5126,
                "VEC2",
                len(primitive_vertices),
                target=34962,
            )

        if any(normal is not None for _, _, normal in primitive_vertices):
            attributes["NORMAL"] = gltf_accessor(
                accessors,
                buffer_views,
                buffer,
                pack_floats(
                    [coord for _, _, normal in primitive_vertices for coord in (normal or (0.0, 0.0, 1.0))]
                ),
                5126,
                "VEC3",
                len(primitive_vertices),
                target=34962,
            )

        primitive = {
            "attributes": attributes,
            "indices": gltf_accessor(
                accessors,
                buffer_views,
                buffer,
                pack_uints(indices),
                5125,
                "SCALAR",
                len(indices),
                target=34963,
            ),
        }
        material_name = obj.get("material_name")
        if material_name in material_index_by_name:
            primitive["material"] = material_index_by_name[material_name]

        meshes.append({"name": obj["name"], "primitives": [primitive]})
        node_extras = {
            "source_hrc": obj.get("source_hrc"),
            "material_name": material_name,
        }
        node_name = obj.get("node_name") or obj["name"]
        node_index = add_node(
            node_name,
            matrix=obj.get("node_matrix"),
            extras=node_extras,
            parent_name=obj.get("parent_name"),
        )
        if "mesh" in nodes[node_index]:
            suffix = len(nodes)
            node_index = add_node(
                f"{node_name}_mesh{suffix}",
                matrix=obj.get("node_matrix"),
                extras=node_extras,
                parent_name=obj.get("parent_name"),
            )
        nodes[node_index]["mesh"] = len(meshes) - 1

    for camera in cameras:
        yfov = camera.get("yfov_radians")
        znear = camera.get("clip_near")
        zfar = camera.get("clip_far")
        if yfov is None or znear is None:
            continue
        gltf_cameras.append(
            {
                "name": camera["name"],
                "type": "perspective",
                "perspective": {
                    "yfov": float(yfov),
                    "znear": max(float(znear), 0.0001),
                    **(
                        {"zfar": float(zfar)}
                        if zfar is not None and float(zfar) > float(znear)
                        else {}
                    ),
                },
            }
        )
        node_name = camera["name"]
        if node_name in node_index_by_name:
            node_name = f"{node_name}_cam"
        node_index = add_node(node_name, extras=camera.get("extras", {}))
        node = nodes[node_index]
        node["camera"] = len(gltf_cameras) - 1
        if camera.get("translation") is not None and "matrix" not in node:
            node["translation"] = camera["translation"]
        if camera.get("rotation") is not None and "matrix" not in node:
            node["rotation"] = camera["rotation"]

    for light in lights:
        light_def = {
            "name": light["name"],
            "type": light["type"],
            "color": light.get("color", [1.0, 1.0, 1.0]),
            "intensity": float(light.get("intensity", 1.0)),
        }
        if light.get("range") is not None:
            light_def["range"] = float(light["range"])
        if light["type"] == "spot":
            light_def["spot"] = {
                "innerConeAngle": float(light.get("innerConeAngle", 0.0)),
                "outerConeAngle": float(light.get("outerConeAngle", 0.785398)),
            }
        gltf_lights.append(light_def)
        node_name = light["name"]
        if node_name in node_index_by_name:
            node_name = f"{node_name}_light"
        node_index = add_node(node_name, extras=light.get("extras", {}))
        node = nodes[node_index]
        node["extensions"] = {"KHR_lights_punctual": {"light": len(gltf_lights) - 1}}
        if light.get("translation") is not None and "matrix" not in node:
            node["translation"] = light["translation"]
        if light.get("rotation") is not None and "matrix" not in node:
            node["rotation"] = light["rotation"]

    for extra in extra_nodes:
        node_index = add_node(
            extra["name"],
            extras=extra.get("extras", {}),
            parent_name=extra.get("parent_name"),
        )
        node = nodes[node_index]
        if extra.get("translation") is not None and "matrix" not in node:
            node["translation"] = extra["translation"]
        if extra.get("rotation") is not None and "matrix" not in node:
            node["rotation"] = extra["rotation"]

    child_indices: set[int] = set()
    for child_index, parent_name in pending_parent_links:
        parent_index = node_index_by_name.get(parent_name)
        if parent_index is None:
            continue
        nodes[parent_index].setdefault("children", []).append(child_index)
        child_indices.add(child_index)

    gltf = {
        "asset": {"version": "2.0", "generator": "bz2_extract.py"},
        "scene": 0,
        "scenes": [
            {
                "nodes": [
                    index
                    for index in range(len(nodes))
                    if index not in child_indices
                ]
                or list(range(len(nodes)))
            }
        ],
        "nodes": nodes,
        "meshes": meshes,
        "buffers": [{"byteLength": len(buffer), "uri": gltf_bin_path.name}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "materials": gltf_materials,
    }
    if images:
        gltf["images"] = images
    if textures:
        gltf["textures"] = textures
    if gltf_cameras:
        gltf["cameras"] = gltf_cameras
    if gltf_lights:
        gltf["extensionsUsed"] = ["KHR_lights_punctual"]
        gltf["extensions"] = {"KHR_lights_punctual": {"lights": gltf_lights}}

    gltf_path.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    gltf_bin_path.write_bytes(buffer)


def scene_export_output_dir(scene_path: str) -> Path:
    relative = Path(scene_path)
    try:
        relative = relative.relative_to(MODEL_ROOT)
    except ValueError:
        relative = Path(scene_path)
    return EXTRACT_DIR / "scene_exports" / relative.with_suffix("")


def infer_scene_alias_mirror_x(model_name: str | None, source_hrc: str | None) -> bool:
    if not model_name or not source_hrc:
        return False
    model_lower = model_name.lower()
    source_lower = source_hrc.lower()
    return (
        (("tread_r" in model_lower) and ("tread_l" in source_lower))
        or (("suspension_r" in model_lower) and ("suspension_l" in source_lower))
        or (("tractor_r" in model_lower) and ("tractor_l" in source_lower))
        or (("sidewall_r" in model_lower) and ("sidewall_l" in source_lower))
    )


def infer_isdf_scavenger_track_offsets(scene_path: str, mesh_lookup: dict[str, dict], scene: dict) -> dict[str, list[float]]:
    if "modelsdirectory/ISDF_SCAVENGER/SCENES/" not in scene_path:
        return {}

    model_links = {link.get("model_name"): link for link in scene.get("model_material_texture_links", [])}
    body_link = model_links.get("Scavenger_t-main_body.1-0")
    tread_link = model_links.get("Scavenger_t-tread_l.1-0")
    if not body_link or not tread_link:
        return {}

    body_entry = mesh_lookup.get(body_link.get("model_resolved_path") or "")
    tread_entry = mesh_lookup.get(tread_link.get("model_resolved_path") or "")
    if not body_entry or not tread_entry:
        return {}

    body_bounds = body_entry.get("bounds")
    tread_bounds = tread_entry.get("bounds")
    if not body_bounds or not tread_bounds:
        return {}

    body_min = body_bounds["min"]
    body_max = body_bounds["max"]
    tread_min = tread_bounds["min"]
    tread_max = tread_bounds["max"]

    body_half_width = max(abs(body_min[0]), abs(body_max[0]))
    tread_half_width = max(abs(tread_min[0]), abs(tread_max[0]))
    # The decoded tread mesh sits in a different local placement than the body shell.
    # These offsets are tuned against the fuller scavenger surrogate meshes so the
    # left/right track groups tuck under the hull instead of floating wide and low.
    x_offset = round(body_half_width + (tread_half_width * 0.79), 6)
    y_offset = round(body_min[1] - tread_max[1] + 1.35, 6)
    z_offset = round(body_max[2] - tread_max[2] + 0.5, 6)

    return {
        "Scavenger_t-tread_l.1-0": [-x_offset, y_offset, z_offset],
        "Scavenger_t-tread_r.1-0": [x_offset, y_offset, z_offset],
    }


def infer_scene_mesh_override(scene_path: str, model_name: str | None, model_path: str | None, mesh_lookup: dict[str, dict]) -> dict | None:
    if not model_name:
        return None

    if scene_path.startswith("modelsdirectory/ISDF_SCAVENGER/SCENES/"):
        if model_name == "Scavenger_t-main_body.1-0":
            alt_path = "modelsdirectory/ISDF_SCAVENGER/MODELS/Scavenger_t-main_body.2-0.hrc"
            alt_entry = mesh_lookup.get(alt_path)
            if alt_entry and alt_entry.get("decoded_obj"):
                return {
                    "source_hrc": alt_path,
                    "decoded_obj": alt_entry["decoded_obj"],
                    "local_translation_xyz": ISDF_SCAVENGER_BODY_OFFSET,
                    "reason": "closed_sibling_main_body",
                }

        surrogate_components = {
            "Scavenger_t-cockpit.1-0": (8, "surrogate_obj2_component_8_cockpit"),
            "Scavenger_t-mudflap.1-0": (2, "surrogate_obj2_component_2_mudflap"),
            "Scavenger_t-obj3_2_1.2-0": (3, "surrogate_obj2_component_3_obj3_2_1"),
        }
        if model_name in surrogate_components:
            alt_path = "modelsdirectory/ISDF_SCAVENGER/MODELS/Scavenger_t-obj2.1-0.hrc"
            alt_entry = mesh_lookup.get(alt_path)
            component_rank, reason = surrogate_components[model_name]
            if alt_entry and alt_entry.get("decoded_obj"):
                return {
                    "source_hrc": alt_path,
                    "decoded_obj": alt_entry["decoded_obj"],
                    "component_rank": component_rank,
                    "local_translation_xyz": ISDF_SCAVENGER_BODY_OFFSET,
                    "reason": reason,
                }

    return None


def resolve_scene_texture_png(info: dict, scene_path: str, images_report: dict | None) -> Path | None:
    texture_png_rel = info.get("texture_source_picture_png")
    if texture_png_rel:
        src_png = ROOT / texture_png_rel
        if src_png.exists():
            return src_png

    if not images_report:
        return None

    scene_parts = Path(scene_path).parts
    scene_family = scene_parts[1].lower() if len(scene_parts) > 1 else ""
    wanted_source = str(info.get("texture_source_picture_path") or "").replace("\\", "/").lower()
    wanted_stem = Path(str(info.get("texture_source_picture") or "")).stem.lower()
    material_path = str(info.get("material_resolved_path") or "").replace("\\", "/").lower()

    best_score = -1
    best_path: Path | None = None
    image_entries = list(images_report.get("entries", [])) + list(images_report.get("archive_entries", []))
    for entry in image_entries:
        source_rel = str(entry.get("source") or "").replace("\\", "/")
        png_rel = entry.get("png")
        if not png_rel:
            continue
        png_path = ROOT / png_rel
        if not png_path.exists():
            continue

        source_lower = source_rel.lower()
        score = 0
        if wanted_source and source_lower == wanted_source:
            score += 100
        if wanted_stem and Path(source_lower).stem.lower() == wanted_stem:
            score += 20
        if scene_family and f"/{scene_family}/" in source_lower:
            score += 5
        if material_path and scene_family and scene_family in material_path and scene_family in source_lower:
            score += 2

        if score > best_score:
            best_score = score
            best_path = png_path

    return best_path if best_score > 0 else None


def build_scene_export(scene_path: str) -> dict:
    scene_report = load_cached_report("scene_dependencies.json")
    if scene_report is None:
        scene_report = build_scene_dependencies()

    mesh_report = load_cached_report("hrc_mesh_like.json")
    if mesh_report is None:
        mesh_report = parse_mesh_like_hrc()

    hrc_header_report = load_cached_report("hrc_headers.json")
    if hrc_header_report is None:
        hrc_header_report = parse_hrc_headers()

    material_report = load_cached_report("binary_materials.json")
    if material_report is None:
        material_report = parse_binary_materials()

    camera_report = load_cached_report("binary_cameras.json")
    if camera_report is None:
        camera_report = parse_binary_cameras()

    light_report = load_cached_report("binary_lights.json")
    if light_report is None:
        light_report = parse_binary_lights()

    images_report = load_cached_report("images.json")

    scene = next((entry for entry in scene_report["entries"] if entry["path"] == scene_path), None)
    if scene is None:
        raise RuntimeError(f"Scene not found in dependencies report: {scene_path}")

    hrc_lookup = {entry["path"]: entry for entry in hrc_header_report.get("entries", []) if entry.get("path")}
    mesh_lookup = {entry["path"]: entry for entry in mesh_report.get("entries", []) if entry.get("path")}
    material_lookup = {entry["path"]: entry for entry in material_report.get("entries", []) if entry.get("path")}
    camera_lookup = {entry["path"]: entry for entry in camera_report.get("entries", []) if entry.get("path")}
    light_lookup = {entry["path"]: entry for entry in light_report.get("entries", []) if entry.get("path")}
    model_parent = {}
    for relation in scene.get("relations", []):
        if relation["source_chapter"] == "MODELS" and relation["target_chapter"] == "MODELS":
            for edge in relation["entries"]:
                if edge.get("source_name") and edge.get("target_name"):
                    model_parent[edge["source_name"]] = edge["target_name"]
    environment_lookup = scene.get("environment", {})
    inferred_model_offsets = infer_isdf_scavenger_track_offsets(scene_path, mesh_lookup, scene)

    model_override: dict[str, dict | None] = {}
    model_mirror: dict[str, bool] = {}
    for link in scene.get("model_material_texture_links", []):
        model_name = link.get("model_name")
        if not model_name:
            continue
        if model_name not in model_override:
            model_override[model_name] = infer_scene_mesh_override(
                scene_path, model_name, link.get("model_resolved_path"), mesh_lookup
            )
        if model_name not in model_mirror:
            model_mirror[model_name] = infer_scene_alias_mirror_x(
                model_name, link.get("model_resolved_path")
            )

    world_matrix_cache: dict[str, list[list[float]]] = {}
    local_matrix_cache: dict[str, list[list[float]]] = {}

    def model_local_matrix(model_name: str | None) -> list[list[float]]:
        if not model_name:
            return matrix_to_rows(None)
        if model_name in local_matrix_cache:
            return local_matrix_cache[model_name]

        env = environment_lookup.get(model_name, {})
        if env.get("srt"):
            local = build_srt_matrix(
                env["srt"]["scale"],
                env["srt"]["rotation_xyz"],
                env["srt"]["translation_xyz"],
            )
        elif model_name in inferred_model_offsets:
            local = build_srt_matrix(
                (1.0, 1.0, 1.0),
                (0.0, 0.0, 0.0),
                inferred_model_offsets[model_name],
            )
        else:
            local = matrix_to_rows(None)

        override = model_override.get(model_name)
        if override and override.get("local_translation_xyz"):
            local = mul_row_major(
                build_srt_matrix(
                    (1.0, 1.0, 1.0),
                    (0.0, 0.0, 0.0),
                    override["local_translation_xyz"],
                ),
                local,
            )

        if model_mirror.get(model_name):
            local = mul_row_major(
                [
                    [-1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
                local,
            )

        local_matrix_cache[model_name] = local
        return local

    def model_world_matrix(model_name: str | None) -> list[list[float]]:
        if not model_name:
            return matrix_to_rows(None)
        if model_name in world_matrix_cache:
            return world_matrix_cache[model_name]
        parent = model_parent.get(model_name)
        world = mul_row_major(model_local_matrix(model_name), model_world_matrix(parent))
        world_matrix_cache[model_name] = world
        return world

    scene_dir = scene_export_output_dir(scene_path)
    texture_dir = scene_dir / "textures"
    scene_dir.mkdir(parents=True, exist_ok=True)
    texture_dir.mkdir(parents=True, exist_ok=True)

    obj_path = scene_dir / f"{sanitize_name(Path(scene_path).stem)}.obj"
    mtl_path = scene_dir / f"{sanitize_name(Path(scene_path).stem)}.mtl"
    gltf_path = scene_dir / f"{sanitize_name(Path(scene_path).stem)}.gltf"
    gltf_bin_path = scene_dir / f"{sanitize_name(Path(scene_path).stem)}.bin"
    manifest_path = scene_dir / "scene.json"

    exported_objects = []
    missing_objects = []
    exported_cameras = []
    missing_cameras = []
    exported_lights = []
    missing_lights = []
    exported_placeholders = []
    used_materials: dict[str, dict] = {}
    gltf_objects = []
    gltf_cameras = []
    gltf_lights = []
    gltf_extra_nodes = []
    exported_model_names: set[str] = set()
    model_node_defs: list[dict] = []
    model_node_by_name: dict[str, dict] = {}
    model_node_name_map: dict[str, str] = {}
    for chapter in scene.get("chapters", []):
        if chapter["chapter"] != "MODELS":
            continue
        for item in chapter.get("entries", []):
            model_name = item.get("name")
            if not model_name:
                continue
            node_name = sanitize_name(model_name)
            parent_name = model_parent.get(model_name)
            parent_node_name = sanitize_name(parent_name) if parent_name else None
            node_def = {
                "name": node_name,
                "parent_name": parent_node_name,
                "matrix": row_major_to_gltf_matrix(model_local_matrix(model_name)),
                "extras": {
                    "model_name": model_name,
                    "source_hrc": item.get("resolved_path"),
                },
            }
            model_node_defs.append(node_def)
            model_node_by_name[model_name] = node_def
            model_node_name_map[model_name] = node_name

    vertex_offset = 0
    texcoord_offset = 0
    normal_offset = 0
    obj_lines = [f"mtllib {mtl_path.name}"]

    for link in scene.get("model_material_texture_links", []):
        model_path = link.get("model_resolved_path")
        override = model_override.get(link.get("model_name") or "")
        mesh_entry = mesh_lookup.get(model_path or "")
        effective_model_path = override["source_hrc"] if override else model_path
        decoded_obj = override["decoded_obj"] if override else (mesh_entry.get("decoded_obj") if mesh_entry else None)
        if not decoded_obj:
            missing_objects.append(
                {
                    "model_name": link.get("model_name"),
                    "reason": "missing_decoded_obj",
                    "model_resolved_path": model_path,
                }
            )
            continue

        decoded_obj_path = ROOT / decoded_obj
        if not decoded_obj_path.exists():
            missing_objects.append(
                {
                    "model_name": link.get("model_name"),
                    "reason": "decoded_obj_not_found",
                    "decoded_obj": decoded_obj,
                }
            )
            continue

        geometry = parse_obj_geometry(decoded_obj_path)
        if override and override.get("component_rank") is not None:
            components = connected_obj_components(geometry)
            component_index = int(override["component_rank"]) - 1
            if component_index < 0 or component_index >= len(components):
                missing_objects.append(
                    {
                        "model_name": link.get("model_name"),
                        "reason": "surrogate_component_not_found",
                        "decoded_obj": decoded_obj,
                        "component_rank": override["component_rank"],
                    }
                )
                continue
            geometry = components[component_index]

        world = model_world_matrix(link.get("model_name"))
        mirror_x = model_mirror.get(link.get("model_name") or "", False)
        material_name = sanitize_name(link.get("material_name") or f"{link.get('model_name', 'material')}_mat")
        material_entry = material_lookup.get(link.get("material_resolved_path") or "", {})
        used_materials[material_name] = {
            "material_name": link.get("material_name"),
            "material_resolved_path": link.get("material_resolved_path"),
            "texture_name": link.get("texture_name"),
            "texture_resolved_path": link.get("texture_resolved_path"),
            "texture_source_picture": link.get("texture_source_picture"),
            "texture_source_picture_png": link.get("texture_source_picture_png"),
            "material_hint": material_entry.get("likely_fields", {}),
            "material_decoded": material_entry.get("decoded_fields", {}),
        }

        obj_lines.append("")
        obj_lines.append(f"o {sanitize_name(link.get('model_name') or decoded_obj_path.stem)}")
        transformed_vertices = [transform_point(vertex, world) for vertex in geometry["vertices"]]
        transformed_normals = [transform_vector(normal, world) for normal in geometry["normals"]]
        face_refs = [list(reversed(refs)) if mirror_x else list(refs) for refs in geometry["faces"]]
        gltf_face_refs = geometry["faces"]
        obj_lines.extend(f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in transformed_vertices)
        obj_lines.extend(f"vt {u:.6f} {v:.6f}" for u, v in geometry["texcoords"])
        obj_lines.extend(f"vn {nx:.6f} {ny:.6f} {nz:.6f}" for nx, ny, nz in transformed_normals)
        obj_lines.append(f"usemtl {material_name}")
        for refs in face_refs:
            out_tokens = face_tokens_with_offsets(
                refs,
                vertex_offset=vertex_offset,
                texcoord_offset=texcoord_offset,
                normal_offset=normal_offset,
            )
            obj_lines.append("f " + " ".join(out_tokens))

        exported_objects.append(
            {
                "model_name": link.get("model_name"),
                "source_hrc": effective_model_path,
                "decoded_obj": decoded_obj,
                "world_transform": environment_lookup.get(link.get("model_name") or "", {}).get("srt"),
                "material_name": link.get("material_name"),
                "texture_name": link.get("texture_name"),
                "texture_source_picture": link.get("texture_source_picture"),
                "inferred_mirror_x": mirror_x,
                "inferred_override": override["reason"] if override else None,
                "inferred_component_rank": override.get("component_rank") if override else None,
            }
        )
        if link.get("model_name"):
            exported_model_names.add(link["model_name"])
        model_name = link.get("model_name") or ""
        node_name = model_node_name_map.get(model_name, sanitize_name(model_name or decoded_obj_path.stem))
        node_matrix = None
        parent_name = None
        if model_name not in model_node_name_map:
            node_matrix = row_major_to_gltf_matrix(model_local_matrix(model_name))
            parent = model_parent.get(model_name)
            parent_name = sanitize_name(parent) if parent else None
        gltf_objects.append(
            {
                "name": sanitize_name(model_name or decoded_obj_path.stem),
                "node_name": node_name,
                "parent_name": parent_name,
                "node_matrix": node_matrix,
                "source_hrc": effective_model_path,
                "material_name": material_name,
                "geometry": {
                    "vertices": geometry["vertices"],
                    "texcoords": geometry["texcoords"],
                    "normals": geometry["normals"],
                    "faces": gltf_face_refs,
                },
            }
        )

        vertex_offset += len(transformed_vertices)
        texcoord_offset += len(geometry["texcoords"])
        normal_offset += len(transformed_normals)

    obj_path.write_text("\n".join(obj_lines) + "\n", encoding="utf-8")

    mtl_lines = []
    copied_textures = []
    for export_name, info in used_materials.items():
        decoded = info.get("material_decoded", {})
        hints = info.get("material_hint", {})
        diffuse = decoded.get("diffuse_rgb") or hints.get("color_hint_b") or [1.0, 1.0, 1.0]
        specular = decoded.get("specular_rgb") or hints.get("specular_hint") or [0.0, 0.0, 0.0]
        shininess = float(decoded.get("shininess") or hints.get("hardness_hint") or 0.0)
        roughness = float(decoded.get("roughness_hint") or phong_exponent_to_roughness(shininess))
        alpha = float(decoded.get("alpha_hint", 1.0))
        illum_model = 2 if int(decoded.get("shading_type") or hints.get("shading_type_hint") or 2) != 0 else 1
        texture_uri = None
        mtl_lines.append(f"newmtl {export_name}")
        mtl_lines.append(f"Kd {float(diffuse[0]):.6f} {float(diffuse[1]):.6f} {float(diffuse[2]):.6f}")
        mtl_lines.append(f"Ks {float(specular[0]):.6f} {float(specular[1]):.6f} {float(specular[2]):.6f}")
        mtl_lines.append(f"Ns {shininess:.6f}")
        mtl_lines.append(f"illum {illum_model}")
        mtl_lines.append(f"d {alpha:.6f}")

        src_png = resolve_scene_texture_png(info, scene_path, images_report)
        if src_png is not None:
            dest_png = texture_dir / src_png.name
            shutil.copy2(src_png, dest_png)
            mtl_lines.append(f"map_Kd textures/{dest_png.name}")
            texture_uri = f"textures/{dest_png.name}"
            copied_textures.append(
                {
                    "material_name": info.get("material_name"),
                    "texture_name": info.get("texture_name"),
                    "source_picture": info.get("texture_source_picture"),
                    "png": rel(dest_png),
                }
            )
        info["gltf_material"] = {
            "diffuse": [float(diffuse[0]), float(diffuse[1]), float(diffuse[2])],
            "specular": [float(specular[0]), float(specular[1]), float(specular[2])],
            "alpha": alpha,
            "roughness_factor": roughness,
            "texture_uri": texture_uri,
        }
        mtl_lines.append("")

    mtl_path.write_text("\n".join(mtl_lines), encoding="utf-8")

    for chapter in scene.get("chapters", []):
        if chapter["chapter"] == "CAMERAS":
            for item in chapter.get("entries", []):
                resolved_path = item.get("resolved_path")
                if not resolved_path:
                    missing_cameras.append(
                        {
                            "camera_name": item.get("name"),
                            "reason": "missing_resolved_path",
                        }
                    )
                    continue
                camera_entry = camera_lookup.get(resolved_path)
                if not camera_entry:
                    missing_cameras.append(
                        {
                            "camera_name": item.get("name"),
                            "reason": "missing_camera_decode",
                            "camera_resolved_path": resolved_path,
                        }
                    )
                    continue

                exported_camera = {
                    "camera_name": item.get("name"),
                    "camera_resolved_path": resolved_path,
                    "position_xyz": camera_entry.get("position_xyz"),
                    "rotation_xyz_hint": camera_entry.get("rotation_xyz_hint"),
                    "clip_near": camera_entry.get("clip_near"),
                    "clip_far": camera_entry.get("clip_far"),
                    "yfov_radians": camera_entry.get("yfov_radians"),
                }
                exported_cameras.append(exported_camera)

                gltf_cameras.append(
                    {
                        "name": sanitize_name(item.get("name") or Path(resolved_path).stem),
                        "translation": camera_entry.get("position_xyz"),
                        "rotation": camera_entry.get("rotation_quaternion_hint"),
                        "clip_near": camera_entry.get("clip_near"),
                        "clip_far": camera_entry.get("clip_far"),
                        "yfov_radians": camera_entry.get("yfov_radians"),
                        "extras": {
                            "source_cam": resolved_path,
                            "rotation_xyz_hint": camera_entry.get("rotation_xyz_hint"),
                            "rotation_unit_hint": camera_entry.get("rotation_unit_hint"),
                            "focal_length_hint": camera_entry.get("focal_length_hint"),
                            "f_stop_hint": camera_entry.get("f_stop_hint"),
                            "focus_distance_hint": camera_entry.get("focus_distance_hint"),
                        },
                    }
                )

        if chapter["chapter"] == "LIGHTS":
            for item in chapter.get("entries", []):
                resolved_path = item.get("resolved_path")
                if not resolved_path:
                    missing_lights.append(
                        {
                            "light_name": item.get("name"),
                            "reason": "missing_resolved_path",
                        }
                    )
                    continue
                light_entry = light_lookup.get(resolved_path)
                if not light_entry:
                    missing_lights.append(
                        {
                            "light_name": item.get("name"),
                            "reason": "missing_light_decode",
                            "light_resolved_path": resolved_path,
                        }
                    )
                    continue

                exported_light = {
                    "light_name": item.get("name"),
                    "light_resolved_path": resolved_path,
                    "light_kind_hint": light_entry.get("light_kind_hint"),
                    "color_rgb": light_entry.get("color_rgb"),
                    "position_xyz": light_entry.get("position_xyz"),
                    "direction_xyz_hint": light_entry.get("direction_xyz_hint"),
                    "range_hint": light_entry.get("range_hint"),
                }
                exported_lights.append(exported_light)

                light_kind = light_entry.get("light_kind_hint") or "point"
                light_range = light_entry.get("range_hint")
                if light_range is not None and float(light_range) >= 5000.0:
                    light_range = None

                gltf_light = {
                    "name": sanitize_name(item.get("name") or Path(resolved_path).stem),
                    "type": light_kind if light_kind in {"point", "spot", "directional"} else "point",
                    "color": light_entry.get("color_rgb") or [1.0, 1.0, 1.0],
                    "intensity": light_entry.get("intensity_hint") or 1.0,
                    "range": light_range,
                    "translation": light_entry.get("position_xyz"),
                    "rotation": light_entry.get("rotation_quaternion_hint"),
                    "innerConeAngle": min(
                        float(light_entry.get("spot_inner_cone_angle") or 0.0),
                        float(light_entry.get("spot_outer_cone_angle") or 0.785398),
                    ),
                    "outerConeAngle": max(
                        float(light_entry.get("spot_inner_cone_angle") or 0.0),
                        float(light_entry.get("spot_outer_cone_angle") or 0.785398),
                    ),
                    "extras": {
                        "source_lig": resolved_path,
                        "direction_xyz_hint": light_entry.get("direction_xyz_hint"),
                        "cone_scale_hint": light_entry.get("cone_scale_hint"),
                    },
                }
                gltf_lights.append(gltf_light)

    for chapter in scene.get("chapters", []):
        if chapter["chapter"] != "MODELS":
            continue
        for item in chapter.get("entries", []):
            model_name = item.get("name")
            resolved_path = item.get("resolved_path")
            if not model_name or model_name in exported_model_names:
                continue
            if not resolved_path or resolved_path not in hrc_lookup:
                continue
            header = hrc_lookup[resolved_path]
            payload_kind = header.get("payload_kind")
            if payload_kind not in {"nurbs_like", "spline_like", "surface_or_fx"}:
                continue
            world = model_world_matrix(model_name)
            translation = matrix_translation_xyz(world)
            placeholder = {
                "model_name": model_name,
                "resolved_path": resolved_path,
                "payload_kind": payload_kind,
                "class_id": header.get("class_id"),
                "subtype_id": header.get("subtype_id"),
                "world_translation_xyz": translation,
            }
            exported_placeholders.append(placeholder)
            node_def = model_node_by_name.get(model_name)
            placeholder_extra = {
                "source_hrc": resolved_path,
                "payload_kind": payload_kind,
                "class_id": header.get("class_id"),
                "subtype_id": header.get("subtype_id"),
                "placeholder_kind": "unresolved_hrc_payload",
            }
            if node_def:
                node_def.setdefault("extras", {}).setdefault("placeholders", []).append(placeholder_extra)
            else:
                gltf_extra_nodes.append(
                    {
                        "name": sanitize_name(model_name),
                        "translation": translation,
                        "extras": placeholder_extra,
                    }
                )

    write_gltf_scene_bundle(
        gltf_path,
        gltf_bin_path,
        gltf_objects,
        {
            name: info["gltf_material"]
            for name, info in used_materials.items()
        },
        cameras=gltf_cameras,
        lights=gltf_lights,
        extra_nodes=gltf_extra_nodes,
        node_defs=model_node_defs,
    )

    manifest = {
        "scene_path": scene_path,
        "obj": rel(obj_path),
        "mtl": rel(mtl_path),
        "gltf": rel(gltf_path),
        "gltf_bin": rel(gltf_bin_path),
        "exported_object_count": len(exported_objects),
        "missing_object_count": len(missing_objects),
        "exported_camera_count": len(exported_cameras),
        "missing_camera_count": len(missing_cameras),
        "exported_light_count": len(exported_lights),
        "missing_light_count": len(missing_lights),
        "exported_placeholder_count": len(exported_placeholders),
        "exported_objects": exported_objects,
        "missing_objects": missing_objects,
        "exported_cameras": exported_cameras,
        "missing_cameras": missing_cameras,
        "exported_lights": exported_lights,
        "missing_lights": missing_lights,
        "exported_placeholders": exported_placeholders,
        "copied_textures": copied_textures,
    }
    write_json(manifest_path, manifest)
    return manifest


def load_cached_report(name: str) -> dict | None:
    path = REPORTS_DIR / name
    if not path.exists():
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def run_full(*, refresh_heavy: bool = False) -> dict:
    inventory = build_inventory()
    scenes = parse_all_dsc()
    texture_maps = parse_all_texture_maps()
    binaries = fingerprint_binary_formats()
    images = None if refresh_heavy else load_cached_report("images.json")
    if images is None:
        images = convert_all_images()

    xsi_exports = None if refresh_heavy else load_cached_report("xsi_exports.json")
    if xsi_exports is None:
        xsi_exports = export_all_xsi()

    scene_dependencies = build_scene_dependencies()
    expressions = parse_expression_files()
    animations = parse_animation_files()
    binary_cameras = parse_binary_cameras()
    binary_lights = parse_binary_lights()
    nurbs_usage = analyze_nurbs_usage()
    hrc_headers = parse_hrc_headers()
    binary_materials = parse_binary_materials()
    hrc_mesh_like = parse_mesh_like_hrc()

    write_json(REPORTS_DIR / "inventory.json", inventory)
    write_json(REPORTS_DIR / "scenes.json", scenes)
    write_json(REPORTS_DIR / "texture_maps.json", texture_maps)
    write_json(REPORTS_DIR / "binary_signatures.json", binaries)
    write_json(REPORTS_DIR / "images.json", images)
    write_json(REPORTS_DIR / "xsi_exports.json", xsi_exports)
    write_json(REPORTS_DIR / "scene_dependencies.json", scene_dependencies)
    write_json(REPORTS_DIR / "expressions.json", expressions)
    write_json(REPORTS_DIR / "animations.json", animations)
    write_json(REPORTS_DIR / "binary_cameras.json", binary_cameras)
    write_json(REPORTS_DIR / "binary_lights.json", binary_lights)
    write_json(REPORTS_DIR / "nurbs_usage.json", nurbs_usage)
    write_json(REPORTS_DIR / "hrc_headers.json", hrc_headers)
    write_json(REPORTS_DIR / "binary_materials.json", binary_materials)
    write_json(REPORTS_DIR / "hrc_mesh_like.json", hrc_mesh_like)

    summary = {
        "inventory_report": rel(REPORTS_DIR / "inventory.json"),
        "scene_report": rel(REPORTS_DIR / "scenes.json"),
        "texture_map_report": rel(REPORTS_DIR / "texture_maps.json"),
        "binary_signature_report": rel(REPORTS_DIR / "binary_signatures.json"),
        "image_report": rel(REPORTS_DIR / "images.json"),
        "xsi_export_report": rel(REPORTS_DIR / "xsi_exports.json"),
        "scene_dependency_report": rel(REPORTS_DIR / "scene_dependencies.json"),
        "expression_report": rel(REPORTS_DIR / "expressions.json"),
        "animation_report": rel(REPORTS_DIR / "animations.json"),
        "binary_camera_report": rel(REPORTS_DIR / "binary_cameras.json"),
        "binary_light_report": rel(REPORTS_DIR / "binary_lights.json"),
        "nurbs_usage_report": rel(REPORTS_DIR / "nurbs_usage.json"),
        "hrc_header_report": rel(REPORTS_DIR / "hrc_headers.json"),
        "binary_material_report": rel(REPORTS_DIR / "binary_materials.json"),
        "hrc_mesh_like_report": rel(REPORTS_DIR / "hrc_mesh_like.json"),
        "xsi_extract_root": rel(EXTRACT_DIR / "xsi"),
        "image_extract_root": rel(EXTRACT_DIR / "images"),
    }
    write_json(REPORTS_DIR / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory and extract BZ2 Softimage-era assets.")
    parser.add_argument(
        "command",
        choices=[
            "inventory",
            "scenes",
            "texture-maps",
            "binaries",
            "images",
            "images-batch",
            "image-file",
            "scene-deps",
            "expressions",
            "animations",
            "ani-stream-probe",
            "ani-camera-probe",
            "binary-cameras",
            "binary-lights",
            "nurbs-usage",
            "hrc-headers",
            "binary-materials",
            "hrc-mesh-like",
            "scene-export",
            "xsi",
            "full",
        ],
        default="full",
        nargs="?",
        help="Which extraction pass to run.",
    )
    parser.add_argument("arg1", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("arg2", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument(
        "--refresh-heavy",
        action="store_true",
        help="When running `full`, rebuild image conversion and XSI export instead of reusing existing reports.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    if args.command == "inventory":
        payload = build_inventory()
        write_json(REPORTS_DIR / "inventory.json", payload)
    elif args.command == "scenes":
        payload = parse_all_dsc()
        write_json(REPORTS_DIR / "scenes.json", payload)
    elif args.command == "texture-maps":
        payload = parse_all_texture_maps()
        write_json(REPORTS_DIR / "texture_maps.json", payload)
    elif args.command == "binaries":
        payload = fingerprint_binary_formats()
        write_json(REPORTS_DIR / "binary_signatures.json", payload)
    elif args.command == "images":
        payload = convert_all_images()
        write_json(REPORTS_DIR / "images.json", payload)
    elif args.command == "images-batch":
        if not args.arg1:
            raise SystemExit("images-batch requires a batch file")
        batch_paths = [line for line in Path(args.arg1).read_text(encoding="utf-8").splitlines() if line]
        payload = convert_image_batch(batch_paths)
        Path(args.arg1).with_suffix(".json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return 0
    elif args.command == "image-file":
        if not args.arg1 or not args.arg2:
            raise SystemExit("image-file requires a source path and destination directory")
        try:
            png_path = ensure_png_direct(Path(args.arg1), Path(args.arg2))
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        payload = {"png": str(png_path.resolve())}
        print(json.dumps(payload))
        return 0
    elif args.command == "scene-deps":
        payload = build_scene_dependencies()
        write_json(REPORTS_DIR / "scene_dependencies.json", payload)
    elif args.command == "expressions":
        payload = parse_expression_files()
        write_json(REPORTS_DIR / "expressions.json", payload)
    elif args.command == "animations":
        payload = parse_animation_files()
        write_json(REPORTS_DIR / "animations.json", payload)
    elif args.command == "ani-stream-probe":
        per_chapter_limit = 12
        max_frames = 60
        if args.arg1 and args.arg1.isdigit():
            per_chapter_limit = max(1, int(args.arg1))
        if args.arg2 and args.arg2.isdigit():
            max_frames = max(1, int(args.arg2))
        payload = probe_animation_streams(per_chapter_limit=per_chapter_limit, max_frames=max_frames)
        write_json(REPORTS_DIR / "animation_stream_probes.json", payload)
    elif args.command == "ani-camera-probe":
        max_frames = 120
        if args.arg1 and args.arg1.isdigit():
            max_frames = max(1, int(args.arg1))
        payload = probe_camera_animation_links(max_frames=max_frames)
        write_json(REPORTS_DIR / "animation_camera_probe.json", payload)
    elif args.command == "binary-cameras":
        payload = parse_binary_cameras()
        write_json(REPORTS_DIR / "binary_cameras.json", payload)
    elif args.command == "binary-lights":
        payload = parse_binary_lights()
        write_json(REPORTS_DIR / "binary_lights.json", payload)
    elif args.command == "nurbs-usage":
        payload = analyze_nurbs_usage()
        write_json(REPORTS_DIR / "nurbs_usage.json", payload)
    elif args.command == "hrc-headers":
        payload = parse_hrc_headers()
        write_json(REPORTS_DIR / "hrc_headers.json", payload)
    elif args.command == "binary-materials":
        payload = parse_binary_materials()
        write_json(REPORTS_DIR / "binary_materials.json", payload)
    elif args.command == "hrc-mesh-like":
        payload = parse_mesh_like_hrc()
        write_json(REPORTS_DIR / "hrc_mesh_like.json", payload)
    elif args.command == "scene-export":
        if not args.arg1:
            raise SystemExit("scene-export requires a .dsc scene path")
        payload = build_scene_export(args.arg1.replace("\\", "/"))
    elif args.command == "xsi":
        payload = export_all_xsi()
        write_json(REPORTS_DIR / "xsi_exports.json", payload)
    else:
        payload = run_full(refresh_heavy=args.refresh_heavy)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

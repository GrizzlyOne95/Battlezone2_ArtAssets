#!/usr/bin/env python3
"""Stage the shipped Battlezone II textures as a last-resort picture source.

The art archive references pictures that it does not contain, for example
``//SERVER/.../dropship/PICTURES/ivdrop00`` or the whole
``FURY_vehicles/PICTURES`` family. The retail game ships compiled versions of
most of them in its DOCP ``.pak`` files:
- ``data.pak`` holds the in-game resolution (256x256 for units);
- ``smtex.pak`` is the 128x128 low-detail set;
- ``bumps.pak`` holds bump maps.

This script extracts only the picture members (``.pic``, ``.tga``, ``.bmp``)
into ``.bz2-source-cache/retail_bz2/<pak>/...``. The texture resolver
(``bz2_texture_layers_gltf.resolve_picture_for_crop``) consults them only when
a picture is absent from the whole art archive, prefers ``data`` over
``smtex`` over ``bumps``, and records ``picture_resolution:
retail_game_supplement``. These pictures are the shipped derivatives, not
the artist's originals: resized and possibly re-touched. Stale crops over
them follow ``bz2_projection_uv.effective_crop``.

    python scripts/bz2_retail_pictures.py "<...>/_Battlezone_II_1.0.7z"
    python scripts/bz2_retail_pictures.py "<install dir with data.pak>"
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

PAKS = ("data.pak", "smtex.pak", "bumps.pak")
PICTURE_SUFFIXES = {".pic", ".tga", ".bmp"}
DEFAULT_OUT = Path(__file__).resolve().parents[1] / ".bz2-source-cache" / "retail_bz2"


def read_docp(path: Path) -> dict[str, tuple[int, int, int]]:
    """DOCP v2 table of contents: member path -> (offset, stored size, size)."""
    data = path.read_bytes()
    if data[:4] != b"DOCP":
        raise ValueError(f"not a DOCP pak: {path}")
    version, directory_count, records_end, file_count, toc_offset = struct.unpack_from("<IIIII", data, 4)
    if version != 2 or not 56 <= toc_offset <= records_end <= len(data):
        raise ValueError(f"unsupported DOCP header: {path}")
    cursor = toc_offset
    records = []
    for _ in range(file_count):
        directory_id, name_length = struct.unpack_from("<IB", data, cursor)
        cursor += 5
        name = data[cursor : cursor + name_length].decode("cp1252")
        cursor += name_length
        offset, stored, size = struct.unpack_from("<III", data, cursor)
        cursor += 12
        records.append((directory_id, name, offset, stored, size))
    directories = [""]
    for _ in range(directory_count):
        length = data[cursor]
        directories.append(data[cursor + 1 : cursor + 1 + length].decode("cp1252").replace("\\", "/"))
        cursor += 1 + length
    members = {}
    for directory_id, name, offset, stored, size in records:
        member = f"{directories[directory_id]}/{name}" if directory_id else name
        if ".." in member.split("/") or ":" in member:
            raise ValueError(f"unsafe member {member!r} in {path}")
        members[member] = (offset, stored, size)
    return members


def extract_pictures(pak: Path, out_dir: Path) -> int:
    members = read_docp(pak)
    with pak.open("rb") as stream:
        count = 0
        for member, (offset, stored, size) in members.items():
            if Path(member).suffix.lower() not in PICTURE_SUFFIXES:
                continue
            stream.seek(offset)
            payload = stream.read(stored)
            if stored != size:
                payload = zlib.decompress(payload)
            if len(payload) != size:
                raise ValueError(f"size mismatch for {member} in {pak}")
            target = out_dir / member
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            count += 1
    return count


def stage(source: Path, out: Path = DEFAULT_OUT) -> dict:
    """Extract the retail pictures from an install folder or the installer 7z."""
    with tempfile.TemporaryDirectory(prefix="bz2-retail-") as temp:
        install = source
        if source.is_file():
            seven_zip = shutil.which("7z") or r"C:\Program Files\7-Zip\7z.exe"
            subprocess.run([seven_zip, "x", "-y", f"-o{temp}", str(source), *[f"*\\{p}" for p in PAKS], "-r"], check=True, capture_output=True)
            install = Path(temp)
        report = {"source": str(source), "out": str(out), "paks": {}}
        for pak_name in PAKS:
            found = next(iter(sorted(install.rglob(pak_name))), None)
            if found is None:
                report["paks"][pak_name] = "absent"
                continue
            report["paks"][pak_name] = extract_pictures(found, out / Path(pak_name).stem)
    out.mkdir(parents=True, exist_ok=True)
    (out / "retail_pictures.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="retail installer .7z or an install folder containing data.pak")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(stage(args.source, args.out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

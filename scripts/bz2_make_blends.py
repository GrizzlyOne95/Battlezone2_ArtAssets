#!/usr/bin/env python3
"""Write a ready-to-open ``<scene>.blend`` into every reconstructed bundle.

Each .blend is built by ``blender_engine_blend.py`` from the bundle's engine
glTF twin (baked textures, identical to the .xsi) plus the recovered camera,
lights and render resolution. Paths are relative, so bundles stay portable.

    python scripts/bz2_make_blends.py artifacts/reconstructed --jobs 12
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "blender_engine_blend.py"
BLENDER_CANDIDATES = [
    os.environ.get("BLENDER"),
    shutil.which("blender"),
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
]


def find_blender() -> str:
    for candidate in BLENDER_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("Blender not found: set BLENDER or pass --blender")


def build(bundle: Path, blender: str, force: bool) -> dict:
    gltf = next(iter(sorted((bundle / "engine").glob("*.gltf"))), None)
    if gltf is None:
        return {"bundle": bundle.name, "status": "no_engine_gltf"}
    blend = bundle / f"{gltf.stem}.blend"
    if not force and blend.is_file() and blend.stat().st_mtime >= gltf.stat().st_mtime:
        return {"bundle": bundle.name, "status": "up_to_date", "blend": blend.name}
    command = [blender, "--background", "--factory-startup", "--python", str(SCRIPT), "--",
               str(gltf), str(blend), str(bundle / "scene.scene.json"), str(bundle / "scene.render_state.json")]
    run = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=900)
    ok = run.returncode == 0 and "BLEND_OK" in run.stdout and blend.is_file()
    return {"bundle": bundle.name, "status": "ok" if ok else "failed", "blend": blend.name,
            **({} if ok else {"log_tail": (run.stdout + run.stderr)[-1500:]})}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reconstructed", type=Path)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--blender")
    parser.add_argument("--force", action="store_true", help="rebuild even when the .blend is newer than the glTF")
    args = parser.parse_args()
    blender = args.blender or find_blender()
    bundles = sorted(p for p in args.reconstructed.iterdir() if p.is_dir())
    with ThreadPoolExecutor(args.jobs) as pool:
        results = list(pool.map(lambda b: build(b, blender, args.force), bundles))
    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    (args.reconstructed / "blend_build.json").write_text(json.dumps({"counts": counts, "results": results}, indent=1), encoding="utf-8")
    print(json.dumps(counts))
    for result in results:
        if result["status"] == "failed":
            print("FAILED", result["bundle"], result.get("log_tail", "")[-300:])
    return 0 if not counts.get("failed") else 1


if __name__ == "__main__":
    raise SystemExit(main())

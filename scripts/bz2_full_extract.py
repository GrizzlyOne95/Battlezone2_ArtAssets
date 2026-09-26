#!/usr/bin/env python3
r"""Run the full BZ2 Softimage static-scene reconstruction pipeline.

Accepts an extracted tree, ZIP, or original .7z archive; discovers DSC scenes
(including embedded historical ZIPs), infers scene prefixes, runs one/many/all
scenes, optionally finishes them in Blender, and writes a batch manifest.
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence


class PipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class SceneCandidate:
    path: Path
    relative: str
    prefix: str
    asset_source: Path
    source_label: str = "primary"

    @property
    def selector(self) -> str:
        return self.relative if self.source_label == "primary" else f"{self.source_label}::{self.relative}"


def _norm_relative(path: Path) -> str:
    return path.as_posix().lstrip("./")


def _safe_output_name(value: str) -> str:
    path = Path(value)
    stem = path.name[:-4] if path.name.lower().endswith(".dsc") else path.name
    parent = "__".join(p for p in path.parts[:-2] if p not in (".", ""))
    raw = f"{parent}__{stem}" if parent else stem
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
    return "".join(c if c in allowed else "_" for c in raw).strip("._") or "scene"


def _find_modelsdirectory(root: Path) -> Path:
    root = root.resolve()
    if not root.is_dir():
        raise PipelineError(f"source root is not a directory: {root}")
    if root.name.lower() == "modelsdirectory":
        return root
    if (root / "modelsdirectory").is_dir():
        return (root / "modelsdirectory").resolve()
    candidates = sorted(p.resolve() for p in root.rglob("modelsdirectory") if p.is_dir())
    if not candidates:
        if any(root.glob("**/SCENES/*.dsc")):
            return root
        raise PipelineError(f"could not locate modelsdirectory beneath: {root}")
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) != 1:
        raise PipelineError("multiple modelsdirectory trees found:\n  - " + "\n  - ".join(map(str, candidates[:12])))
    return candidates[0]


def _infer_prefix(modelsdirectory: Path, scene: Path) -> str:
    try:
        rel = scene.resolve().parent.parent.relative_to(modelsdirectory.resolve())
    except ValueError as exc:
        raise PipelineError(f"scene is not beneath modelsdirectory: {scene}") from exc
    value = _norm_relative(rel)
    return "" if value == "." else value


def discover_scenes(modelsdirectory: Path, *, source_label: str = "primary") -> list[SceneCandidate]:
    return [
        SceneCandidate(
            path=p.resolve(),
            relative=_norm_relative(p.relative_to(modelsdirectory)),
            prefix=_infer_prefix(modelsdirectory, p),
            asset_source=modelsdirectory.resolve(),
            source_label=source_label,
        )
        for p in sorted(modelsdirectory.glob("**/SCENES/*.dsc"))
        if p.is_file()
    ]


def _extract_zip(source: Path, destination: Path) -> str:
    destination = destination.resolve()
    with zipfile.ZipFile(source, "r") as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise PipelineError(f"ZIP member escapes extraction root: {info.filename!r}") from exc
        archive.extractall(destination)
    return "python-zipfile"


@contextlib.contextmanager
def prepared_scene_sources(primary: Path, *, include_embedded_zips: bool = True) -> Iterator[tuple[list[Path], list[SceneCandidate], list[dict]]]:
    roots = [primary.resolve()]
    scenes = discover_scenes(primary)
    sources = [{"label": "primary", "modelsdirectory": str(primary.resolve()), "scene_count": len(scenes)}]
    archives = sorted(p for p in primary.glob("**/*.zip") if p.is_file()) if include_embedded_zips else []
    if not archives:
        yield roots, scenes, sources
        return
    with tempfile.TemporaryDirectory(prefix="bz2-embedded-zips-") as temp:
        scene_archive_index = 0
        for archive in archives:
            label = _norm_relative(archive.relative_to(primary))
            try:
                # PATCH: the source corpus also contains ordinary picture/render ZIPs.
                # Only archives that actually contain DSCs beneath a SCENES folder
                # are historical scene roots; treating every ZIP as one produced
                # false source errors and needlessly unpacked large render archives.
                with zipfile.ZipFile(archive, "r") as probe:
                    has_scene = any(
                        not info.is_dir()
                        and info.filename.replace("\\", "/").lower().endswith(".dsc")
                        and "/scenes/" in ("/" + info.filename.replace("\\", "/").lower())
                        for info in probe.infolist()
                    )
                if not has_scene:
                    sources.append({
                        "label": label,
                        "archive": str(archive.resolve()),
                        "scene_count": 0,
                        "status": "ignored_non_scene_archive",
                    })
                    continue

                out = Path(temp) / f"{scene_archive_index:04d}_{_safe_output_name(label)}"
                scene_archive_index += 1
                out.mkdir(parents=True, exist_ok=True)
                _extract_zip(archive, out)
                root = _find_modelsdirectory(out)
                found = discover_scenes(root, source_label=label)
                roots.append(root)
                scenes.extend(found)
                sources.append({"label": label, "archive": str(archive.resolve()), "modelsdirectory": str(root), "scene_count": len(found), "status": "ok"})
            except Exception as exc:
                sources.append({"label": label, "archive": str(archive.resolve()), "status": "error", "error": f"{type(exc).__name__}: {exc}"})
        scenes.sort(key=lambda s: (s.source_label, s.relative))
        yield roots, scenes, sources


def _select_scenes(scenes: Sequence[SceneCandidate], requested: Sequence[str], matches: Sequence[str], select_all: bool) -> list[SceneCandidate]:
    if select_all:
        return list(scenes)
    chosen: dict[str, SceneCandidate] = {}
    for raw in requested:
        q = raw.replace("\\", "/").lstrip("./")
        exact = [s for s in scenes if q.lower() in {s.selector.lower(), s.relative.lower()}]
        if len(exact) == 1:
            chosen[exact[0].selector] = exact[0]
            continue
        if len(exact) > 1:
            raise PipelineError(f"scene selector is ambiguous: {raw}\n  - " + "\n  - ".join(s.selector for s in exact[:20]))
        base = Path(q.split("::", 1)[-1]).name.lower()
        hits = [s for s in scenes if Path(s.relative).name.lower() == base]
        if len(hits) == 1:
            chosen[hits[0].selector] = hits[0]
            continue
        if len(hits) > 1:
            raise PipelineError(f"scene basename is ambiguous: {raw}\n  - " + "\n  - ".join(s.selector for s in hits[:20]))
        hits = [s for s in scenes if q.lower() in s.selector.lower()]
        if len(hits) == 1:
            chosen[hits[0].selector] = hits[0]
            continue
        if len(hits) > 1:
            raise PipelineError(f"scene selector matches multiple scenes: {raw}\n  - " + "\n  - ".join(s.selector for s in hits[:20]))
        raise PipelineError(f"scene not found: {raw}")
    for pattern in matches:
        p = pattern.lower()
        wildcard = any(c in p for c in "*?[]")
        for scene in scenes:
            candidate = scene.selector.lower()
            if (fnmatch.fnmatch(candidate, p) if wildcard else p in candidate):
                chosen[scene.selector] = scene
    selected = sorted(chosen.values(), key=lambda s: s.selector)
    if not selected:
        raise PipelineError("no scenes selected; pass --scene, --match, --all, or --list-scenes")
    return selected


def _find_7zip(explicit: str | None = None) -> str | None:
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return str(p.resolve())
        if shutil.which(explicit):
            return shutil.which(explicit)
        raise PipelineError(f"7-Zip executable not found: {explicit}")
    for name in ("7z", "7zz", "7za"):
        if shutil.which(name):
            return shutil.which(name)
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            p = Path(base) / "7-Zip" / "7z.exe"
            if p.is_file():
                return str(p)
    return None


def _extract_7z(source: Path, destination: Path, seven_zip: str | None) -> str:
    if importlib.util.find_spec("py7zr") is not None:
        import py7zr  # type: ignore
        with py7zr.SevenZipFile(source, mode="r") as archive:
            archive.extractall(path=destination)
        return "py7zr"
    exe = _find_7zip(seven_zip)
    if not exe:
        raise PipelineError(".7z input requires py7zr or 7-Zip; install one or pass --7zip <path>")
    result = subprocess.run([exe, "x", str(source), f"-o{destination}", "-y"], check=False)
    if result.returncode:
        raise PipelineError(f"7-Zip extraction failed with exit code {result.returncode}: {source}")
    return exe


def _cache_marker(cache: Path) -> Path:
    return cache / ".bz2_full_extract_source.json"


def _source_signature(source: Path) -> dict:
    stat = source.stat()
    return {"path": str(source.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _cache_is_current(cache: Path, source: Path) -> bool:
    marker = _cache_marker(cache)
    if not marker.is_file():
        return False
    try:
        return json.loads(marker.read_text(encoding="utf-8")).get("source") == _source_signature(source)
    except (OSError, json.JSONDecodeError):
        return False


def _write_cache_marker(cache: Path, source: Path, method: str) -> None:
    _cache_marker(cache).write_text(json.dumps({"schema": "bz2-full-extract-source-cache-v1", "source": _source_signature(source), "method": method}, indent=2), encoding="utf-8")


@contextlib.contextmanager
def prepared_source(source: Path, *, cache_dir: Path | None = None, seven_zip: str | None = None, refresh_cache: bool = False) -> Iterator[tuple[Path, dict]]:
    source = source.expanduser().resolve()
    if source.is_dir():
        root = _find_modelsdirectory(source)
        yield root, {"kind": "directory", "source": str(source), "modelsdirectory": str(root), "cached": False}
        return
    if not source.is_file():
        raise PipelineError(f"source does not exist: {source}")
    kind = source.suffix.lower()
    if kind not in {".zip", ".7z"}:
        raise PipelineError(f"unsupported source type: {source.name}; expected directory, .zip or .7z")
    temporary = None
    cached = cache_dir is not None
    if cached:
        extraction_root = cache_dir.expanduser().resolve()
    else:
        temporary = tempfile.TemporaryDirectory(prefix="bz2-full-extract-")
        extraction_root = Path(temporary.name)
    try:
        needs_extract = not (cached and extraction_root.is_dir() and not refresh_cache and _cache_is_current(extraction_root, source))
        if needs_extract:
            if cached and extraction_root.exists():
                entries = list(extraction_root.iterdir())
                if entries and not _cache_marker(extraction_root).is_file():
                    raise PipelineError(f"refusing to clear non-empty unowned --cache-dir: {extraction_root}")
                shutil.rmtree(extraction_root)
            extraction_root.mkdir(parents=True, exist_ok=True)
            method = _extract_zip(source, extraction_root) if kind == ".zip" else _extract_7z(source, extraction_root, seven_zip)
            if cached:
                _write_cache_marker(extraction_root, source, method)
        else:
            method = json.loads(_cache_marker(extraction_root).read_text(encoding="utf-8")).get("method", "cached")
        root = _find_modelsdirectory(extraction_root)
        yield root, {"kind": kind[1:], "source": str(source), "modelsdirectory": str(root), "extraction_root": str(extraction_root), "cached": cached, "cache_reused": not needs_extract, "extractor": method}
    finally:
        if temporary is not None:
            temporary.cleanup()


def _resolve_blender(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "auto":
        resolved = shutil.which("blender") or shutil.which("blender.exe")
        if not resolved:
            raise PipelineError("Blender is not on PATH; pass the full executable path")
        return resolved
    p = Path(value).expanduser()
    if p.is_file():
        return str(p.resolve())
    if shutil.which(value):
        return shutil.which(value)
    raise PipelineError(f"Blender executable not found: {value}")


def _run_blender(blender: str, out: Path) -> dict:
    argv = [blender, "--background", "--python", str(Path(__file__).with_name("blender_finish_reconstruction.py")), "--", str(out / "scene.gltf"), str(out / "scene.scene.json"), str(out / "scene.blend"), str(out / "scene.texture_layers.json"), str(out / "scene.model_textures.json"), str(out / "scene.render_state.json")]
    started = time.time()
    result = subprocess.run(argv, check=False)
    return {"argv": argv, "returncode": result.returncode, "seconds": round(time.time() - started, 3), "scene_blend": str(out / "scene.blend")}


def _import_reconstructor():
    import bz2_reconstruct_scene as reconstruct_scene
    return reconstruct_scene


def _import_xsi_exporter():
    import bz2_xsi_export as xsi_export
    return xsi_export


def process_scene(recon, scene: SceneCandidate, out: Path, *, curve_steps: int, surface_steps_u: int, surface_steps_v: int, blender: str | None, xsi: bool, clean_output: bool) -> dict:
    """Reconstruct one scene in this process and return its batch record."""
    if clean_output and out.exists():
        shutil.rmtree(out)
    started = time.time()
    item = {"scene": scene.relative, "selector": scene.selector, "source_label": scene.source_label, "asset_source": str(scene.asset_source), "prefix": scene.prefix, "output_dir": str(out.resolve())}
    try:
        manifest = recon.reconstruct(scene.path, scene.asset_source, scene.prefix, out, curve_steps=curve_steps, surface_steps_u=surface_steps_u, surface_steps_v=surface_steps_v)
        render = out / "scene.render_state.json"
        if not render.is_file():
            render.write_text(json.dumps({"schema": "bz2-render-state-placeholder-v1", "status": "not_authored", "note": "DSC scene contains no resolved SETUP_SOFT record"}, indent=2), encoding="utf-8")
        item.update({"status": "ok", "reconstruction_manifest": str((out / "reconstruction.json").resolve()), "source_warning_count": int(manifest.get("source_warning_count") or 0), "source_warnings": manifest.get("source_warnings") or [], "counts": {"nodes": manifest.get("final_node_count"), "meshes": manifest.get("final_mesh_count"), "primitives": manifest.get("final_primitive_count"), "materials": manifest.get("final_material_count"), "images": manifest.get("final_image_count")}})
        if manifest.get("cross_group_bindings"):
            item["cross_group_bindings"] = manifest["cross_group_bindings"]
        if xsi:
            xsi_export = _import_xsi_exporter()
            try:
                item["xsi"] = xsi_export.batch_summary(xsi_export.export_bundle(out))
            except Exception as exc:  # noqa: BLE001 - recorded per scene
                item["xsi"] = xsi_export.failure_summary(exc)
        if blender:
            item["blender"] = _run_blender(blender, out)
            if item["blender"]["returncode"]:
                raise PipelineError(f"Blender failed with exit code {item['blender']['returncode']}")
    except Exception as exc:
        item.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    item["seconds"] = round(time.time() - started, 3)
    return item


def _scene_to_json(scene: SceneCandidate) -> dict:
    return {"path": str(scene.path), "relative": scene.relative, "prefix": scene.prefix, "asset_source": str(scene.asset_source), "source_label": scene.source_label}


def _run_isolated(scene: SceneCandidate, out: Path, options: dict, work_dir: Path, index: int) -> dict:
    """Run one scene in a fresh interpreter so native crashes stay per-scene failures."""
    request = work_dir / f"scene_{index:05d}.request.json"
    result_path = work_dir / f"scene_{index:05d}.result.json"
    request.write_text(json.dumps({"scene": _scene_to_json(scene), "out": str(out), "options": options, "result": str(result_path)}), encoding="utf-8")
    started = time.time()
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--scene-worker", str(request)], capture_output=True, text=True, errors="replace", check=False)
    if result_path.is_file():
        return json.loads(result_path.read_text(encoding="utf-8"))
    tail = (proc.stderr or proc.stdout or "")[-2000:]
    return {"scene": scene.relative, "selector": scene.selector, "source_label": scene.source_label, "asset_source": str(scene.asset_source), "prefix": scene.prefix, "output_dir": str(out.resolve()), "status": "error", "error": f"WorkerCrash: scene worker exited with code {proc.returncode} without a result", "worker_output_tail": tail, "seconds": round(time.time() - started, 3)}


def _scene_worker(request_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    data = request["scene"]
    scene = SceneCandidate(Path(data["path"]), data["relative"], data["prefix"], Path(data["asset_source"]), data["source_label"])
    item = process_scene(_import_reconstructor(), scene, Path(request["out"]), **request["options"])
    Path(request["result"]).write_text(json.dumps(item), encoding="utf-8")
    return 0


def run_batch(modelsdirectory: Path, scenes: Sequence[SceneCandidate], output_root: Path, *, curve_steps: int, surface_steps_u: int, surface_steps_v: int, blender: str | None, keep_going: bool, clean_output: bool, xsi: bool = False, isolate: bool = False, jobs: int = 1) -> dict:
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    order = {scene.selector: i for i, scene in enumerate(scenes)}
    batch_started = time.time()
    isolated = bool(isolate or jobs > 1)
    options = {"curve_steps": curve_steps, "surface_steps_u": surface_steps_u, "surface_steps_v": surface_steps_v, "blender": blender, "xsi": xsi, "clean_output": clean_output}

    def snapshot() -> dict:
        # Qualification fix: persist progress after every scene so a long corpus
        # run remains diagnosable if interrupted by CI, sandbox limits, or user cancellation.
        ordered = sorted(results, key=lambda r: order.get(r.get("selector"), 0))
        return {"schema": "bz2-full-extraction-batch-v2", "modelsdirectory": str(modelsdirectory.resolve()), "output_root": str(output_root.resolve()), "requested_scene_count": len(scenes), "processed_scene_count": len(results), "success_count": sum(r.get("status") == "ok" for r in results), "failure_count": sum(r.get("status") == "error" for r in results), "xsi_success_count": sum((r.get("xsi") or {}).get("status") == "ok" for r in results), "isolated_workers": isolated, "jobs": jobs, "seconds": round(time.time() - batch_started, 3), "results": ordered}

    def checkpoint() -> None:
        (output_root / "batch_reconstruction.json").write_text(json.dumps(snapshot(), indent=2), encoding="utf-8")

    def record(item: dict) -> bool:
        results.append(item)
        status = item["status"] if item["status"] == "ok" else f"FAILED {item.get('error', '')[:160]}"
        print(f"[{len(results)}/{len(scenes)}] {item['selector']}: {status}", flush=True)
        checkpoint()
        return item["status"] == "ok"

    if not isolated:
        recon = _import_reconstructor()
        for scene in scenes:
            out = output_root / _safe_output_name(scene.selector)
            if not record(process_scene(recon, scene, out, **options)) and not keep_going:
                break
        return snapshot()

    import concurrent.futures

    with tempfile.TemporaryDirectory(prefix="bz2-scene-workers-") as tmp:
        work_dir = Path(tmp)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futures = {
                pool.submit(_run_isolated, scene, output_root / _safe_output_name(scene.selector), options, work_dir, i): scene
                for i, scene in enumerate(scenes, 1)
            }
            for future in concurrent.futures.as_completed(futures):
                if not record(future.result()) and not keep_going:
                    for pending in futures:
                        pending.cancel()
                    break
    return snapshot()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path, nargs="?", help="modelsdirectory/tree, .zip, or original .7z")
    p.add_argument("--scene", action="append", default=[])
    p.add_argument("--match", action="append", default=[])
    p.add_argument("--all", action="store_true")
    p.add_argument("--list-scenes", action="store_true")
    p.add_argument("--no-embedded-zips", action="store_true")
    p.add_argument("--output", type=Path, default=Path("artifacts/reconstructed"))
    p.add_argument("--cache-dir", type=Path)
    p.add_argument("--refresh-cache", action="store_true")
    p.add_argument("--7zip", dest="seven_zip")
    p.add_argument("--blender", nargs="?", const="auto")
    p.add_argument("--keep-going", action="store_true")
    p.add_argument("--preserve-output", action="store_true")
    p.add_argument("--curve-steps", type=int, default=64)
    p.add_argument("--surface-steps-u", type=int, default=32)
    p.add_argument("--surface-steps-v", type=int, default=32)
    p.add_argument("--no-xsi", action="store_true", help="skip the engine-ready dotXSI export stage")
    p.add_argument("--jobs", type=int, default=1, help="parallel isolated scene workers")
    p.add_argument("--isolate", action="store_true", help="run each scene in its own interpreter (implied for multi-scene batches and --jobs > 1)")
    p.add_argument("--scene-worker", type=Path, help=argparse.SUPPRESS)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.scene_worker:
        return _scene_worker(args.scene_worker)
    if args.source is None:
        _parser().error("the source argument is required")
    try:
        blender = _resolve_blender(args.blender)
        with prepared_source(args.source, cache_dir=args.cache_dir, seven_zip=args.seven_zip, refresh_cache=args.refresh_cache) as (models, source_info):
            with prepared_scene_sources(models, include_embedded_zips=not args.no_embedded_zips) as (_roots, scenes, sources):
                if not scenes:
                    raise PipelineError(f"no DSC scenes found beneath: {models}")
                if args.list_scenes:
                    print(json.dumps({"source": source_info, "discovered_sources": sources, "scene_count": len(scenes), "scenes": [{"selector": s.selector, "path": s.relative, "prefix": s.prefix, "source_label": s.source_label} for s in scenes]}, indent=2))
                    return 0
                selected = _select_scenes(scenes, args.scene, args.match, args.all)
                batch = run_batch(models, selected, args.output.expanduser().resolve(), curve_steps=max(2, args.curve_steps), surface_steps_u=max(2, args.surface_steps_u), surface_steps_v=max(2, args.surface_steps_v), blender=blender, keep_going=args.keep_going, clean_output=not args.preserve_output, xsi=not args.no_xsi, isolate=args.isolate or len(selected) > 1, jobs=max(1, args.jobs))
                batch.update({"source": source_info, "discovered_sources": sources})
                path = args.output.expanduser().resolve() / "batch_reconstruction.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(batch, indent=2), encoding="utf-8")
                print(json.dumps(batch, indent=2))
                return 0 if batch["failure_count"] == 0 else 1
    except Exception as exc:
        print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Apply archive-backed fixes proven locally during bz2_art.7z qualification."""
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"patch anchor missing in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "scripts/bz2_hrc_gltf.py",
    'SLOT_MATERIAL_RE = re.compile(rb"\\x00([\\x01-\\xff])\\x00\\x00([ -~]{1,80})\\x00")',
    '# Qualification fix: enumerate overlapping slot signatures. A byte sequence inside\n'
    '# the final SRT float can itself resemble a short slot record and a consuming\n'
    '# regex then skips the genuine marker one byte later. Zero-width lookahead keeps\n'
    '# both candidates visible while the existing SRT plausibility checks select the\n'
    '# real 36-byte-preceding transform.\n'
    'SLOT_MATERIAL_RE = re.compile(rb"(?=\\x00([\\x01-\\xff])\\x00\\x00([ -~]{1,80})\\x00)")',
)

replace_once(
    "scripts/bz2_full_extract.py",
    '    results = []\n    batch_started = time.time()\n    for i, scene in enumerate(scenes, 1):\n',
    '    results = []\n'
    '    batch_started = time.time()\n\n'
    '    def snapshot() -> dict:\n'
    '        # Qualification fix: persist progress after every scene so a long corpus\n'
    '        # run remains diagnosable if interrupted by CI, sandbox limits, or user cancellation.\n'
    '        return {"schema": "bz2-full-extraction-batch-v1", "modelsdirectory": str(modelsdirectory.resolve()), "output_root": str(output_root.resolve()), "requested_scene_count": len(scenes), "processed_scene_count": len(results), "success_count": sum(r.get("status") == "ok" for r in results), "failure_count": sum(r.get("status") == "error" for r in results), "seconds": round(time.time() - batch_started, 3), "results": results}\n\n'
    '    def checkpoint() -> None:\n'
    '        (output_root / "batch_reconstruction.json").write_text(json.dumps(snapshot(), indent=2), encoding="utf-8")\n\n'
    '    for i, scene in enumerate(scenes, 1):\n',
)

replace_once(
    "scripts/bz2_full_extract.py",
    '            results.append(item)\n            item["seconds"] = round(time.time() - started, 3)\n            if not keep_going:\n',
    '            results.append(item)\n            item["seconds"] = round(time.time() - started, 3)\n            checkpoint()\n            if not keep_going:\n',
)

replace_once(
    "scripts/bz2_full_extract.py",
    '        item["seconds"] = round(time.time() - started, 3)\n        results.append(item)\n    return {"schema": "bz2-full-extraction-batch-v1", "modelsdirectory": str(modelsdirectory.resolve()), "output_root": str(output_root.resolve()), "requested_scene_count": len(scenes), "processed_scene_count": len(results), "success_count": sum(r.get("status") == "ok" for r in results), "failure_count": sum(r.get("status") == "error" for r in results), "seconds": round(time.time() - batch_started, 3), "results": results}\n',
    '        item["seconds"] = round(time.time() - started, 3)\n        results.append(item)\n        checkpoint()\n    return snapshot()\n',
)

replace_once(
    "tests/test_full_extract.py",
    '            self.assertEqual(json.loads((out / "scene.render_state.json").read_text(encoding="utf-8"))["status"], "not_authored")\n',
    '            self.assertEqual(json.loads((out / "scene.render_state.json").read_text(encoding="utf-8"))["status"], "not_authored")\n'
    '            # Long corpus runs checkpoint after each processed scene so an external\n'
    '            # timeout/cancellation does not discard all batch diagnostics.\n'
    '            checkpoint = json.loads((output / "batch_reconstruction.json").read_text(encoding="utf-8"))\n'
    '            self.assertEqual(checkpoint["processed_scene_count"], 1)\n'
    '            self.assertEqual(checkpoint["success_count"], 1)\n',
)

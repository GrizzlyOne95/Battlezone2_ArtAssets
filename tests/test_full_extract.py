from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bz2_full_extract as full


class FullExtractTests(unittest.TestCase):
    def tree(self, root: Path) -> Path:
        models = root / "modelsdirectory"
        for rel in ("ISDF_WALKER/SCENES/walker.1-0.dsc", "Archival/NewTank/SCENES/tank.1-0.dsc"):
            p = models / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("ELEMENTS\nEndOfELEMENTS\nRELATIONS\nEndOfRELATIONS\n", encoding="latin-1")
        return models

    def test_discovery_prefix_and_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = self.tree(Path(tmp))
            self.assertEqual(full._find_modelsdirectory(Path(tmp)), models.resolve())
            scenes = full.discover_scenes(models)
            self.assertEqual([s.relative for s in scenes], ["Archival/NewTank/SCENES/tank.1-0.dsc", "ISDF_WALKER/SCENES/walker.1-0.dsc"])
            self.assertEqual([s.prefix for s in scenes], ["Archival/NewTank", "ISDF_WALKER"])
            self.assertEqual(full._select_scenes(scenes, ["walker.1-0.dsc"], [], False)[0].prefix, "ISDF_WALKER")
            self.assertEqual(full._select_scenes(scenes, [], ["newtank"], False)[0].prefix, "Archival/NewTank")
            self.assertEqual(full._select_scenes(scenes, [], ["*WALKER*"], False)[0].prefix, "ISDF_WALKER")

    def test_safe_output_name(self):
        value = full._safe_output_name("Archival/New Tank/SCENES/hi_res:ISDF_tank.1-0.dsc")
        self.assertEqual(value, "Archival__New_Tank__hi_res_ISDF_tank.1-0")
        self.assertNotIn("/", value)
        self.assertNotIn(":", value)

    def test_cache_signature_and_ownership_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.zip"
            with zipfile.ZipFile(source, "w") as zf:
                zf.writestr("modelsdirectory/A/SCENES/a.dsc", "x")
            cache = root / "cache"
            cache.mkdir()
            full._write_cache_marker(cache, source, "test")
            self.assertTrue(full._cache_is_current(cache, source))
            source.write_bytes(source.read_bytes() + b"x")
            self.assertFalse(full._cache_is_current(cache, source))

            other = root / "other-cache"
            other.mkdir()
            (other / "user-file.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(full.PipelineError):
                with full.prepared_source(source, cache_dir=other):
                    pass
            self.assertTrue((other / "user-file.txt").is_file())

    def test_directory_source_needs_no_archive_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = self.tree(Path(tmp))
            with full.prepared_source(Path(tmp)) as (actual, info):
                self.assertEqual(actual, models.resolve())
                self.assertEqual(info["kind"], "directory")

    def test_zip_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "bad.zip"
            with zipfile.ZipFile(source, "w") as zf:
                zf.writestr("../escape.txt", "bad")
            out = root / "out"
            out.mkdir()
            with self.assertRaises(full.PipelineError):
                full._extract_zip(source, out)
            self.assertFalse((root / "escape.txt").exists())

    def test_embedded_zip_isolated_and_qualified(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = self.tree(Path(tmp))
            archive = models / "Archive.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("ISDF_WALKER/SCENES/walker.1-0.dsc", "ELEMENTS\nEndOfELEMENTS\nRELATIONS\nEndOfRELATIONS\n")
            pictures = models / "Pictures.zip"
            with zipfile.ZipFile(pictures, "w") as zf:
                zf.writestr("RENDER_PICTURES/frame.001.pic", b"not-a-scene")
            with full.prepared_scene_sources(models) as (_roots, scenes, sources):
                embedded = [s for s in scenes if s.source_label == "Archive.zip"]
                self.assertEqual(embedded[0].selector, "Archive.zip::ISDF_WALKER/SCENES/walker.1-0.dsc")
                self.assertNotEqual(embedded[0].asset_source, models.resolve())
                self.assertTrue(any(s.get("label") == "Archive.zip" for s in sources))
                picture_source = next(s for s in sources if s.get("label") == "Pictures.zip")
                self.assertEqual(picture_source["status"], "ignored_non_scene_archive")
                self.assertEqual(picture_source["scene_count"], 0)
                self.assertFalse(any(s.source_label == "Pictures.zip" for s in scenes))
                with self.assertRaises(full.PipelineError):
                    full._select_scenes(scenes, ["walker.1-0.dsc"], [], False)
                chosen = full._select_scenes(scenes, [embedded[0].selector], [], False)
                self.assertEqual(chosen[0].source_label, "Archive.zip")

    def test_run_batch_cleans_and_supplies_render_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "source"
            scene = asset / "A/SCENES/a.dsc"
            scene.parent.mkdir(parents=True)
            scene.write_text("x", encoding="latin-1")
            candidate = full.SceneCandidate(scene, "A/SCENES/a.dsc", "A", asset, "Archive.zip")
            calls = []

            class Fake:
                @staticmethod
                def reconstruct(scene_dsc, asset_source, prefix, output_dir, **kwargs):
                    calls.append((asset_source, prefix))
                    output_dir.mkdir(parents=True, exist_ok=True)
                    (output_dir / "reconstruction.json").write_text("{}", encoding="utf-8")
                    return {"final_node_count": 1, "final_mesh_count": 2, "final_primitive_count": 3, "final_material_count": 4, "final_image_count": 5, "source_warning_count": 2, "source_warnings": [{"kind": "missing_material_picture_sources", "count": 2, "details": []}]}

            original = full._import_reconstructor
            full._import_reconstructor = lambda: Fake
            try:
                output = root / "out"
                stale = output / full._safe_output_name(candidate.selector)
                stale.mkdir(parents=True)
                (stale / "stale.txt").write_text("stale", encoding="utf-8")
                batch = full.run_batch(asset, [candidate], output, curve_steps=64, surface_steps_u=32, surface_steps_v=32, blender=None, keep_going=False, clean_output=True)
            finally:
                full._import_reconstructor = original

            self.assertEqual(batch["success_count"], 1)
            self.assertEqual(batch["results"][0]["source_warning_count"], 2)
            self.assertEqual(batch["results"][0]["source_warnings"][0]["kind"], "missing_material_picture_sources")
            self.assertEqual(calls[0], (asset, "A"))
            out = Path(batch["results"][0]["output_dir"])
            self.assertFalse((out / "stale.txt").exists())
            self.assertEqual(json.loads((out / "scene.render_state.json").read_text(encoding="utf-8"))["status"], "not_authored")
            # Long corpus runs checkpoint after each processed scene so an external
            # timeout/cancellation does not discard all batch diagnostics.
            checkpoint = json.loads((output / "batch_reconstruction.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["processed_scene_count"], 1)
            self.assertEqual(checkpoint["success_count"], 1)


if __name__ == "__main__":
    unittest.main()

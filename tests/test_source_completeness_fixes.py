import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bz2_dsc_multiroot_gltf as multiroot  # noqa: E402
import bz2_hrc_gltf as hrc_gltf  # noqa: E402
import bz2_hrc_tree_probe as tree_probe  # noqa: E402
import bz2_mtr_gltf_refine as mtr_refine  # noqa: E402


class _Store:
    def __init__(self, files):
        self.files = files

    def find_basename(self, basename, prefix=None):
        for path in self.files:
            if Path(path).name.lower() == basename.lower() and (prefix is None or path.lower().startswith(prefix.lower() + "/")):
                return path
        return None

    def find_all_basename(self, basename):
        return sorted(p for p in self.files if Path(p).name.lower() == basename.lower())

    def read(self, path):
        return self.files[path]


class CrossGroupRootTests(unittest.TestCase):
    def test_own_group_wins(self):
        store = _Store({"A/MODELS/x.1-0.hrc": b"a", "B/MODELS/x.1-0.hrc": b"b"})
        self.assertEqual(multiroot.resolve_root_member(store, "x.1-0", "A"), ("A/MODELS/x.1-0.hrc", None))

    def test_unique_or_identical_exact_name_binds_with_provenance(self):
        store = _Store({"B/MODELS/x.1-0.hrc": b"same", "C/MODELS/x.1-0.hrc": b"same"})
        member, binding = multiroot.resolve_root_member(store, "x.1-0", "A")
        self.assertEqual(member, "B/MODELS/x.1-0.hrc")
        self.assertEqual(binding["status"], "exact_name_cross_group_binding")
        self.assertEqual(binding["candidate_count"], 2)

    def test_differing_candidates_are_refused(self):
        store = _Store({"B/MODELS/x.1-0.hrc": b"one", "C/MODELS/x.1-0.hrc": b"two"})
        member, binding = multiroot.resolve_root_member(store, "x.1-0", "A")
        self.assertIsNone(member)
        self.assertEqual(binding["status"], "ambiguous_cross_group_candidates")

    def test_absent_is_plain_missing(self):
        self.assertEqual(multiroot.resolve_root_member(_Store({}), "x.1-0", "A"), (None, None))


class FxPointerResidueTests(unittest.TestCase):
    def test_class2_pointer_residue_restores_depth_slot(self):
        payload = b"\x00\x02\x00\x01" + b"\x05" * 16
        data = (
            b"\x11" + b"\x00" * 20 + b"\x00\x01fxA\x00" + payload
            + b"\x00\x9b\x5a\x03" + b"\x00" * 18 + b"\x00\x01fxB\x00" + payload
        )
        records = tree_probe.discover_records(data)
        self.assertEqual([r["name"] for r in records], ["fxA", "fxB"])
        self.assertEqual(records[1]["zero_run"], 22)
        self.assertTrue(records[1]["zero_run_pointer_residue"])

    def test_non_fx_short_runs_stay_rejected(self):
        data = b"\x11" + b"\x00\x9b\x5a\x03" + b"\x00" * 18 + b"\x00\x01obj\x00\x00\x04\x00\x00" + b"\x05" * 8
        self.assertEqual(tree_probe.discover_records(data), [])


class PinchedPolygonTests(unittest.TestCase):
    @unittest.skipIf(hrc_gltf.Polygon is None, "Shapely >= 2.1 not installed")
    def test_self_touching_ring_triangulates_on_source_corners_only(self):
        # Outer ring revisits vertex (2, 0): a "pinched" pair of lobes, plus a hole.
        vertices = [tuple(float(c) for c in v) for v in [(0, 0, 0), (2, 0, 0), (4, 0, 0), (4, 2, 0), (2, 1, 0), (0, 2, 0)]]
        mesh = {"vertices": vertices + [(0.5, 0.5, 0.0), (1.0, 0.5, 0.0), (0.5, 1.0, 0.0)]}
        shell = [0, 1, 2, 3, 1, 5]  # revisits vertex 1 -> ring self-touch
        hole = [6, 7, 8]
        contours = [[{"vertex_index": i, "uv": (0.0, 0.0), "normal": None} for i in loop] for loop in (shell, hole)]
        triangles = hrc_gltf._triangulate(mesh, contours)
        self.assertTrue(triangles)
        used = {corner["vertex_index"] for triangle in triangles for corner in triangle}
        self.assertTrue(used <= set(range(len(mesh["vertices"]))))


class MtrShadingTests(unittest.TestCase):
    def test_constant_shading_code_maps_to_unlit_and_xsi_zero(self):
        material = {}
        decoded = {
            "diffuse_rgb": [0.0, 0.5, 1.0],
            "specular_rgb": [0.5, 0.5, 0.5],
            "ambient_rgb": [1.0, 1.0, 1.0],
            "shininess": 150.0,
            "transparency": 0.0,
            "reflectivity": 0.0,
            "refractive_index": 1.0,
            "shading_model_code_u16": 1,
        }
        used = mtr_refine.refine_material(material, decoded)
        self.assertIn("KHR_materials_unlit", used)
        self.assertEqual(material["extras"]["bz2_softimage_mtr"]["xsi_shading_type"], 0)
        decoded["shading_model_code_u16"] = 3
        material = {}
        mtr_refine.refine_material(material, decoded)
        self.assertIsNone(material["extras"]["bz2_softimage_mtr"]["xsi_shading_type"])


if __name__ == "__main__":
    unittest.main()

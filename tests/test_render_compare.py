from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bz2_projection_uv as projection_uv  # noqa: E402
import bz2_render_compare as rc  # noqa: E402


def _quad(z: float, colour, part: int, texture=None) -> rc.Triangles:
    corners = np.array([[-1, -1, z], [1, -1, z], [1, 1, z], [-1, 1, z]], float)
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
    tris = [(0, 1, 2), (0, 2, 3)]
    normal = np.array([0, 0, 1.0])
    return rc.Triangles(
        np.array([corners[list(t)] for t in tris]),
        np.array([uv[list(t)] for t in tris]),
        np.array([colour, colour], float),
        [texture, texture],
        np.array([part, part], np.int32),
        np.repeat(normal[None, None], 2, axis=0).repeat(3, axis=1),
        [f"p{part}"],
    )


class ProjectionConventionTests(unittest.TestCase):
    def test_planar_yz_runs_picture_horizontal_along_z(self):
        # pluto.1 corridor walls: cementwall's bands run along the corridor (Z).
        bounds = ((0.0, 0.0, 0.0), (1.0, 8.0, 32.0))
        self.assertEqual(projection_uv.base_projection_uv((0.0, 0.0, 32.0), bounds, 3), (1.0, 0.0))
        self.assertEqual(projection_uv.base_projection_uv((0.0, 8.0, 0.0), bounds, 3), (0.0, 1.0))

    def test_stale_full_frame_crop_selects_whole_picture(self):
        # tankturret1: crop authored on 590x167, picture now 1000x283.
        crop = {"x0": 0, "x1": 589, "y0": 0, "y1": 166}
        self.assertIsNone(projection_uv.effective_crop(crop, 1000, 283))
        self.assertEqual(projection_uv.apply_crop((0.5, 0.5), crop, (1000, 283)), (0.5, 0.5))

    def test_real_crop_window_is_clamped_bottom_up(self):
        crop = {"x0": 100, "x1": 2000, "y0": 0, "y1": 49}
        self.assertEqual(projection_uv.effective_crop(crop, 1000, 100), (100.0, 999.0, 0.0, 49.0))
        u, v = projection_uv.apply_crop((0.0, 1.0), crop, (1000, 100))
        self.assertAlmostEqual(u, 100 / 999)
        self.assertAlmostEqual(v, 49 / 99)

    def test_planar_xz_is_unchanged(self):
        bounds = ((0.0, 0.0, 0.0), (2.0, 1.0, 4.0))
        self.assertEqual(projection_uv.base_projection_uv((2.0, 0.0, 0.0), bounds, 2), (1.0, 0.0))


class RasterTests(unittest.TestCase):
    def test_vertical_fov_frames_the_picture_height(self):
        # A 2-unit quad at distance 1/tan(fov/2) exactly fills the image height.
        fov = 0.8
        camera = rc.Camera(np.array([0, 0, 1 / math.tan(fov / 2)]), np.zeros(3), fov)
        colour, covered, _part = rc.rasterize(_quad(0.0, (1, 0, 0), 0), {}, camera, 40, 20)
        rows = np.nonzero(covered.any(axis=1))[0]
        self.assertEqual((rows.min(), rows.max()), (0, 19))
        columns = np.nonzero(covered.any(axis=0))[0]
        self.assertEqual((columns.min(), columns.max()), (10, 29))

    def test_zbuffer_keeps_the_nearer_part(self):
        near, far = _quad(0.5, (0, 1, 0), 1), _quad(0.0, (1, 0, 0), 0)
        both = rc.Triangles(
            np.concatenate([far.positions, near.positions]),
            np.concatenate([far.uvs, near.uvs]),
            np.concatenate([far.colour, near.colour]),
            far.texture + near.texture,
            np.concatenate([far.part, near.part]),
            np.concatenate([far.normals, near.normals]),
            ["far", "near"],
        )
        camera = rc.Camera(np.array([0, 0, 5.0]), np.zeros(3), 0.8)
        colour, covered, part = rc.rasterize(both, {}, camera, 16, 16)
        self.assertEqual(part[8, 8], 1)
        np.testing.assert_allclose(colour[8, 8], (0, 1, 0))

    def test_texture_lookup_is_bottom_left(self):
        image = np.zeros((2, 2, 3))
        image[0] = (1, 0, 0)  # top row red
        image[1] = (0, 0, 1)  # bottom row blue
        camera = rc.Camera(np.array([0, 0, 1 / math.tan(0.4)]), np.zeros(3), 0.8)
        colour, _covered, _part = rc.rasterize(_quad(0.0, (1, 1, 1), 0, "t"), {"t": image}, camera, 20, 20)
        np.testing.assert_allclose(colour[2, 10], (1, 0, 0))  # screen top = picture top (v=1)
        np.testing.assert_allclose(colour[17, 10], (0, 0, 1))

    def test_lighting_is_ambient_plus_lambert(self):
        camera = rc.Camera(np.array([0, 0, 5.0]), np.zeros(3), 0.8)
        light = rc.Light(np.array([1.0, 0.5, 0.0]), np.array([0, 0, 100.0]))
        colour, _covered, _part = rc.rasterize(
            _quad(0.0, (0.5, 0.5, 0.5), 0), {}, camera, 16, 16, lighting=(np.array([0.2, 0.2, 0.2]), [light])
        )
        np.testing.assert_allclose(colour[8, 8], 0.5 * np.array([1.2, 0.7, 0.2]), atol=1e-3)


class ScoreTests(unittest.TestCase):
    def test_gain_fit_absorbs_uniform_lighting(self):
        rng = np.random.default_rng(0)
        ours = rng.uniform(0.1, 1, (20, 20, 3))
        reference = np.concatenate([ours * 0.6, np.ones((20, 20, 1))], axis=2)
        covered = np.ones((20, 20), bool)
        result = rc.score(reference, ours, covered, np.zeros((20, 20), np.int32), ["a"])
        self.assertLess(result["gain_fit_error"], 1e-6)
        self.assertAlmostEqual(result["parts"][0]["gain"], 0.6, places=5)
        self.assertGreater(result["edge_alignment"], 0.99)

    def test_reference_candidates_follow_sts_output(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            (bundle / "scene.render_state.json").write_text(
                json.dumps({"output_file": "//Server/Battlezone/modelsdirectory/NewTank/NewTank/RENDER_PICTURES/TANK", "rendering_frame": [1, 2, 1]})
            )
            self.assertEqual(
                rc.reference_candidates(bundle),
                ["NewTank/NewTank/RENDER_PICTURES/TANK.1.pic", "NewTank/NewTank/RENDER_PICTURES/TANK.2.pic"],
            )


if __name__ == "__main__":
    unittest.main()


class GltfTwinTests(unittest.TestCase):
    def test_matrix_rows_and_v_flip(self):
        import json
        import tempfile

        sys.path.insert(0, str(ROOT / "tools" / "io_scene_bz2xsi"))
        import bz2xsi
        import bz2_xsi_to_gltf

        xsi = bz2xsi.XSI()
        frame = xsi.add_frame("root")
        frame.transform = bz2xsi.Matrix(posit=(1.0, 2.0, 3.0, 1.0))
        mesh = bz2xsi.Mesh("root")
        mesh.vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        mesh.faces = [(0, 1, 2)]
        mesh.uv_vertices = [(0.0, 0.25), (1.0, 0.25), (0.0, 1.0)]
        mesh.uv_faces = [(0, 1, 2)]
        mesh.face_materials = [bz2xsi.Material(shading_type=0)]
        frame.mesh = mesh
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "t.gltf"
            report = bz2_xsi_to_gltf.convert(xsi, Path(temp), out)
            gltf = json.loads(out.read_text())
            self.assertEqual(gltf["nodes"][0]["matrix"][12:15], [1.0, 2.0, 3.0])
            accessor = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]]
            view = gltf["bufferViews"][accessor["bufferView"]]
            data = np.frombuffer((Path(temp) / "t.bin").read_bytes(), np.float32, count=6, offset=view["byteOffset"]).reshape(3, 2)
            np.testing.assert_allclose(data[:, 1], [0.75, 0.75, 0.0])
            self.assertIn("KHR_materials_unlit", gltf["extensionsUsed"])
            self.assertEqual(report["winding_reversed_meshes"], [])


class HiddenNodeOverrideTests(unittest.TestCase):
    def test_curated_entries_are_scoped_to_their_scene(self):
        import bz2_xsi_export

        self.assertEqual(bz2_xsi_export.render_hidden_models("hires-tank2.17-0"), {"Main_tank-gun.4-0"})
        self.assertEqual(bz2_xsi_export.render_hidden_models("hires-tank.3-0"), set())


class RetailPictureSupplementTests(unittest.TestCase):
    def test_archive_first_then_retail_ranked_data_before_smtex(self):
        import tempfile

        import bz2_texture_layers_gltf as layers

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for member in ("smtex/ivdrop00.pic", "data/objects/ivdrop/ivdrop00.pic", "data/objects/ivdrop/ivdrop00.tga"):
                (root / member).parent.mkdir(parents=True, exist_ok=True)
                (root / member).write_bytes(b"x")
            saved = (layers.RETAIL_PICTURES_ROOT, layers._RETAIL_INDEX)
            layers.RETAIL_PICTURES_ROOT, layers._RETAIL_INDEX = root, None
            try:
                store, member = layers.resolve_retail_picture("//SERVER/Battlezone/modelsdirectory/dropship/PICTURES/ivdrop00")
                self.assertEqual(member, "data/objects/ivdrop/ivdrop00.pic")
                self.assertIsNone(layers.resolve_retail_picture("//SERVER/x/PICTURES/not_shipped"))
            finally:
                layers.RETAIL_PICTURES_ROOT, layers._RETAIL_INDEX = saved

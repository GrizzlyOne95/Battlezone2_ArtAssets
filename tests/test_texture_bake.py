import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import bz2_texture_bake as bake  # noqa: E402


class TextureBakeTests(unittest.TestCase):
    def _quad(self):
        corners = np.array(
            [[[0, 0, 0], [1, 0, 0], [1, 1, 0]], [[0, 0, 0], [1, 1, 0], [0, 1, 0]], [[0, 0, 0], [0, 0, 1], [1, 0, 1]]],
            dtype=float,
        )
        uv = np.array([[[0, 0], [1, 0], [1, 1]], [[0, 0], [1, 1], [0, 1]], [[0, 0], [0, 1], [1, 1]]], dtype=float)
        return corners, uv

    def test_charts_split_on_facing(self):
        corners, _uv = self._quad()
        charts = bake.build_charts(corners)
        self.assertEqual(sorted(len(chart) for chart in charts), [1, 2])

    def test_overlay_composites_over_base_and_uvs_stay_in_atlas(self):
        corners, uv = self._quad()
        red = np.zeros((4, 4, 4))
        red[...] = (1.0, 0.0, 0.0, 1.0)
        overlay = np.zeros((4, 4, 4))
        overlay[:, :2] = (0.0, 0.0, 1.0, 1.0)  # left half opaque blue, right half transparent (alpha mask)
        layers = [bake.Layer(red, uv), bake.Layer(overlay, uv, blending_type=1)]
        image, atlas_uv = bake.bake(corners, layers, (0.2, 0.2, 0.2))
        self.assertTrue(np.all((atlas_uv >= 0.0) & (atlas_uv <= 1.0)))
        colours = {tuple(pixel) for pixel in image.reshape(-1, 3)}
        self.assertIn((255, 0, 0), colours)
        self.assertIn((0, 0, 255), colours)

    def test_intensity_mask_keeps_material_where_dark(self):
        texel = np.array([[0.0, 0.0, 0.0, 1.0], [1.0, 1.0, 1.0, 1.0]])
        layer = bake.Layer(np.zeros((1, 1, 4)), np.zeros((1, 3, 2)), blending_type=2)
        out = bake.composite((0.2, 0.4, 0.6), [texel], [layer])
        np.testing.assert_allclose(out, [[0.2, 0.4, 0.6], [1.0, 1.0, 1.0]])


if __name__ == "__main__":
    unittest.main()

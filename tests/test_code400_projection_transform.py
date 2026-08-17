from __future__ import annotations
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import bz2_projection_uv as uv

class Code400ProjectionTransformTests(unittest.TestCase):
    def test_inverse_support_rotation_matches_face39_axis(self):
        p={
            "relation_code":400,
            "projection_or_mapping_code_candidate":2,
            "si_texture2d_matrix_rotation_xyz_radians":[-0.5000020265579224,0.0,-0.6780027747154236],
            "si_texture2d_matrix_scale_xyz":[1,1,1],
            "si_texture2d_matrix_translation_xyz":[0,0,0],
        }
        y=uv.projection_space_point((0,1,0),p)
        expected=(-0.627239,0.683484,0.373391)
        for actual,want in zip(y,expected):
            self.assertAlmostEqual(actual,want,places=5)

    def test_nonidentity_code401_remains_deferred(self):
        p={"relation_code":401,"projection_or_mapping_code_candidate":2,
           "si_texture2d_matrix_rotation_xyz_radians":[0,0.5,0],
           "si_texture2d_matrix_scale_xyz":[1,1,1],
           "si_texture2d_matrix_translation_xyz":[0,0,0]}
        self.assertFalse(uv.code400_rotation_supported(p))
        with self.assertRaises(ValueError):
            uv.project_polygon([(0,0,0)],((-1,-1,-1),(1,1,1)),p)

    def test_nonunit_code400_scale_remains_deferred(self):
        p={"relation_code":400,"si_texture2d_matrix_rotation_xyz_radians":[0,0,0],
           "si_texture2d_matrix_scale_xyz":[1.1,1,1],
           "si_texture2d_matrix_translation_xyz":[0,0,0]}
        self.assertFalse(uv.code400_rotation_supported(p))

if __name__=='__main__': unittest.main()

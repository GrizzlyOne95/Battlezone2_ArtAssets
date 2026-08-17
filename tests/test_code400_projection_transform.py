from __future__ import annotations
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import bz2_projection_uv as uv

class Code400ProjectionTransformTests(unittest.TestCase):
    def test_serialized_rotation_maps_face39_normal_to_planar_axis(self):
        p={
            "relation_code":400,
            "projection_or_mapping_code_candidate":2,
            "si_texture2d_matrix_rotation_xyz_radians":[-0.5000020265579224,0.0,-0.6780027747154236],
            "si_texture2d_matrix_scale_xyz":[1,1,1],
            "si_texture2d_matrix_translation_xyz":[0,0,0],
        }
        normal=(-0.7183021974407146,0.6008504122768953,0.350743118561898)
        projected=uv.projection_space_point(normal,p)
        self.assertGreater(abs(projected[1]),0.99)
        self.assertLess(abs(projected[0]),0.13)
        self.assertLess(abs(projected[2]),0.03)

    def test_prepared_support_bounds_use_actual_transformed_mesh(self):
        p={
            "relation_code":400,
            "projection_or_mapping_code_candidate":2,
            "si_texture2d_matrix_rotation_xyz_radians":[0.0,0.0,0.7],
            "si_texture2d_matrix_scale_xyz":[1,1,1],
            "si_texture2d_matrix_translation_xyz":[0,0,0],
        }
        points=[(0,0,0),(4,0,0),(0,1,0)]
        prepared,bounds=uv.prepare_projection_points(points,p)
        self.assertEqual(len(prepared),3)
        self.assertEqual(bounds,uv.bounds_from_points(prepared))
        original=uv.bounds_from_points(points)
        rotated_aabb=uv.projection_space_bounds(original,p)
        self.assertNotEqual(bounds,rotated_aabb)

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

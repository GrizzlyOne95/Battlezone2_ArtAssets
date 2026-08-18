from __future__ import annotations
import math
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import bz2_projection_uv as uv

class Code401CurrentUVEffectsTests(unittest.TestCase):
    def test_stasis_pi_y_mirrors_u_without_mutating_input(self):
        p={
            "relation_code":401,
            "si_texture2d_matrix_rotation_xyz_radians":[0.0,math.pi,0.0],
            "si_texture2d_matrix_scale_xyz":[1,1,1],
            "si_texture2d_matrix_translation_xyz":[0,0,0],
            "si_texture2d_repeat_uv":[1,1],
            "si_texture2d_uv_scale":[1,1],
            "si_texture2d_uv_offset":[0,0],
        }
        source=(0.25,0.75,0.0)
        out=uv.apply_current_uv_effects(source,p)
        self.assertAlmostEqual(out[0],-0.25,places=6)
        self.assertAlmostEqual(out[1],0.75,places=6)
        self.assertEqual(source,(0.25,0.75,0.0))

    def test_rotation_then_repeat_scale_offset(self):
        p={
            "relation_code":401,
            "si_texture2d_matrix_rotation_xyz_radians":[0,0,math.pi/2],
            "si_texture2d_matrix_scale_xyz":[1,1,1],
            "si_texture2d_matrix_translation_xyz":[0,0,0],
            "si_texture2d_repeat_uv":[2,3],
            "si_texture2d_uv_scale":[0.5,2],
            "si_texture2d_uv_offset":[0.1,-0.2],
        }
        u,v,w=uv.apply_current_uv_effects((1,0,0),p)
        self.assertAlmostEqual(u,0.1,places=6)
        self.assertAlmostEqual(v,5.8,places=6)
        self.assertAlmostEqual(w,0,places=6)

    def test_unobserved_matrix_scale_is_rejected(self):
        p={
            "relation_code":401,
            "si_texture2d_matrix_rotation_xyz_radians":[0,0.5,0],
            "si_texture2d_matrix_scale_xyz":[1.1,1,1],
            "si_texture2d_matrix_translation_xyz":[0,0,0],
        }
        with self.assertRaises(ValueError):
            uv.apply_current_uv_effects((0.2,0.3,0),p)

if __name__=='__main__': unittest.main()

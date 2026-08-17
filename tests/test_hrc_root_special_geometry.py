from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bz2_hrc_root_special_geometry as special


class Class1RootGeometryTests(unittest.TestCase):
    def test_type3_open_patch_uses_cubic_bspline_steps(self):
        u_count, v_count = 5, 4
        controls = []
        for u in range(u_count):
            for v in range(v_count):
                # Deliberately include an extreme control point. A B-spline is a
                # weighted approximation and should not simply emit the cage.
                z = -10.0 if (u, v) == (2, 3) else 0.25 * u + 0.1 * v
                controls.append((float(u), float(v), z))
        payload = bytearray(struct.pack(">HHH", 3, u_count, v_count))
        for point in controls:
            payload.extend(struct.pack(">fff", *point))
        payload.extend(struct.pack(">HHffHHHH", 0, 0, 0.5, 0.5, 3, 3, 1, 1))

        result = special._decode_class1_grid(
            bytes(payload), {"class_id": 1, "payload_offset": 0}
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["primitive_kind"], 3)
        self.assertEqual(result["evaluator"], "uniform_cubic_bspline")
        self.assertEqual(result["sample_u_count"], 7)  # (5 - 3) * step3 + 1
        self.assertEqual(result["sample_v_count"], 4)  # (4 - 3) * step3 + 1
        self.assertEqual(len(result["vertices"]), 28)
        self.assertEqual(len(result["indices"]) // 3, 36)
        self.assertGreater(min(point[2] for point in result["vertices"]), -10.0)

    def test_type2_open_patch_uses_cardinal_tangent_controls(self):
        u_count, v_count = 5, 4
        controls = []
        for u in range(u_count):
            for v in range(v_count):
                controls.append((float(u), float(v), float(10 * u + v)))
        payload = bytearray(struct.pack(">HHH", 2, u_count, v_count))
        for point in controls:
            payload.extend(struct.pack(">fff", *point))
        payload.extend(struct.pack(">HHffHHHH", 0, 0, 0.0, 0.0, 2, 2, 1, 1))

        result = special._decode_class1_grid(
            bytes(payload), {"class_id": 1, "payload_offset": 0}
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["evaluator"], "zero_tension_cardinal")
        self.assertEqual(result["sample_u_count"], 5)  # (5 - 3) * 2 + 1
        self.assertEqual(result["sample_v_count"], 3)  # (4 - 3) * 2 + 1
        self.assertEqual(len(result["vertices"]), 15)
        self.assertEqual(len(result["indices"]) // 3, 16)
        self.assertEqual(result["vertices"][0], controls[1 * v_count + 1])
        self.assertEqual(result["vertices"][-1], controls[3 * v_count + 2])

    def test_type2_closed_direction_is_periodic_without_duplicate_seam(self):
        u_count, v_count = 5, 4
        controls = []
        for u in range(u_count):
            for v in range(v_count):
                controls.append((float(u), float(v), float(v * v)))
        payload = bytearray(struct.pack(">HHH", 2, u_count, v_count))
        for point in controls:
            payload.extend(struct.pack(">fff", *point))
        payload.extend(struct.pack(">HHffHHHH", 0, 1, 0.0, 0.0, 1, 1, 1, 1))

        result = special._decode_class1_grid(
            bytes(payload), {"class_id": 1, "payload_offset": 0}
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["sample_u_count"], 3)
        self.assertEqual(result["sample_v_count"], 4)
        self.assertEqual(len(result["vertices"]), 12)
        # Two open-U cell rows x four periodic V cells x two triangles.
        self.assertEqual(len(result["indices"]) // 3, 16)
        self.assertEqual(result["vertices"][0], controls[1 * v_count + 0])


if __name__ == "__main__":
    unittest.main()

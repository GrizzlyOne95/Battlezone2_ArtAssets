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

    def test_type2_control_cage_behavior_remains_available(self):
        payload = bytearray(struct.pack(">HHH", 2, 2, 2))
        for point in ((0, 0, 0), (0, 1, 0), (1, 0, 0), (1, 1, 0)):
            payload.extend(struct.pack(">fff", *point))
        result = special._decode_class1_grid(
            bytes(payload), {"class_id": 1, "payload_offset": 0}
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["evaluator"], "control_cage")
        self.assertEqual(len(result["vertices"]), 4)
        self.assertEqual(len(result["indices"]) // 3, 2)


if __name__ == "__main__":
    unittest.main()

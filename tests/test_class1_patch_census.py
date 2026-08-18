from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bz2_class1_patch_census as class1


class Class1PatchLayoutTests(unittest.TestCase):
    def test_tag_section_terminator_and_srt_boundary(self):
        u_count, v_count = 2, 2
        points = [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (1.0, 1.0, 0.0),
        ]
        payload = bytearray(struct.pack(">HHH", 3, u_count, v_count))
        for point in points:
            payload.extend(struct.pack(">fff", *point))
        payload.extend(struct.pack(">HHffHHHH", 0, 0, 0.5, 0.5, 3, 3, 1, 1))
        payload.extend(b"\x00" * 8)
        payload.extend(struct.pack(">IHfffHHH", 1, 0, 5.0, 0.1, 45.0, 1, 10, 0))
        payload.extend(struct.pack(">4H", 0x8000, 0, 0x8000, 0))
        payload.extend(struct.pack(">H", 0))
        expected_srt = (1.0, 2.0, 3.0, 0.1, 0.2, 0.3, 4.0, 5.0, 6.0)
        payload.extend(struct.pack(">9f", *expected_srt))

        decoded = class1.decode_class1_payload(bytes(payload), 0)
        self.assertEqual(decoded["surface_type_code"], 3)
        self.assertEqual(decoded["control_point_count"], 4)
        self.assertEqual(decoded["nonzero_tag_count"], 2)
        self.assertEqual(decoded["tag_values"], [0x8000, 0, 0x8000, 0])
        self.assertEqual(decoded["tag_terminator"], 0)
        self.assertEqual(decoded["recursion"], 0)
        for actual, expected in zip(decoded["srt"], expected_srt):
            self.assertAlmostEqual(actual, expected, places=5)


if __name__ == "__main__":
    unittest.main()

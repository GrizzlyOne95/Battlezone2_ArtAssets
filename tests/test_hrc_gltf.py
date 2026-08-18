from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bz2_hrc_gltf as hrc_gltf


class HrcGltfSrtTests(unittest.TestCase):
    def test_overlapping_material_signature_does_not_hide_real_srt_anchor(self):
        # Real archive regression: the low bytes of a negative translation float
        # can look like a short material-slot record beginning five bytes before
        # the genuine marker. A consuming regex skips the true marker because the
        # two signatures overlap by one byte. Lookahead enumeration must see both.
        values = (
            1.0,
            1.0,
            1.0,
            0.0,
            0.0,
            0.0,
            4.0275250512422645e-7,
            0.0,
            -8.000076293945312,
        )
        data = struct.pack(">9f", *values) + b"\x00\x02\x00\x00Default113\x00"
        recovered = hrc_gltf._slot_material_srt(data, 0, len(data))
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered["offset"], 0)
        self.assertEqual(recovered["anchor_slot"], 2)
        self.assertEqual(recovered["anchor_name"], "Default113")
        self.assertAlmostEqual(recovered["translation_xyz"][2], values[8], places=5)


if __name__ == "__main__":
    unittest.main()

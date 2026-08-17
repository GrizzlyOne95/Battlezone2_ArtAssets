from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bz2_hrc_tree_probe as probe


class MeshSrtTailTests(unittest.TestCase):
    def test_standard_tail_with_zero_padding_recovers_srt(self):
        values = (1.0, 1.0, 1.0, 0.12, 0.0, 0.0, 0.0, 1.25, 6.75)
        data = struct.pack(">9f", *values) + probe.MESH_STANDARD_TAIL + (b"\0" * 28)
        decoded = probe._decode_mesh_srt_between(data, 0, len(data), 0)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["source"], "pre_mesh_standard_tail_zero_padded")
        self.assertAlmostEqual(decoded["translation_xyz"][1], 1.25)
        self.assertAlmostEqual(decoded["translation_xyz"][2], 6.75)

    def test_nonzero_bytes_after_tail_are_not_accepted(self):
        values = (1.0, 1.0, 1.0, 0.12, 0.0, 0.0, 0.0, 1.25, 6.75)
        data = struct.pack(">9f", *values) + probe.MESH_STANDARD_TAIL + b"\0\0\x01\0"
        self.assertIsNone(probe._decode_mesh_srt_between(data, 0, len(data), 0))


if __name__ == "__main__":
    unittest.main()

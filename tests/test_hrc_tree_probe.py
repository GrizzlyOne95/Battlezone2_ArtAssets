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

    def test_standard_tail_variant_6_recovers_srt(self):
        values = (1.0, 1.0, 1.0, 0.0, 0.0, 0.0, -0.25, -0.75, 1.8)
        data = struct.pack(">9f", *values) + probe.MESH_STANDARD_TAIL_VARIANT_6
        decoded = probe._decode_mesh_srt_between(data, 0, len(data), 0)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["source"], "pre_mesh_standard_tail_variant_6")
        self.assertAlmostEqual(decoded["translation_xyz"][2], 1.8)

    def test_nonzero_bytes_after_tail_are_not_accepted(self):
        values = (1.0, 1.0, 1.0, 0.12, 0.0, 0.0, 0.0, 1.25, 6.75)
        data = struct.pack(">9f", *values) + probe.MESH_STANDARD_TAIL + b"\0\0\x01\0"
        self.assertIsNone(probe._decode_mesh_srt_between(data, 0, len(data), 0))

    def test_nonunit_material_slot_recovers_srt_in_shared_probe(self):
        values = (1.0, 1.0, 1.0, 0.0, 0.0, 0.0, -2.0, 3.0, 4.0)
        data = struct.pack(">9f", *values) + b"\x00\x02\x00\x00mat2\x00"
        decoded = probe._decode_mesh_srt_between(data, 0, len(data), 0)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["source"], "pre_mesh_material_slot_block")
        self.assertEqual(decoded["anchor_slot"], 2)
        self.assertEqual(decoded["anchor_name"], "mat2")

    def test_zero_unit_standard_tail_recovers_artillery_mirror_srt(self):
        values = (1.0, 1.0, 1.0, 0.0, 0.0, 0.0, -1.397568, 1.378649, -2.727489)
        data = struct.pack(">9f", *values) + probe.MESH_STANDARD_TAIL_ZERO_UNIT
        decoded = probe._decode_mesh_srt_between(data, 0, len(data), 0)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["source"], "pre_mesh_standard_tail_zero_unit")
        self.assertAlmostEqual(decoded["translation_xyz"][0], -1.397568, places=5)

    def test_mire_extended_tail_with_zero_padding_recovers_srt(self):
        values = (1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        data = struct.pack(">9f", *values) + probe.MESH_MIRE_GRID_EXTENDED_TAIL + (b"\0" * 24)
        decoded = probe._decode_mesh_srt_between(data, 0, len(data), 0)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["source"], "pre_mesh_mire_grid_extended_tail_zero_padded")

    def test_cusa_custom_attribute_anchor_recovers_srt(self):
        values = (0.996, 0.996, 0.996, 0.0, 0.0, 0.0, 0.0, 0.6020126, 0.0)
        preamble = probe.CUSA_PREAMBLE_PREFIX + b"\x02"
        data = struct.pack(">9f", *values) + preamble + b"CUSA" + (b"\0" * 64)
        decoded = probe._decode_mesh_srt_between(data, 0, len(data), 0)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["source"], "pre_custom_attribute_cusa")
        self.assertAlmostEqual(decoded["scale"][0], 0.996, places=5)
        self.assertAlmostEqual(decoded["translation_xyz"][1], 0.6020126, places=5)

    def test_class0_nonzero_subtype_is_not_a_hierarchy_record(self):
        # Real archive regressions use class-0/nonzero internal records named
        # cls0, Face, and t. Only subtype 0 is a transform/null model node.
        real = (b"\0" * 20) + b"\0\x01real\0" + b"\x00\x00\x00\x00" + (b"\0" * 36)
        helper = (b"\0" * 22) + b"\0\x01helper\0" + b"\x00\x00\x00\x01" + (b"\0" * 36)
        records = probe.discover_records(real + helper)
        self.assertEqual([item["name"] for item in records], ["real"])
        self.assertEqual(records[0]["class_id"], 0)
        self.assertEqual(records[0]["subtype"], 0)


if __name__ == "__main__":
    unittest.main()

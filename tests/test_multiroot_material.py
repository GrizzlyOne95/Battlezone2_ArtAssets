from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bz2_dsc_multiroot_material_gltf as materials


class UnboundSourceMaterialTests(unittest.TestCase):
    def test_slot_zero_without_authored_material_only(self):
        self.assertTrue(materials._is_explicitly_unbound_source_mesh([], [0]))
        self.assertTrue(materials._is_explicitly_unbound_source_mesh([], [0, 0]))
        self.assertFalse(materials._is_explicitly_unbound_source_mesh([{}], [0]))
        self.assertFalse(materials._is_explicitly_unbound_source_mesh([], [1]))
        self.assertFalse(materials._is_explicitly_unbound_source_mesh([], [0, 1]))
        self.assertFalse(materials._is_explicitly_unbound_source_mesh([], []))


if __name__ == "__main__":
    unittest.main()

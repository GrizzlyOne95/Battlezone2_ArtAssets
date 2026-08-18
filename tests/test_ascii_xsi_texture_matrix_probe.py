from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bz2_ascii_xsi_texture_matrix_probe as probe


class AsciiXsiTextureMatrixProbeTests(unittest.TestCase):
    def test_texture_blocks_keep_frame_mesh_context_and_matrix(self):
        text = '''xsi 0101txt 0032
Frame frm-left {
  Mesh left {
    SI_Texture2D {
      "left.pic";
      1,0,0,0,
      0,1,0,0,
      0,0,1,0,
      0,0,0,1;;
    }
  }
}
Frame frm-right {
  Mesh right {
    SI_Texture2D {
      "right.pic";
      -1,0,0,0,
      0,1,0,0,
      0,0,-1,0,
      0,0,0,1;;
    }
  }
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.xsi"
            path.write_text(text, encoding="latin-1")
            result = probe.probe(path)

        self.assertEqual(result["block_count"], 2)
        left, right = result["blocks"]
        self.assertEqual((left["frame"], left["mesh"]), ("frm-left", "left"))
        self.assertTrue(left["matrix_identity"])
        self.assertEqual((right["frame"], right["mesh"]), ("frm-right", "right"))
        self.assertFalse(right["matrix_identity"])
        self.assertEqual(right["matrix4x4"][0][0], -1.0)
        self.assertEqual(right["matrix4x4"][2][2], -1.0)


if __name__ == "__main__":
    unittest.main()

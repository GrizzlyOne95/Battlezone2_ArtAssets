from __future__ import annotations
import unittest

class NurbsTextureUvProvenancePolicyTests(unittest.TestCase):
    def test_normalized_parameter_space_is_not_authored_current_uv(self):
        def usable(count, all_zero, provenance):
            return count > 0 and all_zero is False and provenance != "normalized_parameter_space"
        self.assertFalse(usable(1, False, "normalized_parameter_space"))
        self.assertTrue(usable(1, False, "source_polygon_uv"))
        self.assertFalse(usable(1, True, "source_polygon_uv"))

if __name__=='__main__': unittest.main()

from __future__ import annotations
import unittest

class Code401SourceUVPolicyTests(unittest.TestCase):
    def test_policy_truth_table(self):
        def source_usable(count, all_zero):
            return count > 0 and all_zero is False
        self.assertTrue(source_usable(1, False))
        self.assertFalse(source_usable(1, True))
        self.assertFalse(source_usable(0, None))

    def test_generation_is_only_for_missing_or_zero_uv_identity_supported_projection(self):
        def generate(source_usable, projection_supported, matrix_identity):
            return (not source_usable) and projection_supported and matrix_identity
        self.assertFalse(generate(True, True, True))
        self.assertTrue(generate(False, True, True))
        self.assertFalse(generate(False, True, False))
        self.assertFalse(generate(False, False, True))

if __name__=='__main__': unittest.main()

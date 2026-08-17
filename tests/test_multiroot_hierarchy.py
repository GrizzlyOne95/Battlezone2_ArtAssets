from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bz2_dsc_multiroot_gltf as multiroot


class DscHierarchyBaselineTests(unittest.TestCase):
    def setUp(self):
        self.models = [
            {"name": "asset-hp_dummyroot.1-0", "root": True},
            {"name": "asset-cube23__2g.2-0", "root": False},
            {"name": "asset-obj1__2g1.1-0", "root": False},
        ]
        self.parents = {1: 0, 2: 1}

    def test_dsc_score_prefers_chain_over_flattened_tree(self):
        flat = {
            "chosen_baseline": 24,
            "tree": [
                {"name": "cube23__2g", "depth": 1},
                {"name": "obj1__2g1", "depth": 1},
            ],
        }
        chain = {
            "chosen_baseline": 26,
            "tree": [
                {"name": "cube23__2g", "depth": 1},
                {"name": "obj1__2g1", "depth": 2},
            ],
        }
        flat_score = multiroot._score_hrc_tree_against_dsc(
            flat, self.models, self.parents, 0
        )
        chain_score = multiroot._score_hrc_tree_against_dsc(
            chain, self.models, self.parents, 0
        )
        self.assertEqual(flat_score["mismatch_count"], 1)
        self.assertEqual(chain_score["match_count"], 2)
        chosen, status = multiroot._choose_scored_baseline(
            [flat_score, chain_score], 24
        )
        self.assertEqual(chosen, 26)
        self.assertEqual(status, "dsc_code110_unique_improvement")

    def test_equal_scores_keep_standalone_default(self):
        a = {
            "baseline": 20,
            "violation_count": 0,
            "match_count": 1,
            "constraint_count": 1,
            "mapped_model_count": 2,
        }
        b = dict(a, baseline=22)
        chosen, status = multiroot._choose_scored_baseline([a, b], 20)
        self.assertEqual(chosen, 20)
        self.assertEqual(status, "ambiguous_dsc_score_keep_default")


if __name__ == "__main__":
    unittest.main()

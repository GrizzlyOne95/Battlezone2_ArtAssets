from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import bz2_reconstruct_scene as reconstruct

class SourcePictureWarningTests(unittest.TestCase):
    def test_missing_pictures_are_aggregated_as_source_warnings(self):
        layers = {'unresolved_picture_count': 2, 'unresolved_pictures': [{'raw_source_path': '//server/a'}, {'raw_source_path': '//server/b'}]}
        projections = {'unresolved_picture_count': 1, 'unresolved_pictures': [{'raw_source_path': '//server/c'}]}
        warnings = reconstruct._source_picture_warnings(layers, projections)
        self.assertEqual(sum(item['count'] for item in warnings), 3)
        self.assertEqual([item['kind'] for item in warnings], ['missing_material_picture_sources', 'missing_model_projection_picture_sources'])

    def test_no_missing_pictures_produces_no_warning(self):
        self.assertEqual(reconstruct._source_picture_warnings({'unresolved_picture_count': 0}, {'unresolved_picture_count': 0}), [])

if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Apply the corpus-proven class-0/subtype-0 hierarchy distinction."""
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"patch anchor missing in {path}: {old[:160]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "scripts/bz2_hrc_tree_probe.py",
    '        if class_id not in KNOWN_CLASSES or zeros < 20 or zeros % 2:\n            continue\n        item = {\n',
    '        if class_id not in KNOWN_CLASSES or zeros < 20 or zeros % 2:\n            continue\n'
    '        # Archive census: class 0 is a hierarchy transform/null only for\n'
    '        # subtype 0. The 13 class-0/nonzero signatures across 7,665 HRCs\n'
    '        # are internal/helper payload records (cls0, Face, t); treating them\n'
    '        # as nodes creates garbage immediate SRTs and false parent scopes.\n'
    '        if class_id == 0 and subtype != 0:\n'
    '            continue\n'
    '        item = {\n',
)

replace_once(
    "scripts/bz2_hrc_tree_probe.py",
    '    if outer and outer["class_id"] in {0, 5}:\n',
    '    if outer and (outer["class_id"] == 5 or (outer["class_id"] == 0 and outer.get("subtype") == 0)):\n',
)

replace_once(
    "tests/test_hrc_tree_probe.py",
    '    def test_nonzero_bytes_after_tail_are_not_accepted(self):\n        values = (1.0, 1.0, 1.0, 0.12, 0.0, 0.0, 0.0, 1.25, 6.75)\n        data = struct.pack(">9f", *values) + probe.MESH_STANDARD_TAIL + b"\\0\\0\\x01\\0"\n        self.assertIsNone(probe._decode_mesh_srt_between(data, 0, len(data), 0))\n',
    '    def test_nonzero_bytes_after_tail_are_not_accepted(self):\n        values = (1.0, 1.0, 1.0, 0.12, 0.0, 0.0, 0.0, 1.25, 6.75)\n        data = struct.pack(">9f", *values) + probe.MESH_STANDARD_TAIL + b"\\0\\0\\x01\\0"\n        self.assertIsNone(probe._decode_mesh_srt_between(data, 0, len(data), 0))\n\n'
    '    def test_class0_nonzero_subtype_is_not_a_hierarchy_record(self):\n'
    '        # Real archive regressions use class-0/nonzero internal records named\n'
    '        # cls0, Face, and t. Only subtype 0 is a transform/null model node.\n'
    '        real = (b"\\0" * 20) + b"\\0\\x01real\\0" + b"\\x00\\x00\\x00\\x00" + (b"\\0" * 36)\n'
    '        helper = (b"\\0" * 22) + b"\\0\\x01helper\\0" + b"\\x00\\x00\\x00\\x01" + (b"\\0" * 36)\n'
    '        records = probe.discover_records(real + helper)\n'
    '        self.assertEqual([item["name"] for item in records], ["real"])\n'
    '        self.assertEqual(records[0]["class_id"], 0)\n'
    '        self.assertEqual(records[0]["subtype"], 0)\n',
)

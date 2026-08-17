#!/usr/bin/env python3
"""Apply archive-backed DSC ENVIRONMENT SRT grammar correction."""
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"patch anchor missing in {path}: {old[:180]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "scripts/bz2_dsc_multiroot_gltf.py",
    '''    for line in chapter.group(1).splitlines():\n        match = re.match(r"\\s*(\\d+)\\s+.*?\\bSRT\\s+([^;]+?)\\s+MPRFLG", line)\n        if not match:\n            continue\n        values = [float(value) for value in match.group(2).split()]\n        if len(values) >= 9:\n            result[int(match.group(1))] = values[:9]\n''',
    '''    for line in chapter.group(1).splitlines():\n        # Archive qualification found two valid ENVIRONMENT forms: some roots\n        # append MPRFLG after the nine SRT floats, while 439/3,815 roots end the\n        # line immediately after SRT. Parse the first nine SRT values rather than\n        # making an unrelated trailing field part of the transform grammar.\n        match = re.match(r"\\s*(\\d+)\\s+.*?\\bSRT\\s+([^;]+)", line)\n        if not match:\n            continue\n        tokens = match.group(2).split()\n        if len(tokens) < 9:\n            continue\n        try:\n            values = [float(value) for value in tokens[:9]]\n        except ValueError:\n            continue\n        result[int(match.group(1))] = values\n''',
)

p = Path("tests/test_multiroot_hierarchy.py")
text = p.read_text(encoding="utf-8")
if "import tempfile\n" not in text:
    text = text.replace("import sys\nimport unittest\n", "import sys\nimport tempfile\nimport unittest\n", 1)
anchor = "    def test_equal_scores_keep_standalone_default(self):\n"
if "test_environment_srt_accepts_with_and_without_mprflg" not in text:
    if anchor not in text:
        raise SystemExit("test insertion anchor missing")
    test = '''    def test_environment_srt_accepts_with_and_without_mprflg(self):\n        text = """ENVIRONMENT\nCHAPTER MODELS\n0 SCHEM 1 0 0 SRT 1 1 1 0 0 0 1.25 2.5 -3.75 ;\n1 SCHEM 1 0 0 SRT 2 2 2 0.1 0.2 0.3 4 5 6 MPRFLG 0 ;\nEndOfCHAPTER\nEndOfENVIRONMENT\n"""\n        with tempfile.TemporaryDirectory() as tmp:\n            scene = Path(tmp) / "scene.dsc"\n            scene.write_text(text, encoding="latin-1")\n            srts = multiroot._parse_environment_srts(scene)\n        self.assertEqual(srts[0], [1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.25, 2.5, -3.75])\n        self.assertEqual(srts[1], [2.0, 2.0, 2.0, 0.1, 0.2, 0.3, 4.0, 5.0, 6.0])\n\n'''
    text = text.replace(anchor, test + anchor, 1)
p.write_text(text, encoding="utf-8")

from pathlib import Path


def repl(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"anchor missing in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


repl(
    "scripts/bz2_hrc_gltf.py",
    "def export_hrc(source: Path, output: Path) -> dict:\n    data = source.read_bytes()\n    report = hrc_tree.probe(source)\n",
    "def export_hrc(source: Path, output: Path, *, baseline: int | None = None) -> dict:\n    data = source.read_bytes()\n    report = hrc_tree.probe(source, baseline)\n",
)
repl(
    "scripts/bz2_hrc_gltf.py",
    '        "node_count": len(nodes),\n',
    '        "node_count": len(nodes),\n        "hierarchy_baseline": report.get("chosen_baseline"),\n        "hierarchy_baseline_candidates": report.get("baseline_candidates", []),\n',
)

repl(
    "scripts/bz2_hrc_gltf_parametric.py",
    "def export_parametric(\n    source: Path,\n    output: Path,\n    *,\n    curve_steps: int = 64,\n",
    "def export_parametric(\n    source: Path,\n    output: Path,\n    *,\n    baseline: int | None = None,\n    curve_steps: int = 64,\n",
)
repl(
    "scripts/bz2_hrc_gltf_parametric.py",
    "    base_summary = assembled.export_hrc(source, output)\n",
    "    base_summary = assembled.export_hrc(source, output, baseline=baseline)\n",
)
repl(
    "scripts/bz2_hrc_gltf_parametric.py",
    "    tree_report = hrc_tree.probe(source)\n",
    "    tree_report = hrc_tree.probe(source, baseline)\n",
)
repl(
    "scripts/bz2_hrc_gltf_parametric.py",
    '        "settings": {\n            "curve_steps": curve_steps,\n',
    '        "settings": {\n            "hierarchy_baseline": tree_report.get("chosen_baseline"),\n            "curve_steps": curve_steps,\n',
)

repl(
    "scripts/bz2_dsc_multiroot_gltf.py",
    "import bz2_hrc_gltf as assembled\nimport bz2_hrc_gltf_parametric as parametric\n",
    "import bz2_hrc_gltf as assembled\nimport bz2_hrc_gltf_parametric as parametric\nimport bz2_hrc_tree_probe as hrc_tree\n",
)
repl(
    "scripts/bz2_dsc_multiroot_gltf.py",
    "def _subtree_members(models: list[dict], parents: dict[int, int], root_index: int) -> set[int]:\n",
    "def _model_matches_hrc_name(model_name: str, hrc_name: str) -> bool:\n    stem = _strip_version(model_name)\n    return stem == hrc_name or stem.endswith(\"-\" + hrc_name)\n\n\ndef _subtree_members(models: list[dict], parents: dict[int, int], root_index: int) -> set[int]:\n",
)
repl(
    "scripts/bz2_dsc_multiroot_gltf.py",
    '                stem = _strip_version(models[candidate]["name"])\n                if stem == node_name or stem.endswith("-" + node_name):\n                    candidates.append(candidate)\n',
    '                if _model_matches_hrc_name(models[candidate]["name"], node_name):\n                    candidates.append(candidate)\n',
)

helpers = r'''
def _score_hrc_tree_against_dsc(
    tree_report: dict,
    models: list[dict],
    parents: dict[int, int],
    root_index: int,
) -> dict:
    """Score one HRC hierarchy baseline against authoritative DSC code-110 edges.

    HRC zero-run encoding can admit multiple mathematically valid baselines. DSC
    relation code 110 independently serializes the scene-model parent graph, so
    scene reconstruction can use it to disambiguate only HRC nodes that map
    uniquely into this root's DSC subtree.
    """
    subtree = _subtree_members(models, parents, root_index)
    remaining = set(subtree) - {root_index}
    mapped_by_depth: dict[int, int | None] = {0: root_index}
    mapped_model_count = 1
    matches = mismatches = unresolved = constraints = 0
    details = []

    for item in tree_report.get("tree", []):
        depth = int(item.get("depth", 0))
        name = str(item.get("name") or "")
        candidates = [
            index
            for index in sorted(remaining)
            if _model_matches_hrc_name(models[index]["name"], name)
        ]
        model_index = candidates[0] if len(candidates) == 1 else None
        if model_index is not None:
            remaining.remove(model_index)
            mapped_model_count += 1

        # Replace the stack at this depth so a previous branch can never become
        # the observed parent of a later sibling/deeper branch.
        for stale_depth in [value for value in mapped_by_depth if value >= depth]:
            mapped_by_depth.pop(stale_depth, None)
        observed_parent_model = mapped_by_depth.get(depth - 1)
        mapped_by_depth[depth] = model_index

        if model_index is None or model_index not in parents:
            continue
        constraints += 1
        expected_parent_model = parents[model_index]
        if observed_parent_model is None:
            unresolved += 1
            status = "unresolved_hrc_parent"
        elif observed_parent_model == expected_parent_model:
            matches += 1
            status = "match"
        else:
            mismatches += 1
            status = "mismatch"
        details.append(
            {
                "child": models[model_index]["name"],
                "expected_parent": models[expected_parent_model]["name"],
                "observed_parent": (
                    models[observed_parent_model]["name"]
                    if observed_parent_model is not None
                    else None
                ),
                "status": status,
            }
        )

    return {
        "baseline": tree_report.get("chosen_baseline"),
        "max_depth": max(
            (int(item.get("depth", 0)) for item in tree_report.get("tree", [])),
            default=0,
        ),
        "mapped_model_count": mapped_model_count,
        "constraint_count": constraints,
        "match_count": matches,
        "mismatch_count": mismatches,
        "unresolved_parent_count": unresolved,
        "violation_count": mismatches + unresolved,
        "details": details,
    }


def _choose_scored_baseline(
    scores: list[dict], default_baseline: int | None
) -> tuple[int | None, str]:
    """Choose only a unique DSC-backed improvement; otherwise retain default."""
    if not scores or default_baseline is None:
        return default_baseline, "no_baseline_candidates"
    by_baseline = {
        int(item["baseline"]): item
        for item in scores
        if item.get("baseline") is not None
    }
    default = by_baseline.get(int(default_baseline))
    if default is None:
        return default_baseline, "default_baseline_unscored"

    def rank(item: dict) -> tuple[int, int, int, int]:
        return (
            int(item["violation_count"]),
            -int(item["match_count"]),
            -int(item["constraint_count"]),
            -int(item["mapped_model_count"]),
        )

    best_rank = min(rank(item) for item in scores)
    best = [item for item in scores if rank(item) == best_rank]
    if len(best) != 1:
        return default_baseline, "ambiguous_dsc_score_keep_default"
    chosen = int(best[0]["baseline"])
    if rank(best[0]) < rank(default):
        return chosen, "dsc_code110_unique_improvement"
    return default_baseline, "default_already_best"


def _select_hierarchy_baseline(
    source_hrc: Path,
    models: list[dict],
    parents: dict[int, int],
    root_index: int,
) -> tuple[int | None, dict]:
    # Decode the HRC record stream once, then replay only the inexpensive depth
    # walk for each valid candidate. This avoids re-decoding geometry per baseline.
    data = source_hrc.read_bytes()
    outer = hrc_tree.outer_model(data)
    records = hrc_tree.discover_records(data)
    candidates = [
        int(item["baseline_zero_run"])
        for item in hrc_tree.infer_baselines(records)
    ]
    default = candidates[0] if candidates else None
    scores = []
    if outer is not None:
        for baseline in candidates:
            tree = hrc_tree.apply_tree(records, str(outer["name"]), baseline)
            scores.append(
                _score_hrc_tree_against_dsc(
                    {"chosen_baseline": baseline, "tree": tree},
                    models,
                    parents,
                    root_index,
                )
            )
    chosen, status = _choose_scored_baseline(scores, default)
    return chosen, {
        "status": status,
        "default_baseline": default,
        "chosen_baseline": chosen,
        "candidate_scores": scores,
    }


'''
repl(
    "scripts/bz2_dsc_multiroot_gltf.py",
    "\ndef assemble_scene(\n",
    "\n" + helpers + "def assemble_scene(\n",
)

repl(
    "scripts/bz2_dsc_multiroot_gltf.py",
    '            source_hrc.write_bytes(store.read(member))\n            root_gltf = temp_dir / (model_name + ".gltf")\n            try:\n                if include_parametric:\n',
    '            source_hrc.write_bytes(store.read(member))\n            root_gltf = temp_dir / (model_name + ".gltf")\n            try:\n                hierarchy_baseline, hierarchy_selection = _select_hierarchy_baseline(\n                    source_hrc, models, parents, model_index\n                )\n                if include_parametric:\n',
)
repl(
    "scripts/bz2_dsc_multiroot_gltf.py",
    '                    root_summary = parametric.export_parametric(\n                        source_hrc,\n                        root_gltf,\n                        curve_steps=max(2, curve_steps),\n',
    '                    root_summary = parametric.export_parametric(\n                        source_hrc,\n                        root_gltf,\n                        baseline=hierarchy_baseline,\n                        curve_steps=max(2, curve_steps),\n',
)
repl(
    "scripts/bz2_dsc_multiroot_gltf.py",
    '                    base_summary = assembled.export_hrc(source_hrc, root_gltf)\n',
    '                    base_summary = assembled.export_hrc(\n                        source_hrc, root_gltf, baseline=hierarchy_baseline\n                    )\n',
)
repl(
    "scripts/bz2_dsc_multiroot_gltf.py",
    '                        "source_hrc": member,\n                        "gltf_root_node": root_node,\n',
    '                        "source_hrc": member,\n                        "gltf_root_node": root_node,\n                        "hierarchy_baseline": hierarchy_baseline,\n                        "hierarchy_baseline_selection": hierarchy_selection,\n',
)
repl(
    "scripts/bz2_dsc_multiroot_gltf.py",
    '            "DSC relation code 110 is the hierarchy oracle used to regression-check the merged HRC trees.",\n',
    '            "When an HRC admits multiple zero-run hierarchy baselines, DSC relation code 110 is used only as a context-specific tie-breaker; ambiguous/equivalent scores retain the standalone HRC default.",\n            "DSC relation code 110 remains the hierarchy oracle used to regression-check the merged HRC trees after baseline selection.",\n',
)

Path("tests/test_multiroot_hierarchy.py").write_text(
    '''from __future__ import annotations

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
''',
    encoding="utf-8",
)

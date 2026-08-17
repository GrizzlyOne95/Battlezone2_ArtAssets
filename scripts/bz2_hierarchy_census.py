#!/usr/bin/env python3
"""Census DSC-backed HRC hierarchy-baseline ambiguity across a BZ2 source tree.

This tool is deliberately source-read-only. It discovers every DSC scene (including
isolated historical scene ZIPs by default), resolves each declared ROOT HRC, scores
all mathematically valid zero-run baselines against DSC relation-code-110 parent
edges, and records where DSC uniquely improves, cannot disambiguate, or still
cannot satisfy the mapped hierarchy.

HRC record discovery is cached by source/member so large scene families that reuse
the same HRC do not repeatedly rescan identical binary data.
"""
from __future__ import annotations

import argparse
import collections
import json
import time
from pathlib import Path
from typing import Any

import bz2_dsc_material_gltf as dscmat
import bz2_dsc_multiroot_gltf as multi
import bz2_full_extract as full
import bz2_hrc_tree_probe as hrc_tree


def _candidate_trees(data: bytes) -> tuple[int | None, list[dict[str, Any]]]:
    outer = hrc_tree.outer_model(data)
    records = hrc_tree.discover_records(data)
    baselines = [int(item["baseline_zero_run"]) for item in hrc_tree.infer_baselines(records)]
    default = baselines[0] if baselines else None
    if outer is None:
        return default, []
    return default, [
        {
            "chosen_baseline": baseline,
            "tree": hrc_tree.apply_tree(records, str(outer["name"]), baseline),
        }
        for baseline in baselines
    ]


def census(source: Path, *, include_embedded_zips: bool = True) -> dict[str, Any]:
    started = time.time()
    status_counts: collections.Counter[str] = collections.Counter()
    summary: collections.Counter[str] = collections.Counter()
    remaining: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    no_constraints: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    roots_total = 0
    scene_total = 0

    with full.prepared_source(source) as (primary, source_info):
        with full.prepared_scene_sources(primary, include_embedded_zips=include_embedded_zips) as (_roots, scenes, sources):
            stores: dict[str, Any] = {}
            hrc_cache: dict[tuple[str, str], tuple[int | None, list[dict[str, Any]]]] = {}

            for scene in scenes:
                scene_total += 1
                try:
                    models = multi._parse_model_roots(scene.path)
                    _, relations = dscmat.parse_dsc(scene.path)
                    parents = {
                        int(relation["source_index"]): int(relation["target_index"])
                        for relation in relations
                        if relation["source_chapter"] == "MODELS"
                        and relation["target_chapter"] == "MODELS"
                        and relation["relation_code"] == multi.MODEL_PARENT_CODE
                    }
                    root_indices = [index for index, model in enumerate(models) if model["root"]]
                    source_key = str(scene.asset_source)
                    store = stores.get(source_key)
                    if store is None:
                        store = dscmat.open_store(scene.asset_source)
                        stores[source_key] = store

                    for root_index in root_indices:
                        roots_total += 1
                        model_name = models[root_index]["name"]
                        member = store.find_basename(model_name + ".hrc", f"{scene.prefix}/MODELS")
                        if not member:
                            missing.append(
                                {
                                    "selector": scene.selector,
                                    "root": model_name,
                                    "prefix": scene.prefix,
                                    "source_label": scene.source_label,
                                }
                            )
                            summary["missing_root"] += 1
                            continue

                        cache_key = source_key, str(member)
                        cached = hrc_cache.get(cache_key)
                        if cached is None:
                            cached = _candidate_trees(store.read(member))
                            hrc_cache[cache_key] = cached
                        default, trees = cached
                        scores = [
                            multi._score_hrc_tree_against_dsc(tree, models, parents, root_index)
                            for tree in trees
                        ]
                        chosen, status = multi._choose_scored_baseline(scores, default)
                        status_counts[status] += 1
                        if len(scores) > 1:
                            summary["multi_candidate"] += 1
                        else:
                            summary["single_candidate"] += 1

                        chosen_score = next((item for item in scores if item.get("baseline") == chosen), None)
                        best_violation = min((int(item["violation_count"]) for item in scores), default=None)
                        max_constraints = max((int(item["constraint_count"]) for item in scores), default=0)
                        common = {
                            "selector": scene.selector,
                            "root": model_name,
                            "member": str(member),
                            "source_label": scene.source_label,
                            "prefix": scene.prefix,
                            "default": default,
                            "chosen": chosen,
                        }

                        if max_constraints == 0:
                            no_constraints.append({**common, "candidate_count": len(scores)})
                        if status == "dsc_code110_unique_improvement":
                            improvements.append({**common, "chosen_score": chosen_score, "candidate_scores": scores})
                        if status == "ambiguous_dsc_score_keep_default":
                            ambiguous.append({**common, "candidate_scores": scores})
                        if best_violation is not None and max_constraints > 0 and best_violation > 0:
                            remaining.append(
                                {
                                    **common,
                                    "status": status,
                                    "best_violation_count": best_violation,
                                    "chosen_score": chosen_score,
                                    "candidate_scores": scores,
                                }
                            )
                except Exception as exc:
                    errors.append({"selector": scene.selector, "error": f"{type(exc).__name__}: {exc}"})
                    summary["scene_error"] += 1

    return {
        "schema": "bz2-hierarchy-census-v2",
        "seconds": round(time.time() - started, 3),
        "source": source_info,
        "scene_count": scene_total,
        "root_count": roots_total,
        "unique_hrc_count": len(hrc_cache),
        "discovered_sources": sources,
        "status_counts": dict(status_counts),
        "summary": dict(summary),
        "unique_improvement_count": len(improvements),
        "ambiguous_score_count": len(ambiguous),
        "no_dsc_constraint_count": len(no_constraints),
        "remaining_violation_root_count": len(remaining),
        "missing_root_count": len(missing),
        "error_count": len(errors),
        "remaining_violations": remaining,
        "unique_improvements": improvements,
        "ambiguous_scores": ambiguous,
        "no_constraints": no_constraints,
        "missing_roots": missing,
        "errors": errors,
        "notes": [
            "DSC code 110 is used only to select among already-valid HRC zero-run baselines; nodes are not post-hoc reparented.",
            "A non-default baseline is selected only when it is a unique strict improvement under the production scorer.",
            "HRC parsing is cached by source/member for census speed; scoring remains scene-specific because DSC model graphs can differ.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="modelsdirectory/tree, ZIP, or 7z source")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-embedded-zips", action="store_true")
    args = parser.parse_args()
    payload = census(args.source, include_embedded_zips=not args.no_embedded_zips)
    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if payload["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

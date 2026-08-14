#!/usr/bin/env python3
"""Assemble every DSC ROOT HRC into one complete static glTF scene graph.

Softimage DSC `MODELS ... ROOT` entries are scene-instance boundaries. Most
non-root MODELS entries are internal nodes already serialized inside those ROOT
HRC hierarchies, so exporting one arbitrarily selected 'master' HRC can omit
legitimate scene roots (guns, grids/projection supports, FX faces, etc.).

This exporter:

* resolves every DSC MODELS entry marked ROOT to a scene-local HRC;
* exports polygon + reconstruction-ready NURBS geometry for each root HRC;
* merges the root glTFs into one buffer/scene without duplicating transforms;
* treats DSC ENVIRONMENT SRT as authoritative for the root scene instance when
  an explicit SRT is present;
* maps every HRC node back to its DSC model entry within that root subtree;
* verifies the merged hierarchy against DSC MODELS->MODELS relation code 110.

Materials/textures, cameras/lights, render state, and model-local texture
projections remain independent follow-up layers and can operate on this output.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

import bz2_dsc_material_gltf as dscmat
import bz2_hrc_gltf as assembled
import bz2_hrc_gltf_parametric as parametric

VERSION_RE = re.compile(r"\.\d+-\d+$")
MODEL_PARENT_CODE = 110


def _strip_version(name: str) -> str:
    return VERSION_RE.sub("", name)


def _parse_model_roots(scene_dsc: Path) -> list[dict]:
    text = scene_dsc.read_text(encoding="latin-1", errors="replace")
    element_text = text.split("ELEMENTS", 1)[1].split("EndOfELEMENTS", 1)[0]
    match = re.search(
        r"CHAPTER\s+MODELS\s+NBELEM\s+(\d+)\s+(.*?)EndOfCHAPTER",
        element_text,
        re.DOTALL,
    )
    if not match:
        raise ValueError("DSC MODELS chapter not found")
    models = []
    for line in match.group(2).splitlines():
        entry = re.match(r"\s*(.+?)\s*(ROOT\s*)?;\s*$", line)
        if entry:
            models.append({"name": entry.group(1).strip(), "root": bool(entry.group(2))})
    return models


def _parse_environment_srts(scene_dsc: Path) -> dict[int, list[float]]:
    text = scene_dsc.read_text(encoding="latin-1", errors="replace")
    if "ENVIRONMENT" not in text:
        return {}
    environment = text.split("ENVIRONMENT", 1)[1].split("EndOfENVIRONMENT", 1)[0]
    chapter = re.search(r"CHAPTER\s+MODELS\s+(.*?)EndOfCHAPTER", environment, re.DOTALL)
    if not chapter:
        return {}
    result = {}
    for line in chapter.group(1).splitlines():
        match = re.match(r"\s*(\d+)\s+.*?\bSRT\s+([^;]+?)\s+MPRFLG", line)
        if not match:
            continue
        values = [float(value) for value in match.group(2).split()]
        if len(values) >= 9:
            result[int(match.group(1))] = values[:9]
    return result


def _srt_dict(values: list[float]) -> dict:
    return {
        "scale": values[0:3],
        "rotation_xyz": values[3:6],
        "translation_xyz": values[6:9],
    }


def _append_root_document(
    output: dict,
    output_buffer: bytearray,
    source: dict,
    source_buffer: bytes,
    root_model_name: str,
) -> int:
    while len(output_buffer) % 4:
        output_buffer.append(0)
    byte_base = len(output_buffer)
    output_buffer.extend(source_buffer)

    view_base = len(output["bufferViews"])
    accessor_base = len(output["accessors"])
    mesh_base = len(output["meshes"])
    node_base = len(output["nodes"])

    for view in source.get("bufferViews", []):
        copied = dict(view)
        copied["buffer"] = 0
        copied["byteOffset"] = int(copied.get("byteOffset", 0)) + byte_base
        output["bufferViews"].append(copied)

    for accessor in source.get("accessors", []):
        copied = dict(accessor)
        copied["bufferView"] = int(copied["bufferView"]) + view_base
        output["accessors"].append(copied)

    for mesh in source.get("meshes", []):
        copied = dict(mesh)
        primitives = []
        for primitive in mesh.get("primitives", []):
            remapped = dict(primitive)
            remapped["attributes"] = {
                semantic: int(index) + accessor_base
                for semantic, index in primitive.get("attributes", {}).items()
            }
            if primitive.get("indices") is not None:
                remapped["indices"] = int(primitive["indices"]) + accessor_base
            if primitive.get("material") is not None:
                # Standalone HRC exports use material zero as an explicit unbound
                # placeholder. The merged scene keeps a single shared placeholder.
                remapped["material"] = 0
            primitives.append(remapped)
        copied["primitives"] = primitives
        output["meshes"].append(copied)

    for local_index, node in enumerate(source.get("nodes", [])):
        copied = dict(node)
        if node.get("mesh") is not None:
            copied["mesh"] = int(node["mesh"]) + mesh_base
        if node.get("children"):
            copied["children"] = [int(index) + node_base for index in node["children"]]
        extras = dict(copied.get("extras") or {})
        extras["bz2_root_hrc_model"] = root_model_name
        extras["bz2_root_local_node_index"] = local_index
        copied["extras"] = extras
        output["nodes"].append(copied)

    return node_base


def _subtree_members(models: list[dict], parents: dict[int, int], root_index: int) -> set[int]:
    result = {root_index}
    changed = True
    while changed:
        changed = False
        for child, parent in parents.items():
            if parent in result and child not in result:
                result.add(child)
                changed = True
    return result


def _map_nodes_to_dsc(
    gltf: dict,
    models: list[dict],
    parents: dict[int, int],
    root_exports: list[dict],
) -> tuple[dict[int, int], dict[int, int], list[dict]]:
    model_to_node: dict[int, int] = {}
    node_to_model: dict[int, int] = {}
    ambiguous = []

    for root in root_exports:
        model_index = int(root["model_index"])
        root_node = int(root["gltf_root_node"])
        model_to_node[model_index] = root_node
        node_to_model[root_node] = model_index
        root_extras = gltf["nodes"][root_node].setdefault("extras", {})
        root_extras["bz2_dsc_model_index"] = model_index
        root_extras["bz2_dsc_model_name"] = models[model_index]["name"]

        subtree = _subtree_members(models, parents, model_index)
        remaining = subtree - {model_index}
        for node_index, node in enumerate(gltf.get("nodes", [])):
            if node_index == root_node:
                continue
            extras = node.get("extras") or {}
            if extras.get("bz2_root_hrc_model") != models[model_index]["name"]:
                continue
            node_name = str(node.get("name") or "")
            candidates = []
            for candidate in sorted(remaining):
                if candidate in model_to_node:
                    continue
                stem = _strip_version(models[candidate]["name"])
                if stem == node_name or stem.endswith("-" + node_name):
                    candidates.append(candidate)
            if len(candidates) == 1:
                candidate = candidates[0]
                model_to_node[candidate] = node_index
                node_to_model[node_index] = candidate
                extras = node.setdefault("extras", {})
                extras["bz2_dsc_model_index"] = candidate
                extras["bz2_dsc_model_name"] = models[candidate]["name"]
            elif len(candidates) > 1:
                ambiguous.append(
                    {
                        "root_model": models[model_index]["name"],
                        "hrc_node": node_name,
                        "candidates": [models[index]["name"] for index in candidates],
                    }
                )
    return model_to_node, node_to_model, ambiguous


def _actual_parent_nodes(gltf: dict) -> dict[int, int]:
    result = {}
    for parent_index, node in enumerate(gltf.get("nodes", [])):
        for child_index in node.get("children", []):
            result[int(child_index)] = parent_index
    return result


def assemble_scene(
    scene_dsc: Path,
    asset_source: Path,
    scene_prefix: str,
    output_gltf: Path,
    *,
    include_parametric: bool = True,
    curve_steps: int = 64,
    surface_steps_u: int = 32,
    surface_steps_v: int = 32,
) -> dict:
    models = _parse_model_roots(scene_dsc)
    _, relations = dscmat.parse_dsc(scene_dsc)
    parents = {
        int(relation["source_index"]): int(relation["target_index"])
        for relation in relations
        if relation["source_chapter"] == "MODELS"
        and relation["target_chapter"] == "MODELS"
        and relation["relation_code"] == MODEL_PARENT_CODE
    }
    environment_srts = _parse_environment_srts(scene_dsc)
    root_indices = [index for index, model in enumerate(models) if model["root"]]
    store = dscmat.open_store(asset_source)

    gltf = {
        "asset": {"version": "2.0", "generator": "bz2_dsc_multiroot_gltf.py"},
        "scene": 0,
        "scenes": [{"nodes": []}],
        "nodes": [],
        "meshes": [],
        "materials": [
            {
                "name": "HRC_Unbound_Material",
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.8, 0.8, 0.8, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        ],
        "buffers": [{}],
        "bufferViews": [],
        "accessors": [],
    }
    output_buffer = bytearray()
    root_exports = []
    missing_roots = []
    root_export_failures = []

    with tempfile.TemporaryDirectory(prefix="bz2_dsc_roots_") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        for model_index in root_indices:
            model_name = models[model_index]["name"]
            member = store.find_basename(model_name + ".hrc", f"{scene_prefix}/MODELS")
            if not member:
                missing_roots.append(model_name)
                continue
            source_hrc = temp_dir / (model_name + ".hrc")
            source_hrc.write_bytes(store.read(member))
            root_gltf = temp_dir / (model_name + ".gltf")
            try:
                if include_parametric:
                    root_summary = parametric.export_parametric(
                        source_hrc,
                        root_gltf,
                        curve_steps=max(2, curve_steps),
                        surface_steps_u=max(2, surface_steps_u),
                        surface_steps_v=max(2, surface_steps_v),
                    )
                    base_summary = root_summary["base"]
                    parametric_exported = int(root_summary["parametric_exported_count"])
                    parametric_failures = list(root_summary["parametric_failures"])
                else:
                    base_summary = assembled.export_hrc(source_hrc, root_gltf)
                    parametric_exported = 0
                    parametric_failures = []
                source_doc = json.loads(root_gltf.read_text(encoding="utf-8"))
                source_buffer = root_gltf.with_suffix(".bin").read_bytes()
                root_node = _append_root_document(
                    gltf,
                    output_buffer,
                    source_doc,
                    source_buffer,
                    model_name,
                )

                hrc_matrix = list(gltf["nodes"][root_node].get("matrix") or [])
                dsc_srt = environment_srts.get(model_index)
                if dsc_srt:
                    gltf["nodes"][root_node]["matrix"] = assembled._gltf_matrix(_srt_dict(dsc_srt))
                    extras = gltf["nodes"][root_node].setdefault("extras", {})
                    extras["dsc_environment_srt"] = dsc_srt
                    extras["hrc_root_matrix_before_dsc_instance_override"] = hrc_matrix

                gltf["scenes"][0]["nodes"].append(root_node)
                root_exports.append(
                    {
                        "model_index": model_index,
                        "model_name": model_name,
                        "source_hrc": member,
                        "gltf_root_node": root_node,
                        "node_count": int(base_summary["node_count"]),
                        "class4_mesh_count": int(base_summary["mesh_count"]),
                        "parametric_exported_count": parametric_exported,
                        "parametric_failures": parametric_failures,
                        "dsc_environment_srt": dsc_srt,
                        "hrc_root_srt_source": (
                            source_doc.get("nodes", [{}])[0].get("extras", {}).get("srt_source")
                            if source_doc.get("nodes")
                            else None
                        ),
                    }
                )
            except Exception as exc:
                root_export_failures.append(
                    {"model_name": model_name, "error": f"{type(exc).__name__}: {exc}"}
                )

    model_to_node, node_to_model, ambiguous = _map_nodes_to_dsc(
        gltf, models, parents, root_exports
    )
    actual_parent = _actual_parent_nodes(gltf)
    parent_matches = 0
    parent_mismatches = []
    parent_unmapped = []
    for child_model, parent_model in sorted(parents.items()):
        child_node = model_to_node.get(child_model)
        parent_node = model_to_node.get(parent_model)
        if child_node is None or parent_node is None:
            parent_unmapped.append(
                {
                    "child": models[child_model]["name"],
                    "parent": models[parent_model]["name"],
                }
            )
            continue
        observed_parent = actual_parent.get(child_node)
        if observed_parent == parent_node:
            parent_matches += 1
        else:
            observed_model = node_to_model.get(observed_parent) if observed_parent is not None else None
            parent_mismatches.append(
                {
                    "child": models[child_model]["name"],
                    "expected_parent": models[parent_model]["name"],
                    "observed_parent": (
                        models[observed_model]["name"] if observed_model is not None else None
                    ),
                }
            )

    output_gltf.parent.mkdir(parents=True, exist_ok=True)
    bin_path = output_gltf.with_suffix(".bin")
    gltf["buffers"][0] = {"byteLength": len(output_buffer), "uri": bin_path.name}
    output_gltf.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    bin_path.write_bytes(output_buffer)

    unmapped_models = [
        model["name"] for index, model in enumerate(models) if index not in model_to_node
    ]
    summary = {
        "schema": "bz2-dsc-multiroot-gltf-v1",
        "scene_dsc": str(scene_dsc),
        "asset_source": str(asset_source),
        "scene_prefix": scene_prefix,
        "output_gltf": str(output_gltf),
        "model_count": len(models),
        "root_count": len(root_indices),
        "resolved_root_count": len(root_exports),
        "missing_root_count": len(missing_roots),
        "missing_roots": missing_roots,
        "root_export_failure_count": len(root_export_failures),
        "root_export_failures": root_export_failures,
        "mapped_model_count": len(model_to_node),
        "unmapped_model_count": len(unmapped_models),
        "unmapped_models": unmapped_models,
        "ambiguous_node_mapping_count": len(ambiguous),
        "ambiguous_node_mappings": ambiguous,
        "code110_parent_matches": parent_matches,
        "code110_parent_mismatch_count": len(parent_mismatches),
        "code110_parent_mismatches": parent_mismatches,
        "code110_parent_unmapped_count": len(parent_unmapped),
        "code110_parent_unmapped": parent_unmapped,
        "final_node_count": len(gltf["nodes"]),
        "final_mesh_count": len(gltf["meshes"]),
        "class4_mesh_total": sum(item["class4_mesh_count"] for item in root_exports),
        "parametric_mesh_total": sum(
            item["parametric_exported_count"] for item in root_exports
        ),
        "root_exports": root_exports,
        "notes": [
            "DSC MODELS entries marked ROOT are instantiated as scene roots; non-root model entries map to internal nodes of those HRC trees.",
            "Explicit DSC ENVIRONMENT SRT overrides the HRC root matrix exactly once; it is not multiplied on top of the HRC root transform.",
            "DSC relation code 110 is the hierarchy oracle used to regression-check the merged HRC trees.",
            "The output intentionally retains one unbound placeholder material; source material/texture binding is a separate reconstruction layer.",
        ],
    }
    output_gltf.with_suffix(".multiroot.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene_dsc", type=Path)
    parser.add_argument("asset_source", type=Path)
    parser.add_argument("scene_prefix")
    parser.add_argument("output_gltf", type=Path)
    parser.add_argument("--no-parametric", action="store_true")
    parser.add_argument("--curve-steps", type=int, default=64)
    parser.add_argument("--surface-steps-u", type=int, default=32)
    parser.add_argument("--surface-steps-v", type=int, default=32)
    args = parser.parse_args()
    result = assemble_scene(
        args.scene_dsc,
        args.asset_source,
        args.scene_prefix,
        args.output_gltf,
        include_parametric=not args.no_parametric,
        curve_steps=args.curve_steps,
        surface_steps_u=args.surface_steps_u,
        surface_steps_v=args.surface_steps_v,
    )
    print(json.dumps(result, indent=2))
    return 1 if any(
        (
            result["missing_root_count"],
            result["root_export_failure_count"],
            result["unmapped_model_count"],
            result["ambiguous_node_mapping_count"],
            result["code110_parent_mismatch_count"],
            result["code110_parent_unmapped_count"],
        )
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())

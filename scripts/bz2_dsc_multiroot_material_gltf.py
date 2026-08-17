#!/usr/bin/env python3
"""Bind source DSC materials across every ROOT HRC in a complete scene glTF.

This is the scene-wide counterpart to ``bz2_dsc_material_gltf.py``. It assumes
that ``bz2_dsc_multiroot_gltf.py`` has already mapped every HRC node to its DSC
model index through node extras. That lets material binding avoid historical
name-suffix guessing entirely.

Class-4 meshes are re-decoded from their authoritative ROOT HRC source, split by
the proven polygon material-slot metadata, and rebound to the ordered DSC
MODELS->MATERIALS code-300 list. Class-1 ROOT grids receive their object-level
first material. Parametric meshes receive an object-level material only when a
single material with no DSC code-401 texture relationship is present; textured
parametric projection remains a separate reconstruction problem.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import bz2_dsc_material_gltf as dscmat
import bz2_hrc_gltf as assembled
import bz2_hrc_tree_probe as hrc_tree


def _is_explicitly_unbound_source_mesh(definitions: list[dict], used_slots: list[int]) -> bool:
    """Recognize a class-4 mesh for which the DSC authors no material binding.

    Slot zero is the class-4 default polygon slot. If the reconstructed DSC
    model has no direct or inherited code-300 material definitions at all, a
    slot-zero-only mesh is explicitly unbound in the source scene. Preserve the
    glTF placeholder and record that fact instead of fabricating a material or
    aborting. Nonzero slots and partial authored mappings remain fatal.
    """
    return not definitions and bool(used_slots) and set(used_slots) == {0}


def _parent_map(gltf: dict) -> dict[int, int]:
    result = {}
    for parent_index, node in enumerate(gltf.get("nodes", [])):
        for child_index in node.get("children", []):
            result[int(child_index)] = parent_index
    return result


def _root_hrc_member(store: dscmat.SourceStore, scene_prefix: str, root_model: str) -> str | None:
    return (
        store.find_basename(root_model + ".hrc", f"{scene_prefix}/MODELS")
        or store.find_basename(root_model + ".hrc")
    )


def _material_defs_for_node(
    gltf: dict,
    node_index: int,
    parent_by_node: dict[int, int],
    model_material_defs: dict[int, list[dict]],
) -> tuple[list[dict], int | None]:
    node = gltf["nodes"][node_index]
    model_index = (node.get("extras") or {}).get("bz2_dsc_model_index")
    if model_index is not None and int(model_index) in model_material_defs:
        return model_material_defs[int(model_index)], None

    visited = set()
    parent = parent_by_node.get(node_index)
    while parent is not None and parent not in visited:
        visited.add(parent)
        parent_model_index = (
            gltf["nodes"][parent].get("extras") or {}
        ).get("bz2_dsc_model_index")
        if (
            parent_model_index is not None
            and int(parent_model_index) in model_material_defs
        ):
            return model_material_defs[int(parent_model_index)], int(parent_model_index)
        parent = parent_by_node.get(parent)
    return [], None


def bind_scene_materials(
    input_gltf: Path,
    scene_dsc: Path,
    asset_source: Path,
    scene_prefix: str,
    output_gltf: Path,
) -> dict:
    gltf = json.loads(input_gltf.read_text(encoding="utf-8"))
    if not gltf.get("buffers"):
        raise ValueError("input glTF has no buffer")
    input_buffer_path = input_gltf.parent / gltf["buffers"][0]["uri"]
    buffer = bytearray(input_buffer_path.read_bytes())

    output_gltf.parent.mkdir(parents=True, exist_ok=True)
    texture_dir = output_gltf.parent / "textures"
    texture_dir.mkdir(parents=True, exist_ok=True)

    store = dscmat.open_store(asset_source)
    model_material_defs, material_defs, chapters, relations = dscmat.build_scene_materials(
        scene_dsc,
        store,
        scene_prefix,
        texture_dir,
    )
    source_material_index = dscmat.append_source_materials(gltf, material_defs)
    models = chapters.get("MODELS", [])
    materials = chapters.get("MATERIALS", [])
    parent_by_node = _parent_map(gltf)
    material_names_with_401 = {
        materials[int(relation["source_index"])]
        for relation in relations
        if relation["source_chapter"] == "MATERIALS"
        and relation["target_chapter"] == "TEXTURES2D"
        and relation["relation_code"] == 401
        and 0 <= int(relation["source_index"]) < len(materials)
    }

    # Root HRC data/probes are cached because the large master hierarchy can
    # contribute many class-4 nodes to the same DSC scene.
    root_cache: dict[str, tuple[bytes, dict, list[dict]]] = {}

    def load_root(root_model: str) -> tuple[bytes, dict, list[dict]]:
        if root_model in root_cache:
            return root_cache[root_model]
        member = _root_hrc_member(store, scene_prefix, root_model)
        if not member:
            raise FileNotFoundError(f"ROOT HRC not found for {root_model}")
        data = store.read(member)
        with tempfile.NamedTemporaryFile(suffix=".hrc") as handle:
            handle.write(data)
            handle.flush()
            report = hrc_tree.probe(Path(handle.name))
        outer = dict(report.get("outer_model") or {})
        records = [dict(item) for item in report.get("tree", [])]
        if not outer:
            raise ValueError(f"ROOT HRC has no outer model: {root_model}")
        root_cache[root_model] = (data, outer, records)
        return root_cache[root_model]

    class4_rebound = []
    class4_decode_failures = []
    slot_errors = []
    unbound_source_materials = []
    inherited_materials = []

    for node_index, node in enumerate(gltf.get("nodes", [])):
        extras = node.get("extras") or {}
        if extras.get("class_id") != 4:
            continue
        root_model = extras.get("bz2_root_hrc_model")
        source_offset = extras.get("source_offset")
        if root_model is None or source_offset is None:
            class4_decode_failures.append(
                {"node": node.get("name"), "reason": "missing_root_or_source_offset"}
            )
            continue

        try:
            data, outer, records = load_root(str(root_model))
            source_offset_int = int(source_offset)
            if int(outer.get("offset", -1)) == source_offset_int:
                item = outer
                payload_offset = assembled._outer_payload_offset(data, outer)
                end = (
                    int(records[0]["offset"]) - int(records[0]["zero_run"])
                    if records
                    else len(data)
                )
            else:
                record_index = next(
                    index
                    for index, record in enumerate(records)
                    if int(record.get("offset", -1)) == source_offset_int
                )
                item = records[record_index]
                payload_offset = int(item["payload_offset"])
                end = assembled._record_end(records, record_index, len(data))

            mesh = dscmat.decode_class4_with_slots(data, payload_offset, end)
            definitions, inherited_model_index = _material_defs_for_node(
                gltf,
                node_index,
                parent_by_node,
                model_material_defs,
            )
            material_indices = [
                source_material_index[definition["name"]]
                for definition in definitions
                if definition["name"] in source_material_index
            ]
            mesh_index, used_slots = dscmat.emit_slotted_mesh(
                gltf,
                buffer,
                str(node.get("name") or item.get("name") or "class4"),
                mesh,
                material_indices,
            )
            unresolved_slots = [
                slot for slot in used_slots if slot >= len(material_indices)
            ]
            explicitly_unbound_source = _is_explicitly_unbound_source_mesh(
                definitions, used_slots
            )
            if unresolved_slots and explicitly_unbound_source:
                # PATCH: slot-zero-only class-4 meshes with no direct or inherited
                # DSC code-300 relationship are explicitly unbound in the source.
                # Keep the HRC placeholder rather than fabricating a material or
                # aborting; nonzero slots and partial authored mappings still fail.
                if mesh_index is not None and gltf.get("materials"):
                    for primitive in gltf["meshes"][mesh_index].get("primitives", []):
                        primitive["material"] = 0
                unbound_source_materials.append(
                    {
                        "node": node.get("name"),
                        "root_model": root_model,
                        "used_slots": used_slots,
                        "reason": "class4_without_authored_dsc_material",
                    }
                )
                unresolved_slots = []
            if unresolved_slots:
                slot_errors.append(
                    {
                        "node": node.get("name"),
                        "root_model": root_model,
                        "used_slots": used_slots,
                        "material_count": len(material_indices),
                        "unresolved_slots": unresolved_slots,
                    }
                )
            if mesh_index is not None:
                node["mesh"] = mesh_index
                node.setdefault("extras", {})["source_material_binding"] = (
                    "unbound_source"
                    if explicitly_unbound_source
                    else ("inherited" if inherited_model_index is not None else "direct")
                )
                class4_rebound.append(str(node.get("name") or item.get("name")))
                if inherited_model_index is not None:
                    inherited_materials.append(
                        {
                            "node": node.get("name"),
                            "inherited_from_model_index": inherited_model_index,
                            "inherited_from_model_name": (
                                models[inherited_model_index]
                                if 0 <= inherited_model_index < len(models)
                                else None
                            ),
                        }
                    )
        except Exception as exc:
            class4_decode_failures.append(
                {
                    "node": node.get("name"),
                    "root_model": root_model,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    class1_object_materials = []
    parametric_object_materials = []
    parametric_textured_deferred = []

    for node_index, node in enumerate(gltf.get("nodes", [])):
        if node.get("mesh") is None:
            continue
        extras = node.get("extras") or {}
        definitions, inherited_model_index = _material_defs_for_node(
            gltf,
            node_index,
            parent_by_node,
            model_material_defs,
        )
        if not definitions:
            continue

        if extras.get("class1_grid_geometry_emitted"):
            material_name = definitions[0]["name"]
            material_index = source_material_index.get(material_name)
            if material_index is not None:
                for primitive in gltf["meshes"][int(node["mesh"])].get("primitives", []):
                    primitive["material"] = material_index
                node.setdefault("extras", {})["source_material_binding"] = "object_first_material"
                class1_object_materials.append(
                    {"node": node.get("name"), "material": material_name}
                )
            continue

        if extras.get("class_id") in {9, 10}:
            # NURBS parameter-space UVs are not the original Softimage texture
            # projection. The authoritative DSC code-401 relationship therefore
            # gates this path even when a historical TXMP source picture has not
            # yet resolved to a portable URI.
            has_material_texture = any(
                definition["name"] in material_names_with_401
                for definition in definitions
            )
            if len(definitions) != 1 or has_material_texture:
                parametric_textured_deferred.append(
                    {
                        "node": node.get("name"),
                        "materials": [definition["name"] for definition in definitions],
                        "has_code401_texture": has_material_texture,
                    }
                )
                continue
            material_name = definitions[0]["name"]
            material_index = source_material_index.get(material_name)
            if material_index is None:
                continue
            for primitive in gltf["meshes"][int(node["mesh"])].get("primitives", []):
                primitive["material"] = material_index
            node.setdefault("extras", {})["source_material_binding"] = (
                "inherited_object_material"
                if inherited_model_index is not None
                else "direct_object_material"
            )
            parametric_object_materials.append(
                {"node": node.get("name"), "material": material_name}
            )

    gltf["buffers"][0]["byteLength"] = len(buffer)
    output_bin = output_gltf.with_suffix(".bin")
    gltf["buffers"][0]["uri"] = output_bin.name
    dscmat.compact_referenced_meshes(gltf)
    output_gltf.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    output_bin.write_bytes(buffer)

    referenced_material_indices = sorted(
        {
            int(primitive["material"])
            for mesh in gltf.get("meshes", [])
            for primitive in mesh.get("primitives", [])
            if primitive.get("material") is not None
        }
    )
    summary = {
        "schema": "bz2-dsc-multiroot-material-gltf-v1",
        "input_gltf": str(input_gltf),
        "scene_dsc": str(scene_dsc),
        "asset_source": str(asset_source),
        "scene_prefix": scene_prefix,
        "output_gltf": str(output_gltf),
        "class4_rebound_count": len(class4_rebound),
        "class4_rebound": class4_rebound,
        "class4_decode_failure_count": len(class4_decode_failures),
        "class4_decode_failures": class4_decode_failures,
        "slot_error_count": len(slot_errors),
        "slot_errors": slot_errors,
        "unbound_source_material_count": len(unbound_source_materials),
        "unbound_source_materials": unbound_source_materials,
        "inherited_material_count": len(inherited_materials),
        "inherited_materials": inherited_materials,
        "class1_object_material_count": len(class1_object_materials),
        "class1_object_materials": class1_object_materials,
        "parametric_object_material_count": len(parametric_object_materials),
        "parametric_object_materials": parametric_object_materials,
        "parametric_textured_deferred_count": len(parametric_textured_deferred),
        "parametric_textured_deferred": parametric_textured_deferred,
        "source_material_count": len(material_defs),
        "referenced_gltf_material_count": len(referenced_material_indices),
        "final_mesh_count": len(gltf.get("meshes", [])),
        "final_primitive_count": sum(
            len(mesh.get("primitives", [])) for mesh in gltf.get("meshes", [])
        ),
        "notes": [
            "complete-scene nodes use authoritative bz2_dsc_model_index extras; material binding does not rely on global name-suffix guessing",
            "class-4 polygon metadata upper 16 bits select ordered DSC relation-code-300 material slots",
            "nodes without direct code-300 materials inherit the nearest material-bearing parent in the reconstructed scene graph",
            "slot-zero-only class-4 meshes with no authored direct or inherited DSC material retain the explicit HRC unbound placeholder instead of fabricating a source material",
            "ROOT class-1 grids receive their first object material but retain projection-driven texturing separately",
            "parametric meshes receive a source object material only when the DSC material has no code-401 texture relation; textured NURBS projection remains intentionally deferred",
            "ordered multi-texture code-401 restoration and corrected MTR semantics remain subsequent non-destructive stages",
        ],
    }
    output_gltf.with_suffix(".materials.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_gltf", type=Path)
    parser.add_argument("scene_dsc", type=Path)
    parser.add_argument("asset_source", type=Path)
    parser.add_argument("scene_prefix")
    parser.add_argument("output_gltf", type=Path)
    args = parser.parse_args()
    result = bind_scene_materials(
        args.input_gltf,
        args.scene_dsc,
        args.asset_source,
        args.scene_prefix,
        args.output_gltf,
    )
    print(json.dumps(result, indent=2))
    return 1 if result["class4_decode_failure_count"] or result["slot_error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

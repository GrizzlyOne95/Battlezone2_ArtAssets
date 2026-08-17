#!/usr/bin/env python3
"""Create Blender UV maps and texture nodes from recovered BZ2 projection state.

This is the practical asset-fidelity handoff. It preserves imported source UVs,
adds one named Blender UV map per recoverable Softimage projection, rewires
material-level texture nodes to those maps when the operator is supported, and
adds model-local texture nodes without guessing unresolved cross-scope blending.

Model-local DSC relation-code-400 projection records are particularly safe for
this stage: the validated archival corpus contains 403 resolved edges, all using
working projection codes 1..5 and all with identity +90 texture-matrix SRT.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import bz2_projection_uv as projection_uv

try:
    import bpy  # type: ignore
except ImportError:  # pragma: no cover - Blender-only runtime
    bpy = None


def _find_object(name: str):
    obj = bpy.data.objects.get(name)
    if obj is not None:
        return obj
    matches = [candidate for candidate in bpy.data.objects if candidate.name.startswith(name + ".")]
    return matches[0] if matches else None


def _safe_name(value: str, prefix: str = "BZ2") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return f"{prefix}_{cleaned}"[:63]


def _principled(material):
    material.use_nodes = True
    node = next((item for item in material.node_tree.nodes if item.type == "BSDF_PRINCIPLED"), None)
    return node or material.node_tree.nodes.new("ShaderNodeBsdfPrincipled")


def _source_material_name(material, known_names: set[str]) -> str | None:
    stored = material.get("bz2_source_material_name") if hasattr(material, "get") else None
    if stored in known_names:
        return str(stored)
    if material.name in known_names:
        return material.name
    candidates = [name for name in known_names if material.name.startswith(name + ".")]
    return max(candidates, key=len) if candidates else None


def _load_image(record: dict, sidecar_dir: Path):
    uri = record.get("uri")
    if not uri:
        return None
    path = (sidecar_dir / str(uri)).resolve()
    if not path.is_file():
        return None
    return bpy.data.images.load(str(path), check_existing=True)


def _bounds(mesh):
    return projection_uv.bounds_from_points(tuple(vertex.co) for vertex in mesh.vertices)


def _generate_uv_map(obj, projection: dict, uv_name: str) -> dict:
    mesh = obj.data
    layer = mesh.uv_layers.get(uv_name)
    if layer is None:
        layer = mesh.uv_layers.new(name=uv_name)
    prepared_points, bounds = projection_uv.prepare_projection_points(
        [tuple(vertex.co) for vertex in mesh.vertices], projection
    )
    assigned = 0
    for polygon in mesh.polygons:
        loop_indices = list(polygon.loop_indices)
        points = [prepared_points[mesh.loops[index].vertex_index] for index in loop_indices]
        uvs = projection_uv.project_prepared_polygon(points, bounds, projection)
        for loop_index, uv in zip(loop_indices, uvs):
            layer.data[loop_index].uv = uv
            assigned += 1
    return {
        "uv_map": layer.name,
        "loop_count": assigned,
        "projection_type": projection_uv.projection_type_name(
            projection.get("projection_or_mapping_code_candidate")
        ),
    }


def _existing_source_uv_status(obj) -> dict:
    mesh = obj.data
    if not mesh.uv_layers:
        return {"uv_map_count": 0, "active_uv": None, "active_uv_all_zero": None}
    layer = mesh.uv_layers.active or mesh.uv_layers[0]
    values = [(float(item.uv.x), float(item.uv.y)) for item in layer.data]
    all_zero = all(abs(u) <= 1.0e-12 and abs(v) <= 1.0e-12 for u, v in values)
    return {
        "uv_map_count": len(mesh.uv_layers),
        "active_uv": layer.name,
        "active_uv_all_zero": all_zero,
    }


def _find_texture_node(material, texture_object: str):
    if not material.use_nodes or not material.node_tree:
        return None
    exact = []
    partial = []
    for node in material.node_tree.nodes:
        if node.type != "TEX_IMAGE":
            continue
        label = str(node.label or "")
        if label.endswith(str(texture_object)):
            exact.append(node)
        elif str(texture_object) in label:
            partial.append(node)
    return exact[0] if exact else (partial[0] if partial else None)


def _link_uv_map(material, texture_node, uv_name: str, label: str):
    tree = material.node_tree
    uv_node_name = _safe_name(label, "BZ2UV")
    uv_node = tree.nodes.get(uv_node_name)
    if uv_node is None or uv_node.type != "UVMAP":
        uv_node = tree.nodes.new("ShaderNodeUVMap")
        uv_node.name = uv_node_name
    uv_node.label = label
    uv_node.uv_map = uv_name
    uv_node.location = (texture_node.location.x - 220, texture_node.location.y)
    for link in list(texture_node.inputs["Vector"].links):
        tree.links.remove(link)
    tree.links.new(uv_node.outputs["UV"], texture_node.inputs["Vector"])
    return uv_node


def _combined_source_uv_transform(layer: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    """Compose confirmed repeat, scale/offset and crop for source-UV fallback."""
    repeats = layer.get("si_texture2d_repeat_uv") or [1, 1]
    scale = layer.get("si_texture2d_uv_scale") or [1.0, 1.0]
    offset = layer.get("si_texture2d_uv_offset") or [0.0, 0.0]
    ru = float(repeats[0]) if len(repeats) >= 1 else 1.0
    rv = float(repeats[1]) if len(repeats) >= 2 else 1.0
    su, sv = float(scale[0]) * ru, float(scale[1]) * rv
    ou, ov = float(offset[0]), float(offset[1])
    crop = layer.get("crop_rect_pixels_raw") or {}
    width, height = layer.get("width"), layer.get("height")
    if width and height and int(width) > 1 and int(height) > 1 and crop:
        x0 = float(crop.get("x0", 0))
        x1 = float(crop.get("x1", int(width) - 1))
        y0 = float(crop.get("y0", 0))
        y1 = float(crop.get("y1", int(height) - 1))
        crop_su = (x1 - x0) / float(int(width) - 1)
        crop_sv = (y1 - y0) / float(int(height) - 1)
        su, sv = su * crop_su, sv * crop_sv
        ou = (x0 / float(int(width) - 1)) + ou * crop_su
        ov = (y0 / float(int(height) - 1)) + ov * crop_sv
    return (su, sv), (ou, ov)


def _link_source_uv_transform(material, texture_node, layer: dict, label: str):
    """Restore confirmed image-space placement when the projection is unresolved."""
    tree = material.node_tree
    coord_name = _safe_name(label, "BZ2SRCUV")
    coord = tree.nodes.get(coord_name)
    if coord is None or coord.type != "TEX_COORD":
        coord = tree.nodes.new("ShaderNodeTexCoord")
        coord.name = coord_name
    mapping_name = _safe_name(label, "BZ2MAP")
    mapping = tree.nodes.get(mapping_name)
    if mapping is None or mapping.type != "MAPPING":
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.name = mapping_name
    scale, offset = _combined_source_uv_transform(layer)
    mapping.vector_type = "POINT"
    mapping.inputs["Location"].default_value[0] = offset[0]
    mapping.inputs["Location"].default_value[1] = offset[1]
    mapping.inputs["Location"].default_value[2] = 0.0
    mapping.inputs["Scale"].default_value[0] = scale[0]
    mapping.inputs["Scale"].default_value[1] = scale[1]
    mapping.inputs["Scale"].default_value[2] = 1.0
    mapping.label = f"Confirmed repeat/+6/crop; operator deferred: {label}"
    coord.location = (texture_node.location.x - 440, texture_node.location.y)
    mapping.location = (texture_node.location.x - 220, texture_node.location.y)
    for link in list(mapping.inputs["Vector"].links):
        tree.links.remove(link)
    tree.links.new(coord.outputs["UV"], mapping.inputs["Vector"])
    for link in list(texture_node.inputs["Vector"].links):
        tree.links.remove(link)
    tree.links.new(mapping.outputs["Vector"], texture_node.inputs["Vector"])
    return mapping


def _mark_special_material_mode(material, texture_node, layer: dict, code: int, texture_object: str) -> None:
    """Preserve code-7/8 material textures without inventing geometric UV mapping."""
    candidate = (
        "reflection_environment_candidate"
        if code == 7
        else "glass_environment_candidate"
    )
    texture_node.label = f"SPECIAL MODE {code} ({candidate}): {texture_object}"
    material[f"bz2_special_texture_mode_{code}_{_safe_name(texture_object, '')}"] = candidate
    material["bz2_special_texture_mode_status"] = (
        "source texture preserved; existing imported UV connection is provisional until the legacy special material mapping is reconstructed"
    )


def _copy_materials_for_model_projection(obj, source_names: dict[int, str]) -> None:
    for slot_index, slot in enumerate(obj.material_slots):
        material = slot.material
        if material is None:
            continue
        copied = material.copy()
        copied.name = _safe_name(f"{material.name}_{obj.name}", "BZ2MAT")
        copied["bz2_source_material_name"] = source_names.get(slot_index, material.name)
        slot.material = copied


def _add_model_projection_nodes(obj, projections: list[dict], sidecar_dir: Path) -> dict:
    if not projections or not obj.material_slots:
        return {"texture_node_count": 0, "auto_connected_base_count": 0, "missing_images": []}
    node_count = connected = 0
    missing_images = []
    for slot in obj.material_slots:
        material = slot.material
        if material is None:
            continue
        material.use_nodes = True
        tree = material.node_tree
        principled = _principled(material)
        frame = tree.nodes.new("NodeFrame")
        frame.label = f"BZ2 model-local projections: {obj.name}"
        frame.name = _safe_name(obj.name, "BZ2_MODEL_PROJ")
        y = -500
        for order, projection in enumerate(projections):
            uv_name = projection.get("blender_uv_map")
            if not uv_name:
                continue
            image = _load_image(projection, sidecar_dir)
            if image is None:
                missing_images.append(str(projection.get("uri") or projection.get("resolved_picture") or ""))
                continue
            uvnode = tree.nodes.new("ShaderNodeUVMap")
            uvnode.uv_map = str(uv_name)
            uvnode.label = f"MODEL UV: {projection.get('texture_object', '')}"
            uvnode.parent = frame
            uvnode.location = (-700, y)
            tex = tree.nodes.new("ShaderNodeTexImage")
            tex.image = image
            role = str(projection.get("role_candidate") or "projection")
            tex.label = f"MODEL {role.upper()}: {projection.get('texture_object', '')}"
            tex.parent = frame
            tex.location = (-450, y)
            tree.links.new(uvnode.outputs["UV"], tex.inputs["Vector"])
            node_count += 1
            # Conservative visual handoff: if the imported material currently has
            # no base-color texture at all, a recovered model-local base projection
            # is preferable to a flat diffuse factor. If another texture stack is
            # already connected, preserve it and leave this node explicit for the
            # unresolved cross-scope blend ordering.
            if role == "base_or_default_candidate" and not principled.inputs["Base Color"].links:
                tree.links.new(tex.outputs["Color"], principled.inputs["Base Color"])
                connected += 1
            y -= 220
        material["bz2_model_projection_node_count"] = node_count
        material["bz2_model_projection_blend_status"] = (
            "base auto-connected only when Base Color was otherwise untextured; cross-scope model/material blend ordering preserved for later refinement"
        )
    return {
        "texture_node_count": node_count,
        "auto_connected_base_count": connected,
        "missing_images": missing_images,
    }


def apply_asset_uvs(gltf_path: Path, model_sidecar_path: Path, layer_sidecar_path: Path) -> dict:
    if bpy is None:
        raise RuntimeError("this script must be executed by Blender Python")
    gltf = json.loads(gltf_path.read_text(encoding="utf-8"))
    model_payload = json.loads(model_sidecar_path.read_text(encoding="utf-8"))
    layer_payload = json.loads(layer_sidecar_path.read_text(encoding="utf-8"))
    model_dir = Path(str(model_payload.get("output_gltf") or model_sidecar_path)).resolve().parent

    material_records = {
        str(record.get("material_name")): record
        for record in layer_payload.get("materials") or []
        if record.get("material_name")
    }
    known_material_names = set(material_records)

    generated = 0
    material_rewired = 0
    source_uv_fallbacks = 0
    special_modes_preserved = 0
    deferred = []
    missing_objects = []
    object_records = []

    model_by_node: dict[int, dict] = {
        int(record["gltf_node_index"]): record
        for record in model_payload.get("models") or []
        if record.get("gltf_node_index") is not None
    }

    # Material-level texture projection/tiling applies to every mesh node, not
    # only models that also own a relation-code-400 projection. Model-local
    # projection state is therefore an optional per-node record here.
    for node_index, node in enumerate(gltf.get("nodes", [])):
        if node.get("mesh") is None:
            continue
        record = model_by_node.get(node_index, {})
        node_name = str(node.get("name") or "")
        obj = _find_object(node_name)
        if obj is None or getattr(obj, "type", None) != "MESH":
            missing_objects.append({"node_index": node_index, "node": node_name})
            continue

        source_uv = _existing_source_uv_status(obj)
        projections = [dict(item) for item in record.get("local_texture_projections") or []]
        source_material_names = {
            index: (_source_material_name(slot.material, known_material_names) if slot.material else None)
            for index, slot in enumerate(obj.material_slots)
        }
        source_material_names = {index: name for index, name in source_material_names.items() if name}
        if projections:
            _copy_materials_for_model_projection(obj, source_material_names)

        object_generated = []
        for order, projection in enumerate(projections):
            code = int(projection.get("projection_or_mapping_code_candidate") or 0)
            if projection_uv.projection_type_name(code) is None:
                deferred.append({"object": obj.name, "texture": projection.get("texture_object"), "reason": f"unsupported_projection_code_{code}"})
                continue
            if (
                not projection_uv.matrix_srt_is_identity(projection)
                and not projection_uv.code400_rotation_supported(projection)
            ):
                deferred.append({"object": obj.name, "texture": projection.get("texture_object"), "reason": "unsupported_nonidentity_matrix_srt"})
                continue
            uv_name = _safe_name(f"P{code}_{projection.get('texture_object', order)}", "BZ2")
            try:
                uv_result = _generate_uv_map(obj, projection, uv_name)
            except Exception as exc:
                deferred.append({"object": obj.name, "texture": projection.get("texture_object"), "reason": f"{type(exc).__name__}:{exc}"})
                continue
            projection["blender_uv_map"] = uv_result["uv_map"]
            projection["blender_uv_status"] = "working_projection_generated_v2_repeat_aware"
            generated += 1
            object_generated.append({"texture": projection.get("texture_object"), **uv_result})

        # Rewire material-level texture layers. Authored nonzero CurrentUV
        # is preserved; fitted UV generation is reserved for missing/all-zero source UVs. Special modes 7/8 are
        # explicitly preserved as special material mappings instead of being
        # mislabeled as geometric UV projections. Other unresolved transforms
        # retain confirmed repeat/+6/crop on the source-UV fallback path.
        for slot in obj.material_slots:
            material = slot.material
            if material is None:
                continue
            source_name = _source_material_name(material, known_material_names)
            if source_name is None:
                continue
            layer_record = material_records.get(source_name)
            if not layer_record:
                continue
            for layer in layer_record.get("layers") or []:
                texture_object = str(layer.get("texture_object") or "")
                tex_node = _find_texture_node(material, texture_object)
                if tex_node is None:
                    continue
                code = int(layer.get("projection_or_mapping_code_candidate") or 0)
                if code in {7, 8}:
                    _mark_special_material_mode(material, tex_node, layer, code, texture_object)
                    special_modes_preserved += 1
                    deferred.append(
                        {
                            "object": obj.name,
                            "texture": texture_object,
                            "reason": f"special_material_mode_{code}",
                            "fallback": "existing_imported_uv_connection_preserved_as_provisional_visualization",
                        }
                    )
                    continue
                source_uv_usable = (
                    source_uv.get("uv_map_count", 0) > 0
                    and source_uv.get("active_uv_all_zero") is False
                )
                can_generate_missing_projection = (
                    not source_uv_usable
                    and projection_uv.projection_type_name(code) is not None
                    and projection_uv.matrix_srt_is_identity(layer)
                )
                if source_uv_usable:
                    # Archive qualification: 6,230 mapped class-4 code401 models
                    # carry nonzero authored HRC UVs, and regenerating them from
                    # fitted geometry diverges materially for most source assets.
                    # Preserve CurrentUV and layer only proven live image effects.
                    _link_source_uv_transform(material, tex_node, layer, texture_object)
                    source_uv_fallbacks += 1
                    if not projection_uv.matrix_srt_is_identity(layer):
                        deferred.append({
                            "object": obj.name,
                            "texture": texture_object,
                            "reason": "nonidentity_code401_live_uvw_transform",
                            "fallback": "authored_source_uv_plus_confirmed_repeat_image_transform",
                        })
                elif can_generate_missing_projection:
                    uv_name = _safe_name(f"P{code}_{texture_object}", "BZ2")
                    try:
                        _generate_uv_map(obj, layer, uv_name)
                        _link_uv_map(material, tex_node, uv_name, f"Projected {texture_object}")
                        generated += 1
                        material_rewired += 1
                    except Exception as exc:
                        deferred.append({"object": obj.name, "texture": texture_object, "reason": f"{type(exc).__name__}:{exc}"})
                else:
                    _link_source_uv_transform(material, tex_node, layer, texture_object)
                    source_uv_fallbacks += 1
                    reason = (
                        "nonidentity_matrix_srt_without_usable_source_uv"
                        if not projection_uv.matrix_srt_is_identity(layer)
                        else f"unsupported_projection_code_{code}"
                    )
                    deferred.append({"object": obj.name, "texture": texture_object, "reason": reason, "fallback": "source_uv_plus_confirmed_repeat_image_transform"})

        model_nodes = _add_model_projection_nodes(obj, projections, model_dir)
        obj["bz2_asset_uv_status"] = "repeat-aware generated projection UV maps are additive; imported source UVs preserved"
        obj["bz2_source_uv_all_zero"] = bool(source_uv.get("active_uv_all_zero")) if source_uv.get("active_uv_all_zero") is not None else False
        obj["bz2_generated_projection_uv_count"] = len(object_generated)
        object_records.append({
            "object": obj.name,
            "source_uv": source_uv,
            "generated_model_uvs": object_generated,
            "model_texture_nodes": model_nodes,
        })

    return {
        "schema": "bz2-blender-asset-uv-fidelity-v2",
        "generated_projection_uv_map_count": generated,
        "material_texture_node_rewired_count": material_rewired,
        "source_uv_transform_fallback_count": source_uv_fallbacks,
        "special_material_mode_preserved_count": special_modes_preserved,
        "deferred_projection_count": len(deferred),
        "deferred_projections": deferred,
        "missing_object_count": len(missing_objects),
        "missing_objects": missing_objects,
        "objects": object_records,
        "notes": [
            "Imported source UV maps are never overwritten; generated projection maps are additive named UV layers.",
            "Model-local code-400 projections use the working 1..5 table; rotation-only +90 support poses are applied inversely in projection space, while unproven support scale/translation remain deferred. Recovered URepeat/VRepeat and +6 scale/offset/crop are applied afterward.",
            "Material-level unresolved matrix transforms retain source UVs but receive recovered repeat plus confirmed +6 scale/offset and crop transforms.",
            "Material-level special modes 7/8 are preserved and labeled explicitly; the existing imported UV connection remains only as provisional visualization rather than being promoted as the recovered special mapping.",
            "Model-local texture image nodes are exposed per object; base color is auto-connected only when the material had no existing base-color texture stack.",
        ],
    }
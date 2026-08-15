#!/usr/bin/env python3
"""Finish a recovered BZ2 Blender scene with ordered texture/render metadata.

Run inside Blender after generating the materialized/scene glTF and sidecars:

    blender --background --python scripts/blender_finish_reconstruction.py -- \
        scene.gltf scene.scene.json scene.blend \
        scene.texture_layers.json scene.model_textures.json scene.render_state.json

The stable camera/light importer is reused first. This layer then rebuilds the
ordered Softimage base + alpha-overlay color stack, generates additive projected
UV maps for recoverable asset textures, and stores the original SETUP_SOFT/
Mental Ray state as Blender custom properties. Overlay candidates are not treated
as emission until the source shader semantics are proven.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import blender_reconstruct_scene as base
import blender_apply_bz2_asset_uvs as asset_uvs

try:
    import bpy  # type: ignore
except ImportError:  # pragma: no cover
    bpy = None


def _argv() -> list[str]:
    return sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []


def _find_material(name: str):
    material = bpy.data.materials.get(name)
    if material:
        return material
    return next((item for item in bpy.data.materials if item.name.startswith(name + ".")), None)


def _principled(material):
    material.use_nodes = True
    node = next((item for item in material.node_tree.nodes if item.type == "BSDF_PRINCIPLED"), None)
    return node or material.node_tree.nodes.new("ShaderNodeBsdfPrincipled")


def _load_image(layer: dict, sidecar_dir: Path):
    uri = layer.get("uri")
    if not uri:
        return None
    path = (sidecar_dir / str(uri)).resolve()
    return bpy.data.images.load(str(path), check_existing=True) if path.is_file() else None


def apply_texture_layers(sidecar_path: Path) -> dict:
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    source_dir = Path(str(payload.get("output_gltf") or sidecar_path)).resolve().parent
    applied = overlays = 0
    missing_materials, missing_images = [], []

    for record in payload.get("materials") or []:
        material_name = str(record.get("material_name") or "")
        material = _find_material(material_name)
        if material is None:
            missing_materials.append(material_name)
            continue
        layers = [item for item in (record.get("layers") or []) if item.get("uri")]
        if not layers:
            continue

        tree = material.node_tree
        principled = _principled(material)
        frame = tree.nodes.new("NodeFrame")
        frame.label = "Recovered Softimage TEXTURES2D layers"
        frame.name = "BZ2_Softimage_Texture_Layers"
        coord = tree.nodes.new("ShaderNodeTexCoord")
        coord.label = "Imported/source UV fallback; asset UV stage may replace per layer"
        coord.parent = frame
        coord.location = (-900, 0)

        base_layer = next((item for item in layers if item.get("bound_as_gltf_base_color")), None)
        if base_layer is None:
            base_layer = next((item for item in layers if item.get("role_candidate") == "base_or_default_candidate"), layers[0])
        base_image = _load_image(base_layer, source_dir)
        if base_image is None:
            missing_images.append({"material": material_name, "layer": base_layer})
            continue

        base_tex = tree.nodes.new("ShaderNodeTexImage")
        base_tex.image = base_image
        base_tex.label = f"BASE: {base_layer.get('texture_object', '')}"
        base_tex.parent = frame
        base_tex.location = (-650, 120)
        tree.links.new(coord.outputs["UV"], base_tex.inputs["Vector"])
        current_color = base_tex.outputs["Color"]
        y = -100

        for layer in layers:
            if layer is base_layer:
                continue
            image = _load_image(layer, source_dir)
            if image is None:
                missing_images.append({"material": material_name, "layer": layer})
                continue
            tex = tree.nodes.new("ShaderNodeTexImage")
            tex.image = image
            tex.label = f"{str(layer.get('role_candidate') or 'layer').upper()}: {layer.get('texture_object', '')}"
            tex.parent = frame
            tex.location = (-650, y)
            tree.links.new(coord.outputs["UV"], tex.inputs["Vector"])
            if layer.get("role_candidate") == "alpha_overlay_candidate":
                mix = tree.nodes.new("ShaderNodeMixRGB")
                mix.blend_type = "MIX"
                mix.label = "Softimage alpha-overlay candidate; provisional color mix"
                mix.parent = frame
                mix.location = (-350, y)
                tree.links.new(tex.outputs["Alpha"], mix.inputs[0])
                tree.links.new(current_color, mix.inputs[1])
                tree.links.new(tex.outputs["Color"], mix.inputs[2])
                current_color = mix.outputs[0]
                overlays += 1
            y -= 220

        for link in list(principled.inputs["Base Color"].links):
            tree.links.remove(link)
        tree.links.new(current_color, principled.inputs["Base Color"])
        material["bz2_softimage_texture_layer_count"] = len(record.get("layers") or [])
        material["bz2_softimage_texture_layer_status"] = "base plus alpha overlays; repeat-aware projection UVs/tiling refined by asset-fidelity stage"
        material["bz2_source_material_name"] = material_name
        for layer in record.get("layers") or []:
            order = int(layer.get("order") or 0)
            material[f"bz2_txmp_layer_{order}_role"] = str(layer.get("role_candidate") or "")
            material[f"bz2_txmp_layer_{order}_field86"] = int(layer.get("field_u16_be_86") or 0)
            material[f"bz2_txmp_layer_{order}_field88"] = int(layer.get("field_u16_be_88") or 0)
            material[f"bz2_txmp_layer_{order}_source"] = str(layer.get("resolved_picture") or "")
            material[f"bz2_txmp_layer_{order}_projection_code"] = int(layer.get("projection_or_mapping_code_candidate") or 0)
            material[f"bz2_txmp_layer_{order}_uv_repeat"] = list(layer.get("si_texture2d_repeat_uv") or [])
            material[f"bz2_txmp_layer_{order}_uv_scale"] = list(layer.get("si_texture2d_uv_scale") or [])
            material[f"bz2_txmp_layer_{order}_uv_offset"] = list(layer.get("si_texture2d_uv_offset") or [])
            material[f"bz2_txmp_layer_{order}_crop"] = json.dumps(layer.get("crop_rect_pixels_raw") or {})
            material[f"bz2_txmp_layer_{order}_matrix_rotation"] = list(layer.get("si_texture2d_matrix_rotation_xyz_radians") or [])
            material[f"bz2_txmp_layer_{order}_matrix_scale"] = list(layer.get("si_texture2d_matrix_scale_xyz") or [])
            material[f"bz2_txmp_layer_{order}_matrix_translation"] = list(layer.get("si_texture2d_matrix_translation_xyz") or [])
        applied += 1

    return {
        "applied_material_count": applied,
        "applied_alpha_overlay_count": overlays,
        "missing_material_count": len(missing_materials),
        "missing_materials": missing_materials,
        "missing_image_count": len(missing_images),
        "missing_images": missing_images,
    }


def preserve_render_state(path: Path) -> dict:
    state = json.loads(path.read_text(encoding="utf-8"))
    scene = bpy.context.scene
    scene["bz2_render_state_sidecar"] = str(path)
    scene["bz2_softimage_rendering_type"] = str(state.get("rendering_type") or "")
    scene["bz2_softimage_ambience_rgb"] = list(state.get("ambience_rgb") or [])
    scene["bz2_softimage_global_switches"] = json.dumps(state.get("global_render_switches") or {})
    scene["bz2_softimage_mental_ray"] = json.dumps(state.get("mental_ray") or {})
    scene["bz2_softimage_lens_shaders"] = json.dumps(state.get("lens_shaders") or [])
    scene["bz2_softimage_render_state_status"] = "preserved source metadata; not forced onto Cycles/Eevee"
    resolution = state.get("resolution")
    if resolution and len(resolution) == 2:
        scene.render.resolution_x = int(resolution[0])
        scene.render.resolution_y = int(resolution[1])
        scene.render.resolution_percentage = 100
    return {
        "rendering_type": state.get("rendering_type"),
        "resolution": resolution,
        "ambience_rgb": state.get("ambience_rgb"),
        "lens_shaders": state.get("lens_shaders"),
    }


def main() -> int:
    args = _argv()
    if len(args) not in {5, 6}:
        raise SystemExit(
            "usage: blender --background --python blender_finish_reconstruction.py -- "
            "<scene.gltf> <scene.scene.json> <output.blend> <scene.texture_layers.json> "
            "[scene.model_textures.json] <scene.render_state.json>"
        )
    if bpy is None:
        raise RuntimeError("this script must be executed by Blender Python")

    if len(args) == 6:
        gltf, scene_sidecar, output_blend, layer_sidecar, model_sidecar, render_sidecar = map(Path, args)
    else:
        gltf, scene_sidecar, output_blend, layer_sidecar, render_sidecar = map(Path, args)
        model_sidecar = None

    base_summary = base.reconstruct(gltf, scene_sidecar, output_blend)
    layer_summary = apply_texture_layers(layer_sidecar)
    asset_uv_summary = (
        asset_uvs.apply_asset_uvs(gltf, model_sidecar, layer_sidecar)
        if model_sidecar is not None and model_sidecar.is_file()
        else {"status": "model_projection_sidecar_not_supplied"}
    )
    render_summary = preserve_render_state(render_sidecar)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend.resolve()))
    print(json.dumps({
        "output_blend": str(output_blend),
        "base_reconstruction": base_summary,
        "texture_layers": layer_summary,
        "asset_uvs": asset_uv_summary,
        "render_state": render_summary,
    }, indent=2))
    return 0 if not layer_summary["missing_material_count"] and not layer_summary["missing_image_count"] and not asset_uv_summary.get("missing_object_count", 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
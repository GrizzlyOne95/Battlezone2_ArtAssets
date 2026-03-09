#!/usr/bin/env python3
"""Blender helper for recovered BZ2 prerelease assets.

Usage from Blender:

    blender --python scripts/blender_import_bz2.py -- --xsi-all
    blender --python scripts/blender_import_bz2.py -- --scene modelsdirectory/Archival/adconcept/SCENES/adconcept-mirescene.1-0.dsc

This script imports the real recovered glTF exports for text `.xsi` scenes and
builds collection/placeholder structure for `.dsc` scene descriptors so the
scene organization can be inspected in Blender even when binary `.hrc` geometry
is still unresolved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "artifacts" / "reports"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import recovered BZ2 assets into Blender.")
    parser.add_argument("--reports", default=str(REPORTS_DIR), help="Report directory root.")
    parser.add_argument("--xsi-all", action="store_true", help="Import all recovered glTF exports.")
    parser.add_argument("--xsi", action="append", default=[], help="Import one specific XSI source path.")
    parser.add_argument("--scene", action="append", default=[], help="Build one specific DSC scene collection.")
    parser.add_argument("--clear", action="store_true", help="Clear the current Blender scene first.")
    parser.add_argument(
        "--picture-planes",
        action="store_true",
        help="Create planes for scene picture entries when a converted PNG exists.",
    )
    return parser.parse_args(argv)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_lookup(report_root: Path, report_name: str, key: str) -> dict[str, dict]:
    path = report_root / report_name
    if not path.exists():
        return {}
    payload = load_json(path)
    return {entry[key]: entry for entry in payload.get("entries", []) if key in entry}


def require_bpy():
    try:
        import bpy  # type: ignore
    except Exception as exc:  # pragma: no cover - Blender runtime only
        raise SystemExit(f"This script must run inside Blender: {exc}") from exc
    return bpy


def ensure_collection(bpy, name: str, parent=None):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)

    if parent is None:
        root = bpy.context.scene.collection
        if collection not in list(root.children):
            root.children.link(collection)
    else:
        if collection not in list(parent.children):
            parent.children.link(collection)
    return collection


def sanitize_name(name: str, limit: int = 63) -> str:
    name = name.replace("\\", "/").strip()
    if len(name) <= limit:
        return name
    return name[: limit - 3] + "..."


def unlink_from_other_collections(obj, keep_collection):
    for collection in list(obj.users_collection):
        if collection != keep_collection:
            collection.objects.unlink(obj)


def clear_scene(bpy):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    for data_block in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.images,
        bpy.data.cameras,
        bpy.data.lights,
        bpy.data.collections,
    ):
        for item in list(data_block):
            if getattr(item, "users", 0) == 0:
                data_block.remove(item)


def import_gltf_into_collection(bpy, gltf_path: Path, collection, label: str):
    before_objects = set(bpy.data.objects.keys())
    before_collections = set(bpy.data.collections.keys())

    bpy.ops.import_scene.gltf(filepath=str(gltf_path))

    imported_objects = [bpy.data.objects[name] for name in bpy.data.objects.keys() if name not in before_objects]
    imported_collections = [bpy.data.collections[name] for name in bpy.data.collections.keys() if name not in before_collections]

    root_empty = bpy.data.objects.new(sanitize_name(label), None)
    root_empty.empty_display_type = "PLAIN_AXES"
    collection.objects.link(root_empty)

    for imported in imported_objects:
        if collection not in imported.users_collection:
            collection.objects.link(imported)
        unlink_from_other_collections(imported, collection)
        if imported.parent is None:
            imported.parent = root_empty

    for imported_collection in imported_collections:
        if imported_collection in list(collection.children):
            collection.children.unlink(imported_collection)

    root_empty["bz2_kind"] = "xsi_gltf_root"
    root_empty["bz2_source_label"] = label
    root_empty["bz2_gltf_path"] = str(gltf_path)
    return root_empty


def import_obj_into_collection(bpy, obj_path: Path, collection, label: str):
    before_objects = set(bpy.data.objects.keys())

    if hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=str(obj_path))
    elif hasattr(bpy.ops.import_scene, "obj"):
        bpy.ops.import_scene.obj(filepath=str(obj_path))
    else:  # pragma: no cover - Blender runtime only
        raise RuntimeError("No OBJ importer is available in this Blender build.")

    imported_objects = [bpy.data.objects[name] for name in bpy.data.objects.keys() if name not in before_objects]
    if not imported_objects:
        raise RuntimeError(f"Blender imported no objects from {obj_path}")

    root_empty = bpy.data.objects.new(sanitize_name(label), None)
    root_empty.empty_display_type = "PLAIN_AXES"
    collection.objects.link(root_empty)

    for imported in imported_objects:
        if collection not in imported.users_collection:
            collection.objects.link(imported)
        unlink_from_other_collections(imported, collection)
        if imported.parent is None:
            imported.parent = root_empty

    root_empty["bz2_kind"] = "decoded_hrc_obj_root"
    root_empty["bz2_source_label"] = label
    root_empty["bz2_obj_path"] = str(obj_path)
    return root_empty


def create_image_material(bpy, name: str, png_path: Path):
    material = bpy.data.materials.new(name=name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new(type="ShaderNodeOutputMaterial")
    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    tex = nodes.new(type="ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(png_path), check_existing=True)

    output.location = (300, 0)
    bsdf.location = (0, 0)
    tex.location = (-300, 0)

    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if "Alpha" in tex.outputs:
        links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        material.blend_method = "BLEND"
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return material


def apply_texture_to_object_tree(bpy, root_obj, png_path: Path, material_name: str):
    material = create_image_material(bpy, material_name, png_path)
    stack = [root_obj]
    while stack:
        obj = stack.pop()
        stack.extend(list(obj.children))
        if getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
            continue
        if obj.data.materials:
            for index in range(len(obj.data.materials)):
                obj.data.materials[index] = material
        else:
            obj.data.materials.append(material)


def create_decoded_hrc_mesh(bpy, root: Path, collection, entry: dict, mesh_entry: dict, link_info: dict | None):
    decoded_obj_rel = mesh_entry.get("decoded_obj")
    if decoded_obj_rel:
        decoded_obj_path = root / decoded_obj_rel
        if decoded_obj_path.exists():
            obj = import_obj_into_collection(bpy, decoded_obj_path, collection, entry["name"])
            obj["bz2_import_kind"] = "decoded_hrc_obj"
            obj["bz2_entry_name"] = entry["name"]
            obj["bz2_resolved_path"] = entry["resolved_path"]
            obj["bz2_decoded_obj"] = str(decoded_obj_path)
            png_rel = None
            if link_info:
                png_rel = link_info.get("texture_source_picture_png") or link_info.get("texture_converted_png")
            if png_rel:
                png_path = root / png_rel
                if png_path.exists():
                    apply_texture_to_object_tree(bpy, obj, png_path, f"{sanitize_name(entry['name'])}_scene_tex")
            return obj

    mesh = bpy.data.meshes.new(sanitize_name(entry["name"]) + "_mesh")
    mesh.from_pydata(mesh_entry["decoded_vertices"], [], mesh_entry["decoded_faces"])
    mesh.update()

    obj = bpy.data.objects.new(sanitize_name(entry["name"]), mesh)
    collection.objects.link(obj)
    obj["bz2_import_kind"] = "decoded_hrc_mesh"
    obj["bz2_entry_name"] = entry["name"]
    obj["bz2_resolved_path"] = entry["resolved_path"]
    obj["bz2_decoded_obj"] = mesh_entry.get("decoded_obj", "")
    png_rel = None
    if link_info:
        png_rel = link_info.get("texture_source_picture_png") or link_info.get("texture_converted_png")
    if png_rel:
        png_path = root / png_rel
        if png_path.exists():
            apply_texture_to_object_tree(bpy, obj, png_path, f"{sanitize_name(entry['name'])}_scene_tex")
    return obj


def create_placeholder(
    bpy,
    root: Path,
    collection,
    entry: dict,
    chapter: str,
    link_info: dict | None,
    hrc_lookup: dict[str, dict],
    material_lookup: dict[str, dict],
    mesh_lookup: dict[str, dict],
):
    resolved_path = entry.get("resolved_path")
    if resolved_path and resolved_path in mesh_lookup and mesh_lookup[resolved_path].get("decoded_faces"):
        obj = create_decoded_hrc_mesh(bpy, root, collection, entry, mesh_lookup[resolved_path], link_info)
    else:
        obj = bpy.data.objects.new(sanitize_name(entry["name"]), None)
        obj.empty_display_type = "CUBE" if chapter == "MODELS" else "PLAIN_AXES"
        obj.empty_display_size = 0.5 if chapter == "MODELS" else 0.35
        collection.objects.link(obj)

    obj["bz2_kind"] = "scene_entry"
    obj["bz2_chapter"] = chapter
    obj["bz2_entry_name"] = entry["name"]
    obj["bz2_root"] = bool(entry.get("root"))
    if resolved_path:
        obj["bz2_resolved_path"] = resolved_path
    if entry.get("converted_png"):
        obj["bz2_converted_png"] = entry["converted_png"]
    if link_info:
        if link_info.get("material_name"):
            obj["bz2_scene_material_name"] = link_info["material_name"]
        if link_info.get("material_resolved_path"):
            obj["bz2_scene_material_path"] = link_info["material_resolved_path"]
        if link_info.get("texture_name"):
            obj["bz2_scene_texture_name"] = link_info["texture_name"]
        if link_info.get("texture_resolved_path"):
            obj["bz2_scene_texture_path"] = link_info["texture_resolved_path"]
        if link_info.get("texture_converted_png"):
            obj["bz2_scene_texture_png"] = link_info["texture_converted_png"]
        if link_info.get("texture_source_picture"):
            obj["bz2_scene_texture_source_picture"] = link_info["texture_source_picture"]
        if link_info.get("texture_source_picture_path"):
            obj["bz2_scene_texture_source_path"] = link_info["texture_source_picture_path"]
        if link_info.get("texture_source_picture_png"):
            obj["bz2_scene_texture_source_png"] = link_info["texture_source_picture_png"]

    if resolved_path and resolved_path in hrc_lookup:
        header = hrc_lookup[resolved_path]
        obj["bz2_hrc_kind"] = header["payload_kind"]
        obj["bz2_hrc_class_id"] = header["class_id"]
        obj["bz2_hrc_subtype_id"] = header["subtype_id"]
        obj["bz2_hrc_param"] = header["param_be_float"]
        if "transform_hint" in header:
            obj["bz2_hrc_scale_xyz"] = header["transform_hint"]["scale_xyz"]
            obj["bz2_hrc_rotation_xyz"] = header["transform_hint"]["rotation_xyz_hint"]
            obj["bz2_hrc_translation_xyz"] = header["transform_hint"]["translation_xyz_hint"]
            obj.scale = header["transform_hint"]["scale_xyz"]
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = header["transform_hint"]["rotation_xyz_hint"]
            obj.location = header["transform_hint"]["translation_xyz_hint"]

    if resolved_path and resolved_path in material_lookup:
        material = material_lookup[resolved_path]
        hints = material.get("likely_fields", {})
        obj["bz2_mat_color_hint_b"] = hints.get("color_hint_b", [])
        obj["bz2_mat_specular_hint"] = hints.get("specular_hint", [])
        obj["bz2_mat_hardness_hint"] = hints.get("hardness_hint", 0.0)
        obj["bz2_mat_shading_hint"] = hints.get("shading_type_hint", 0)
    return obj


def create_picture_plane(
    bpy,
    collection,
    entry: dict,
    root: Path,
    hrc_lookup: dict[str, dict],
    material_lookup: dict[str, dict],
    mesh_lookup: dict[str, dict],
):
    png_rel = entry.get("converted_png")
    if not png_rel:
        return create_placeholder(bpy, root, collection, entry, "PICTURES", None, hrc_lookup, material_lookup, mesh_lookup)

    png_path = root / png_rel
    if not png_path.exists():
        return create_placeholder(bpy, root, collection, entry, "PICTURES", None, hrc_lookup, material_lookup, mesh_lookup)

    bpy.ops.mesh.primitive_plane_add(size=1.0)
    obj = bpy.context.active_object
    obj.name = sanitize_name(entry["name"])

    for user_collection in list(obj.users_collection):
        if user_collection != collection:
            user_collection.objects.unlink(obj)
    if collection not in obj.users_collection:
        collection.objects.link(obj)

    material = bpy.data.materials.new(name=f"{obj.name}_mat")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new(type="ShaderNodeOutputMaterial")
    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    tex = nodes.new(type="ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(png_path), check_existing=True)

    output.location = (300, 0)
    bsdf.location = (0, 0)
    tex.location = (-300, 0)

    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if "Alpha" in tex.outputs:
        links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        material.blend_method = "BLEND"
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    if obj.data.materials:
        obj.data.materials[0] = material
    else:
        obj.data.materials.append(material)

    obj["bz2_kind"] = "picture_plane"
    obj["bz2_entry_name"] = entry["name"]
    obj["bz2_converted_png"] = entry["converted_png"]
    return obj


def import_xsi_exports(bpy, report_root: Path, requested_sources: list[str] | None) -> list[str]:
    payload = load_json(report_root / "xsi_exports.json")
    entries = payload["entries"]

    if requested_sources is not None:
        requested = {item.replace("\\", "/") for item in requested_sources}
        entries = [entry for entry in entries if entry["source"].replace("\\", "/") in requested]

    root_collection = ensure_collection(bpy, "BZ2_XSI_Exports")
    imported = []
    for entry in entries:
        gltf_path = ROOT / entry["gltf"]
        if not gltf_path.exists():
            continue
        import_gltf_into_collection(bpy, gltf_path, root_collection, entry["source"])
        imported.append(entry["source"])
    return imported


def select_scene_entries(payload: dict, requested_scenes: list[str]) -> list[dict]:
    if not requested_scenes:
        return []

    wanted = [item.replace("\\", "/") for item in requested_scenes]
    selected = []
    for scene in payload["entries"]:
        path = scene["path"].replace("\\", "/")
        if path in wanted or any(path.endswith(item) for item in wanted):
            selected.append(scene)
    return selected


def build_scene_collections(bpy, report_root: Path, requested_scenes: list[str], picture_planes: bool) -> list[str]:
    payload = load_json(report_root / "scene_dependencies.json")
    selected = select_scene_entries(payload, requested_scenes)
    imported = []
    hrc_lookup = load_lookup(report_root, "hrc_headers.json", "path")
    material_lookup = load_lookup(report_root, "binary_materials.json", "path")
    mesh_lookup = load_lookup(report_root, "hrc_mesh_like.json", "path")

    root_collection = ensure_collection(bpy, "BZ2_DSC_Scenes")
    for scene in selected:
        scene_name = sanitize_name(Path(scene["path"]).stem)
        scene_collection = ensure_collection(bpy, scene_name, root_collection)
        scene_root = bpy.data.objects.new(f"{scene_name}__scene", None)
        scene_root.empty_display_type = "ARROWS"
        scene_collection.objects.link(scene_root)
        scene_root["bz2_kind"] = "scene_root"
        scene_root["bz2_scene_path"] = scene["path"]
        scene_root["bz2_header"] = scene["header"]
        link_lookup = {link["model_name"]: link for link in scene.get("model_material_texture_links", []) if link.get("model_name")}

        for chapter in scene["chapters"]:
            chapter_collection = ensure_collection(bpy, chapter["chapter"], scene_collection)
            for entry in chapter["entries"]:
                if chapter["chapter"] == "PICTURES" and picture_planes:
                    obj = create_picture_plane(bpy, chapter_collection, entry, ROOT, hrc_lookup, material_lookup, mesh_lookup)
                else:
                    obj = create_placeholder(
                        bpy,
                        ROOT,
                        chapter_collection,
                        entry,
                        chapter["chapter"],
                        link_lookup.get(entry["name"]),
                        hrc_lookup,
                        material_lookup,
                        mesh_lookup,
                    )
                if entry.get("root"):
                    obj.parent = scene_root

        imported.append(scene["path"])
    return imported


def main(argv: list[str]) -> int:
    bpy = require_bpy()
    args = parse_args(argv)
    report_root = Path(args.reports).resolve()

    if args.clear:
        clear_scene(bpy)

    imported_xsi = []
    if args.xsi_all:
        imported_xsi = import_xsi_exports(bpy, report_root, None)
    elif args.xsi:
        imported_xsi = import_xsi_exports(bpy, report_root, args.xsi)

    imported_scenes = build_scene_collections(bpy, report_root, args.scene, args.picture_planes)

    print(
        json.dumps(
            {
                "imported_xsi_count": len(imported_xsi),
                "imported_scene_count": len(imported_scenes),
                "imported_xsi": imported_xsi,
                "imported_scenes": imported_scenes,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    raise SystemExit(main(argv))

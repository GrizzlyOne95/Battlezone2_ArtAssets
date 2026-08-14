#!/usr/bin/env python3
"""Restore ordered Softimage TEXTURES2D layers to a reconstructed BZ2 glTF.

Fixes two fidelity gaps in the first DSC material exporter: code-401 relations
can occur more than once per material, and TXMP picture paths come from many
historical workstation/server roots. The first/default layer becomes portable
glTF base color; later layers remain explicit in extras/sidecar for Blender.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import bz2_dsc_material_gltf as dscmat
import softimage_pic

PIC_EXTS = (".pic", ".PIC", ".png", ".PNG", ".tga", ".TGA")


def parse_txmp(data: bytes) -> dict:
    marker = data.find(b"TXMP")
    if marker < 0:
        raise ValueError("TXMP marker not found")
    end = data.find(b"\0", marker + 4)
    if end < 0:
        raise ValueError("TXMP picture path is not NUL terminated")
    tail = data[end + 1 :]

    def u16le(offset: int) -> int | None:
        return int.from_bytes(tail[offset : offset + 2], "little") if offset + 2 <= len(tail) else None

    field87, field89 = u16le(87), u16le(89)
    role = "alpha_overlay_candidate" if field89 == 1 else "base_or_default_candidate"
    if field87 == 1 and field89 != 1:
        role = "bump_candidate"
    return {
        "raw_source_path": data[marker + 4 : end].decode("latin-1", errors="replace"),
        "txmp_payload_u16le_87": field87,
        "txmp_payload_u16le_89": field89,
        "role_candidate": role,
        "txmp_tail_hex": tail[:167].hex(),
    }


def _image_candidates(path: str) -> list[str]:
    if any(path.lower().endswith(ext.lower()) for ext in PIC_EXTS):
        return [path]
    return [path + ext for ext in PIC_EXTS]


def resolve_picture(store: dscmat.SourceStore, raw_path: str, scene_prefix: str) -> str | None:
    normalized = raw_path.replace("\\", "/").strip()
    lower = normalized.lower()
    candidates: list[str] = []
    if "/modelsdirectory/" in lower:
        at = lower.index("/modelsdirectory/") + len("/modelsdirectory/")
        candidates += _image_candidates(normalized[at:])
    if "/pictures/" in lower:
        at = lower.rindex("/pictures/") + 1
        tail = normalized[at:]
        candidates += _image_candidates(f"{scene_prefix.strip('/')}/{tail}")
        candidates += _image_candidates(tail)
    basename = Path(normalized).name
    for name in _image_candidates(basename):
        candidates += [f"{scene_prefix.strip('/')}/PICTURES/{name}", name]
    for candidate in dict.fromkeys(candidates):
        if store.exists(candidate):
            return candidate

    prefix = scene_prefix.strip("/").lower() + "/"
    ranked: list[tuple[int, str]] = []
    for name in _image_candidates(basename):
        found = store.find_basename(Path(name).name)
        if found:
            score = (100 if found.lower().startswith(prefix) else 0) + (20 if "/pictures/" in found.lower() else 0)
            ranked.append((score, found))
    return max(ranked, default=(0, None), key=lambda item: (item[0], item[1] or ""))[1]


def find_txt(store: dscmat.SourceStore, texture_name: str, scene_prefix: str) -> str | None:
    filename = texture_name + ".txt"
    preferred = f"{scene_prefix.strip('/')}/TEXTURES2D/{filename}"
    return preferred if store.exists(preferred) else store.find_basename(filename, scene_prefix)


def export_pic(store: dscmat.SourceStore, logical: str, texture_object: str, out_dir: Path) -> dict:
    data = store.read(logical)
    if Path(logical).suffix.lower() != ".pic":
        return {"status": "unsupported_picture_format", "source_picture": logical, "uri": None}
    info = softimage_pic.inspect_pic_bytes(data)
    if info.get("kind") != "softimage_pic":
        return {"status": info.get("kind"), "source_picture": logical, "uri": None}
    rgba, decoded = softimage_pic.decode_pic_bytes(data)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", texture_object)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(logical).stem)
    out = out_dir / f"{safe}__{stem}.png"
    softimage_pic.write_rgba_png(out, int(decoded["width"]), int(decoded["height"]), rgba)
    alpha = rgba[3::4]
    return {
        "status": "ok",
        "source_picture": logical,
        "uri": f"textures/{out.name}",
        "width": int(decoded["width"]),
        "height": int(decoded["height"]),
        "has_nonopaque_alpha": any(a != 255 for a in alpha),
        "alpha_min": min(alpha) if alpha else 255,
        "alpha_max": max(alpha) if alpha else 255,
    }


def restore_layers(input_gltf: Path, scene_dsc: Path, asset_source: Path, scene_prefix: str, output_gltf: Path) -> dict:
    gltf = json.loads(input_gltf.read_text(encoding="utf-8"))
    chapters, relations = dscmat.parse_dsc(scene_dsc)
    scene_materials = chapters.get("MATERIALS", [])
    texture_objects = chapters.get("TEXTURES2D", [])
    store = dscmat.open_store(asset_source)

    by_material: dict[int, list[dict]] = {}
    for relation in relations:
        if relation["source_chapter"] == "MATERIALS" and relation["target_chapter"] == "TEXTURES2D" and relation["relation_code"] == 401:
            by_material.setdefault(relation["source_index"], []).append(relation)

    output_gltf.parent.mkdir(parents=True, exist_ok=True)
    texture_dir = output_gltf.parent / "textures"
    texture_dir.mkdir(parents=True, exist_ok=True)
    images, textures = gltf.setdefault("images", []), gltf.setdefault("textures", [])
    image_by_uri = {x.get("uri"): i for i, x in enumerate(images) if x.get("uri")}
    tex_by_image = {x.get("source"): i for i, x in enumerate(textures) if x.get("source") is not None}
    gltf_mat_by_name = {m.get("name"): i for i, m in enumerate(gltf.get("materials", []))}

    report_materials, missing, unresolved = [], [], []
    multi = overlays = base_bound = 0
    for material_index, edges in by_material.items():
        if material_index >= len(scene_materials):
            continue
        material_name = scene_materials[material_index]
        gltf_index = gltf_mat_by_name.get(material_name)
        if gltf_index is None:
            missing.append(material_name)
            continue
        layers = []
        for order, edge in enumerate(edges):
            if edge["target_index"] >= len(texture_objects):
                continue
            tex_name = texture_objects[edge["target_index"]]
            txt = find_txt(store, tex_name, scene_prefix)
            layer = {"order": order, "texture_object": tex_name, "source_txt": txt, "relation_code": 401}
            if txt:
                try:
                    layer.update(parse_txmp(store.read(txt)))
                    picture = resolve_picture(store, layer["raw_source_path"], scene_prefix)
                    layer["resolved_picture"] = picture
                    if picture:
                        layer.update(export_pic(store, picture, tex_name, texture_dir))
                        uri = layer.get("uri")
                        if uri:
                            if uri not in image_by_uri:
                                image_by_uri[uri] = len(images)
                                images.append({"uri": uri})
                            image_index = image_by_uri[uri]
                            if image_index not in tex_by_image:
                                tex_by_image[image_index] = len(textures)
                                textures.append({"source": image_index})
                            layer["gltf_image_index"] = image_index
                            layer["gltf_texture_index"] = tex_by_image[image_index]
                    else:
                        layer["status"] = "picture_unresolved"
                        unresolved.append({"material": material_name, **layer})
                except Exception as exc:
                    layer["status"] = f"decode_error:{type(exc).__name__}:{exc}"
            else:
                layer["status"] = "missing_texture_object_file"
            overlays += layer.get("role_candidate") == "alpha_overlay_candidate"
            layers.append(layer)
        multi += len(layers) > 1
        usable = [x for x in layers if x.get("gltf_texture_index") is not None]
        base = next((x for x in usable if x.get("role_candidate") == "base_or_default_candidate"), usable[0] if usable else None)
        material = gltf["materials"][gltf_index]
        if base:
            material.setdefault("pbrMetallicRoughness", {})["baseColorTexture"] = {"index": int(base["gltf_texture_index"])}
            base["bound_as_gltf_base_color"] = True
            base_bound += 1
        material.setdefault("extras", {})["bz2_softimage_texture_layers"] = layers
        report_materials.append({
            "material_index": material_index,
            "material_name": material_name,
            "gltf_material_index": gltf_index,
            "layer_count": len(layers),
            "layers": layers,
        })

    if not images:
        gltf.pop("images", None)
    if not textures:
        gltf.pop("textures", None)
    output_gltf.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    sidecar = {
        "schema": "bz2-softimage-texture-layers-v1",
        "input_gltf": str(input_gltf),
        "scene_dsc": str(scene_dsc),
        "asset_source": str(asset_source),
        "scene_prefix": scene_prefix,
        "output_gltf": str(output_gltf),
        "material_with_401_count": len(by_material),
        "multi_layer_material_count": multi,
        "overlay_candidate_layer_count": overlays,
        "base_color_bound_count": base_bound,
        "missing_gltf_material_count": len(missing),
        "missing_gltf_materials": missing,
        "unresolved_picture_count": len(unresolved),
        "unresolved_pictures": unresolved,
        "materials": report_materials,
        "notes": [
            "DSC relation code 401 is preserved in source order and may occur multiple times per material.",
            "TXMP post-NUL payload u16le field 89 value 1 is an alpha-overlay candidate from corpus correlation, not proven emissive semantics.",
            "The first/default layer is portable glTF base color; later layers remain explicit for Blender reconstruction.",
            "Historical absolute picture paths resolve scene-locally first; the raw source path is retained.",
        ],
    }
    output_gltf.with_suffix(".texture_layers.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return sidecar


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_gltf", type=Path)
    p.add_argument("scene_dsc", type=Path)
    p.add_argument("asset_source", type=Path)
    p.add_argument("scene_prefix")
    p.add_argument("output_gltf", type=Path)
    a = p.parse_args()
    r = restore_layers(a.input_gltf, a.scene_dsc, a.asset_source, a.scene_prefix, a.output_gltf)
    print(json.dumps({k: r[k] for k in (
        "multi_layer_material_count", "overlay_candidate_layer_count", "base_color_bound_count",
        "unresolved_picture_count", "missing_gltf_material_count", "output_gltf"
    )}, indent=2))
    return 1 if r["unresolved_picture_count"] or r["missing_gltf_material_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

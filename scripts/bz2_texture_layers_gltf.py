#!/usr/bin/env python3
"""Restore ordered Softimage TEXTURES2D layers to a reconstructed BZ2 glTF.

Fixes two fidelity gaps in the first DSC material exporter: code-401 relations
can occur more than once per material, and TXMP picture paths come from many
historical workstation/server roots. The first/default layer becomes portable
glTF base color; later layers remain explicit in extras/sidecar for Blender.

TXMP contains texture-placement state in addition to the image path. The common
SI_Texture2D fields that have been source/corpus correlated are decoded here so
both material-level code-401 layers and model-local code-400 projections use the
same field names. Portable glTF base textures carry the confirmed U/V scale and
offset through KHR_texture_transform; projection-operator UV generation remains
a Blender/source-format concern.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path

import bz2_dsc_material_gltf as dscmat
import softimage_pic

PIC_EXTS = (".pic", ".PIC", ".png", ".PNG", ".tga", ".TGA")
SI_TEXTURE2D_UV_TRANSFORM_OFFSET = 6
SI_TEXTURE2D_UV_TRANSFORM_SIZE = 16
SI_TEXTURE2D_SRT_OFFSET = 90
SI_TEXTURE2D_SRT_SIZE = 36


def parse_txmp(data: bytes) -> dict:
    """Decode common legacy SI_Texture2D/TXMP state without guessing operators.

    The byte offsets are measured from the first byte after the NUL-terminated
    TXMP image path. +6 and +90 are source-correlated; the crop rectangle and
    duplicate tail are corpus-validated. +24/+76/+78 remain raw operator/auxiliary
    values until their complete enum/flag names are authoritative.
    """
    marker = data.find(b"TXMP")
    if marker < 0:
        raise ValueError("TXMP marker not found")
    end = data.find(b"\0", marker + 4)
    if end < 0:
        raise ValueError("TXMP picture path is not NUL terminated")
    tail = data[end + 1 :]

    # PATCH: the older probe read u16 values starting at odd offsets +87/+89 as
    # little-endian. Corpus inspection shows the actual scalar boundaries are
    # aligned big-endian u16 words at +86/+88. The old reads happened to retain
    # the low values often enough for the role heuristic to work, but +89 also
    # consumes the first byte of the +90 matrix on rotated records. Decode the
    # aligned fields authoritatively and retain the historical names only as
    # compatibility aliases for downstream sidecars.
    field86 = int.from_bytes(tail[86:88], "big") if len(tail) >= 88 else None
    field88 = int.from_bytes(tail[88:90], "big") if len(tail) >= 90 else None
    role = (
        "alpha_overlay_candidate"
        if field88 == 1
        else "base_or_default_candidate"
    )
    if field86 == 1 and field88 != 1:
        role = "bump_candidate"

    result = {
        "raw_source_path": data[marker + 4 : end].decode("latin-1", errors="replace"),
        "field_u16_be_86": field86,
        "field_u16_be_88": field88,
        "txmp_role_field_alignment_status": "confirmed_aligned_big_endian_u16_v1",
        "txmp_payload_u16le_87": field86,
        "txmp_payload_u16le_89": field88,
        "txmp_payload_u16le_87_89_status": "deprecated_compatibility_aliases_of_field_u16_be_86_88",
        "role_candidate": role,
        "txmp_tail_hex": tail[:167].hex(),
        "txmp_common_decode_status": "path_and_role_only",
    }

    if len(tail) >= SI_TEXTURE2D_UV_TRANSFORM_OFFSET + SI_TEXTURE2D_UV_TRANSFORM_SIZE:
        uv_transform = list(
            struct.unpack_from(">4f", tail, SI_TEXTURE2D_UV_TRANSFORM_OFFSET)
        )
        result.update(
            {
                "txmp_common_decode_status": "confirmed_uv_transform",
                "texture_2d_transform_candidate": uv_transform,
                "si_texture2d_uv_transform_status": "confirmed_from_dotxsi_source_and_corpus_v1",
                "si_texture2d_uv_scale": uv_transform[0:2],
                "si_texture2d_uv_offset": uv_transform[2:4],
            }
        )

    if len(tail) >= 26:
        result.update(
            {
                "field_u16_be_22": int.from_bytes(tail[22:24], "big"),
                "projection_or_mapping_code_candidate": int.from_bytes(
                    tail[24:26], "big"
                ),
            }
        )

    # PATCH: +26..+57 is an aligned contiguous eight-float block. Earlier
    # production output retained only +26/+30. Preserve every scalar now so
    # repeat/alternate/layer-effect correlations can be solved from sidecars
    # without another source-archive pass. No semantic labels are assigned yet.
    scalar_block = []
    for offset in range(26, 58, 4):
        if len(tail) < offset + 4:
            break
        value = struct.unpack_from(">f", tail, offset)[0]
        result[f"field_f32_be_{offset}"] = value
        scalar_block.append(value)
    if len(scalar_block) == 8:
        result["field_f32_be_26_54_raw"] = scalar_block
        result["field_f32_be_26_54_status"] = "aligned_contiguous_raw_block_v1"

    if len(tail) >= 74:
        crop_rect = {
            "x0": int.from_bytes(tail[60:62], "big"),
            "x1": int.from_bytes(tail[62:64], "big"),
            "y0": int.from_bytes(tail[64:66], "big"),
            "y1": int.from_bytes(tail[66:68], "big"),
        }
        crop_tail = [
            int.from_bytes(tail[68:70], "big"),
            int.from_bytes(tail[70:72], "big"),
            int.from_bytes(tail[72:74], "big"),
        ]
        result.update(
            {
                "crop_enabled_raw_u16_be": int.from_bytes(tail[58:60], "big"),
                "crop_rect_pixels_raw": crop_rect,
                "crop_rect_trailing_duplicate_raw": crop_tail,
                "crop_rect_trailing_duplicate_status": (
                    "confirmed_x1_y0_y1_duplicate_v1"
                    if crop_tail
                    == [crop_rect["x1"], crop_rect["y0"], crop_rect["y1"]]
                    else "unexpected_nonduplicate"
                ),
                # Compatibility alias. Older sidecars called this repeat state;
                # corpus validation proves it duplicates x1/y0/y1 instead.
                "crop_repeat_raw": crop_tail,
                "crop_repeat_raw_status": "deprecated_misnamed_alias_of_crop_rect_trailing_duplicate_raw",
            }
        )

    if len(tail) >= 80:
        result.update(
            {
                "field_u16_be_76": int.from_bytes(tail[76:78], "big"),
                "field_u16_be_78": int.from_bytes(tail[78:80], "big"),
            }
        )
    if len(tail) >= 86:
        result.update(
            {
                "field_u16_be_80": int.from_bytes(tail[80:82], "big"),
                "field_u16_be_82": int.from_bytes(tail[82:84], "big"),
                "field_u16_be_84": int.from_bytes(tail[84:86], "big"),
            }
        )

    if len(tail) >= SI_TEXTURE2D_SRT_OFFSET + SI_TEXTURE2D_SRT_SIZE:
        result.update(
            {
                "txmp_common_decode_status": "confirmed_uv_and_matrix_transform",
                "si_texture2d_matrix_srt_status": "confirmed_from_dotxsi_corpus_v1",
                "si_texture2d_matrix_srt_offset": SI_TEXTURE2D_SRT_OFFSET,
                "si_texture2d_matrix_rotation_xyz_radians": list(
                    struct.unpack_from(">3f", tail, SI_TEXTURE2D_SRT_OFFSET)
                ),
                "si_texture2d_matrix_scale_xyz": list(
                    struct.unpack_from(">3f", tail, SI_TEXTURE2D_SRT_OFFSET + 12)
                ),
                "si_texture2d_matrix_translation_xyz": list(
                    struct.unpack_from(">3f", tail, SI_TEXTURE2D_SRT_OFFSET + 24)
                ),
            }
        )
    return result


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
            score = (100 if found.lower().startswith(prefix) else 0) + (
                20 if "/pictures/" in found.lower() else 0
            )
            ranked.append((score, found))
    return max(
        ranked, default=(0, None), key=lambda item: (item[0], item[1] or "")
    )[1]


def find_txt(store: dscmat.SourceStore, texture_name: str, scene_prefix: str) -> str | None:
    filename = texture_name + ".txt"
    preferred = f"{scene_prefix.strip('/')}/TEXTURES2D/{filename}"
    return preferred if store.exists(preferred) else store.find_basename(filename, scene_prefix)


def export_pic(store: dscmat.SourceStore, logical: str, texture_object: str, out_dir: Path) -> dict:
    data = store.read(logical)
    if Path(logical).suffix.lower() != ".pic":
        return {
            "status": "unsupported_picture_format",
            "source_picture": logical,
            "uri": None,
        }
    info = softimage_pic.inspect_pic_bytes(data)
    if info.get("kind") != "softimage_pic":
        return {"status": info.get("kind"), "source_picture": logical, "uri": None}
    rgba, decoded = softimage_pic.decode_pic_bytes(data)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", texture_object)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(logical).stem)
    out = out_dir / f"{safe}__{stem}.png"
    softimage_pic.write_rgba_png(
        out, int(decoded["width"]), int(decoded["height"]), rgba
    )
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


def _portable_texture_transform(layer: dict) -> dict | None:
    """Return confirmed image-space scale/offset/crop as KHR_texture_transform.

    The crop rectangle is stored as inclusive source-pixel coordinates. A full
    0..W-1 / 0..H-1 rectangle therefore composes to identity. This keeps the
    portable base texture aligned with the same confirmed image-space state used
    by the Blender asset-fidelity path.
    """
    scale = layer.get("si_texture2d_uv_scale")
    offset = layer.get("si_texture2d_uv_offset")
    if not (
        isinstance(scale, list)
        and len(scale) == 2
        and isinstance(offset, list)
        and len(offset) == 2
    ):
        return None
    su, sv = float(scale[0]), float(scale[1])
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
    values = [su, sv, ou, ov]
    if all(abs(value - expected) <= 1.0e-8 for value, expected in zip(values, [1.0, 1.0, 0.0, 0.0])):
        return None
    return {"scale": values[:2], "offset": values[2:]}


def _mark_extension_used(gltf: dict, extension: str) -> None:
    used = gltf.setdefault("extensionsUsed", [])
    if extension not in used:
        used.append(extension)


def restore_layers(
    input_gltf: Path,
    scene_dsc: Path,
    asset_source: Path,
    scene_prefix: str,
    output_gltf: Path,
) -> dict:
    gltf = json.loads(input_gltf.read_text(encoding="utf-8"))
    chapters, relations = dscmat.parse_dsc(scene_dsc)
    scene_materials = chapters.get("MATERIALS", [])
    texture_objects = chapters.get("TEXTURES2D", [])
    store = dscmat.open_store(asset_source)

    by_material: dict[int, list[dict]] = {}
    for relation in relations:
        if (
            relation["source_chapter"] == "MATERIALS"
            and relation["target_chapter"] == "TEXTURES2D"
            and relation["relation_code"] == 401
        ):
            by_material.setdefault(relation["source_index"], []).append(relation)

    output_gltf.parent.mkdir(parents=True, exist_ok=True)
    texture_dir = output_gltf.parent / "textures"
    texture_dir.mkdir(parents=True, exist_ok=True)
    images, textures = gltf.setdefault("images", []), gltf.setdefault("textures", [])
    image_by_uri = {x.get("uri"): i for i, x in enumerate(images) if x.get("uri")}
    tex_by_image = {
        x.get("source"): i
        for i, x in enumerate(textures)
        if x.get("source") is not None
    }
    gltf_mat_by_name = {
        m.get("name"): i for i, m in enumerate(gltf.get("materials", []))
    }

    report_materials, missing, unresolved = [], [], []
    multi = overlays = base_bound = transformed_base = 0
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
            layer = {
                "order": order,
                "texture_object": tex_name,
                "source_txt": txt,
                "relation_code": 401,
            }
            if txt:
                try:
                    layer.update(parse_txmp(store.read(txt)))
                    picture = resolve_picture(
                        store, layer["raw_source_path"], scene_prefix
                    )
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
        base = next(
            (x for x in usable if x.get("role_candidate") == "base_or_default_candidate"),
            usable[0] if usable else None,
        )
        material = gltf["materials"][gltf_index]
        if base:
            texture_info: dict = {"index": int(base["gltf_texture_index"])}
            transform = _portable_texture_transform(base)
            if transform:
                texture_info["extensions"] = {"KHR_texture_transform": transform}
                _mark_extension_used(gltf, "KHR_texture_transform")
                base["bound_khr_texture_transform"] = transform
                transformed_base += 1
            material.setdefault("pbrMetallicRoughness", {})[
                "baseColorTexture"
            ] = texture_info
            base["bound_as_gltf_base_color"] = True
            base_bound += 1
        material.setdefault("extras", {})["bz2_softimage_texture_layers"] = layers
        report_materials.append(
            {
                "material_index": material_index,
                "material_name": material_name,
                "gltf_material_index": gltf_index,
                "layer_count": len(layers),
                "layers": layers,
            }
        )

    if not images:
        gltf.pop("images", None)
    if not textures:
        gltf.pop("textures", None)
    output_gltf.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    sidecar = {
        "schema": "bz2-softimage-texture-layers-v2",
        "input_gltf": str(input_gltf),
        "scene_dsc": str(scene_dsc),
        "asset_source": str(asset_source),
        "scene_prefix": scene_prefix,
        "output_gltf": str(output_gltf),
        "material_with_401_count": len(by_material),
        "multi_layer_material_count": multi,
        "overlay_candidate_layer_count": overlays,
        "base_color_bound_count": base_bound,
        "base_color_texture_transform_count": transformed_base,
        "missing_gltf_material_count": len(missing),
        "missing_gltf_materials": missing,
        "unresolved_picture_count": len(unresolved),
        "unresolved_pictures": unresolved,
        "materials": report_materials,
        "notes": [
            "DSC relation code 401 is preserved in source order and may occur multiple times per material.",
            "TXMP +6 and the confirmed source-pixel crop rectangle are composed into KHR_texture_transform for portable base textures when the result is non-identity.",
            "TXMP +24 is preserved as projection/operator state; generated projection UVs are handled by the Blender asset-fidelity stage rather than being guessed into glTF TEXCOORD_0.",
            "TXMP +26..+57 is preserved as an aligned eight-float raw scalar block so remaining placement/effect semantics can be solved without losing source state.",
            "TXMP +80/+82/+84 are also retained as aligned raw u16 words; all are zero in the current 664-record archival validation set but future source packages may vary.",
            "TXMP +86/+88 are aligned big-endian u16 fields; the historical u16le +87/+89 names are retained only as compatibility aliases and are not the binary field boundaries.",
            "The +88 value 1 remains an alpha-overlay candidate and +86 value 1 remains a bump candidate from corpus correlation; their exact legacy semantic names are not promoted yet.",
            "The first/default layer is portable glTF base color; later layers remain explicit for Blender reconstruction.",
            "Historical absolute picture paths resolve scene-locally first; the raw source path is retained.",
        ],
    }
    output_gltf.with_suffix(".texture_layers.json").write_text(
        json.dumps(sidecar, indent=2), encoding="utf-8"
    )
    return sidecar


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_gltf", type=Path)
    p.add_argument("scene_dsc", type=Path)
    p.add_argument("asset_source", type=Path)
    p.add_argument("scene_prefix")
    p.add_argument("output_gltf", type=Path)
    a = p.parse_args()
    r = restore_layers(
        a.input_gltf, a.scene_dsc, a.asset_source, a.scene_prefix, a.output_gltf
    )
    print(
        json.dumps(
            {
                k: r[k]
                for k in (
                    "multi_layer_material_count",
                    "overlay_candidate_layer_count",
                    "base_color_bound_count",
                    "base_color_texture_transform_count",
                    "unresolved_picture_count",
                    "missing_gltf_material_count",
                    "output_gltf",
                )
            },
            indent=2,
        )
    )
    return 1 if r["unresolved_picture_count"] or r["missing_gltf_material_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
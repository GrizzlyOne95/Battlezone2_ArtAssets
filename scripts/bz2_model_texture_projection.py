#!/usr/bin/env python3
"""Preserve DSC model-local Softimage texture/projection state (relation code 400).

Code 400 is distinct from material-level TEXTURES2D code 401. High-resolution
Softimage source meshes often carry zero baked UVs, so this layer deliberately
records projection state without pretending that TEXCOORD_0 is authoritative.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path

import bz2_dsc_material_gltf as dscmat
import bz2_texture_layers_gltf as texture_layers
import softimage_pic

VERSION_RE = re.compile(r"\.\d+-\d+$")
SI_TEXTURE2D_SRT_OFFSET = 90
SI_TEXTURE2D_SRT_SIZE = 36


def parse_projection(data: bytes) -> dict:
    result = texture_layers.parse_txmp(data)
    marker = data.find(b"TXMP")
    end = data.find(b"\0", marker + 4)
    if marker < 0 or end < 0:
        raise ValueError("invalid TXMP record")
    payload = data[end + 1 :]
    if len(payload) < 74:
        result["projection_record_status"] = "short"
        return result

    result.update(
        {
            "projection_record_status": "decoded_structural_v1",
            "scope_u32_be": int.from_bytes(payload[0:4], "big"),
            "scope_u16_be": int.from_bytes(payload[4:6], "big"),
            "texture_2d_transform_candidate": list(struct.unpack_from(">4f", payload, 6)),
            "field_u16_be_22": int.from_bytes(payload[22:24], "big"),
            "projection_or_mapping_code_candidate": int.from_bytes(payload[24:26], "big"),
            "field_f32_be_26": struct.unpack_from(">f", payload, 26)[0],
            "field_f32_be_30": struct.unpack_from(">f", payload, 30)[0],
            "crop_enabled_raw_u16_be": int.from_bytes(payload[58:60], "big"),
            "crop_rect_pixels_raw": {
                "x0": int.from_bytes(payload[60:62], "big"),
                "x1": int.from_bytes(payload[62:64], "big"),
                "y0": int.from_bytes(payload[64:66], "big"),
                "y1": int.from_bytes(payload[66:68], "big"),
            },
            "crop_repeat_raw": [
                int.from_bytes(payload[68:70], "big"),
                int.from_bytes(payload[70:72], "big"),
                int.from_bytes(payload[72:74], "big"),
            ],
        }
    )

    # Source-corpus validation against 15,150 TXMP records plus surviving
    # readable SI_Texture2D blocks confirms that post-path offset +90 stores
    # compact texture-matrix RXYZ/SXYZ/TXYZ values. Rotation is in radians.
    # This is texture/projection state, not the HRC/model transform and not the
    # separate four-float 2D transform candidate retained at +6.
    if len(payload) >= SI_TEXTURE2D_SRT_OFFSET + SI_TEXTURE2D_SRT_SIZE:
        result.update(
            {
                "projection_record_status": "decoded_structural_v2",
                "si_texture2d_matrix_srt_status": "confirmed_from_dotxsi_corpus_v1",
                "si_texture2d_matrix_srt_offset": SI_TEXTURE2D_SRT_OFFSET,
                "si_texture2d_matrix_rotation_xyz_radians": list(
                    struct.unpack_from(">3f", payload, SI_TEXTURE2D_SRT_OFFSET)
                ),
                "si_texture2d_matrix_scale_xyz": list(
                    struct.unpack_from(">3f", payload, SI_TEXTURE2D_SRT_OFFSET + 12)
                ),
                "si_texture2d_matrix_translation_xyz": list(
                    struct.unpack_from(">3f", payload, SI_TEXTURE2D_SRT_OFFSET + 24)
                ),
            }
        )
    return result


def _strip_version(name: str) -> str:
    return VERSION_RE.sub("", name)


def resolve_gltf_node(model_name: str, nodes: list[dict]) -> int | None:
    """Resolve a DSC namespaced/versioned model to an HRC/glTF node name."""
    stem = _strip_version(model_name)
    by_name = {
        str(node.get("name")): index
        for index, node in enumerate(nodes)
        if node.get("name")
    }
    if stem in by_name:
        return by_name[stem]
    candidates = [
        (len(name), index)
        for name, index in by_name.items()
        if stem.endswith("-" + name)
    ]
    return max(candidates, default=(0, None))[1]


def export_picture(
    store: dscmat.SourceStore,
    logical_source: str,
    texture_object: str,
    output_dir: Path,
) -> dict:
    data = store.read(logical_source)
    info = softimage_pic.inspect_pic_bytes(data)
    if info.get("kind") != "softimage_pic":
        return {"status": info.get("kind"), "uri": None}
    rgba, decoded = softimage_pic.decode_pic_bytes(data)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_texture = re.sub(r"[^A-Za-z0-9_.-]+", "_", texture_object)
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(logical_source).stem)
    destination = output_dir / f"{safe_texture}__{safe_stem}.png"
    softimage_pic.write_rgba_png(
        destination,
        int(decoded["width"]),
        int(decoded["height"]),
        rgba,
    )
    return {
        "status": "ok",
        "uri": f"textures/{destination.name}",
        "width": int(decoded["width"]),
        "height": int(decoded["height"]),
    }


def augment_model_projections(
    input_gltf: Path,
    scene_dsc: Path,
    asset_source: Path,
    scene_prefix: str,
    output_gltf: Path,
) -> dict:
    gltf = json.loads(input_gltf.read_text(encoding="utf-8"))
    chapters, relations = dscmat.parse_dsc(scene_dsc)
    store = dscmat.open_store(asset_source)

    models = chapters.get("MODELS", [])
    materials = chapters.get("MATERIALS", [])
    texture_objects = chapters.get("TEXTURES2D", [])
    model_materials: dict[int, list[int]] = {}
    material_textures: dict[int, list[int]] = {}
    model_textures: dict[int, list[int]] = {}

    for relation in relations:
        key = (
            relation["source_chapter"],
            relation["target_chapter"],
            relation["relation_code"],
        )
        if key == ("MODELS", "MATERIALS", 300):
            model_materials.setdefault(relation["source_index"], []).append(
                relation["target_index"]
            )
        elif key == ("MATERIALS", "TEXTURES2D", 401):
            material_textures.setdefault(relation["source_index"], []).append(
                relation["target_index"]
            )
        elif key == ("MODELS", "TEXTURES2D", 400):
            model_textures.setdefault(relation["source_index"], []).append(
                relation["target_index"]
            )

    texture_dir = output_gltf.parent / "textures"
    records = []
    missing_pictures = []
    resolved_nodes = 0

    for model_index, texture_indices in sorted(model_textures.items()):
        if not 0 <= model_index < len(models):
            continue
        model_name = models[model_index]
        material_indices = model_materials.get(model_index, [])
        node_index = resolve_gltf_node(model_name, gltf.get("nodes", []))
        record = {
            "model_index": model_index,
            "model_name": model_name,
            "gltf_node_index": node_index,
            "material_indices": material_indices,
            "material_names": [
                materials[index]
                for index in material_indices
                if 0 <= index < len(materials)
            ],
            "first_material_has_401": bool(
                material_indices and material_textures.get(material_indices[0])
            ),
            "local_texture_projections": [],
        }

        for order, texture_index in enumerate(texture_indices):
            if not 0 <= texture_index < len(texture_objects):
                continue
            texture_name = texture_objects[texture_index]
            texture_member = texture_layers.find_txt(store, texture_name, scene_prefix)
            projection = {
                "order": order,
                "texture_index": texture_index,
                "texture_object": texture_name,
                "source_txt": texture_member,
                "relation_code": 400,
                "projection_required": True,
            }
            if texture_member:
                try:
                    projection.update(parse_projection(store.read(texture_member)))
                    picture = texture_layers.resolve_picture(
                        store,
                        projection["raw_source_path"],
                        scene_prefix,
                    )
                    projection["resolved_picture"] = picture
                    if picture:
                        projection.update(
                            export_picture(store, picture, texture_name, texture_dir)
                        )
                    else:
                        missing_pictures.append(
                            {
                                "model": model_name,
                                "texture": texture_name,
                                "raw_source_path": projection.get("raw_source_path"),
                            }
                        )
                except Exception as exc:
                    projection["status"] = f"{type(exc).__name__}: {exc}"
            else:
                projection["status"] = "missing_texture_object_file"
            record["local_texture_projections"].append(projection)

        if node_index is not None:
            resolved_nodes += 1
            extras = gltf["nodes"][node_index].setdefault("extras", {})
            extras["bz2_dsc_model_name"] = model_name
            extras["bz2_model_texture_projections"] = record[
                "local_texture_projections"
            ]
        records.append(record)

    output_gltf.parent.mkdir(parents=True, exist_ok=True)
    output_gltf.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    result = {
        "schema": "bz2-model-local-texture-projection-v1",
        "input_gltf": str(input_gltf),
        "scene_dsc": str(scene_dsc),
        "asset_source": str(asset_source),
        "scene_prefix": scene_prefix,
        "output_gltf": str(output_gltf),
        "code400_model_count": len(model_textures),
        "code400_edge_count": sum(len(items) for items in model_textures.values()),
        "resolved_gltf_node_count": resolved_nodes,
        "unresolved_gltf_node_count": len(model_textures) - resolved_nodes,
        "unresolved_picture_count": len(missing_pictures),
        "unresolved_pictures": missing_pictures,
        "models": records,
        "notes": [
            "DSC relation code 400 is preserved as model-local TEXTURES2D/projection state and is distinct from material-level code 401.",
            "No model projection is applied to TEXCOORD_0; high-resolution Softimage source meshes frequently store zero UVs and depend on projection state.",
            "TXMP post-path offset 90 is confirmed as compact SI_Texture2D texture-matrix RXYZ/SXYZ/TXYZ state; rotation is stored in radians.",
            "The four big-endian floats at TXMP post-path offset 6 remain a separate 2D texture-transform candidate; exact component semantics remain under validation.",
            "The u16 at TXMP post-path offset 24 varies by texture family; exact mapping/projection enum semantics remain unresolved and are not guessed.",
            "The TXMP crop rectangle is preserved in source pixel coordinates; vertical-origin semantics are not guessed.",
        ],
    }
    output_gltf.with_suffix(".model_textures.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_gltf", type=Path)
    parser.add_argument("scene_dsc", type=Path)
    parser.add_argument("asset_source", type=Path)
    parser.add_argument("scene_prefix")
    parser.add_argument("output_gltf", type=Path)
    args = parser.parse_args()
    result = augment_model_projections(
        args.input_gltf,
        args.scene_dsc,
        args.asset_source,
        args.scene_prefix,
        args.output_gltf,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "code400_model_count",
                    "code400_edge_count",
                    "resolved_gltf_node_count",
                    "unresolved_gltf_node_count",
                    "unresolved_picture_count",
                )
            },
            indent=2,
        )
    )
    return 1 if result["unresolved_picture_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
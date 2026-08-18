#!/usr/bin/env python3
"""Recover Softimage FxDirector settings and bind them to DSC scene lights/models.

Outer HRC class-2 subtype-1 objects frequently contain an FxDirector CUSB user
data block. CUSB stores its own big-endian byte length; property strings begin on
40-byte boundaries, while long values (notably Flare_Preset paths) may span
multiple 40-byte slots.

DSC LIGHTS->MODELS relation code 20000 associates a scene light/light-interest
object with the FxDirector model. When the relation originates from a spotlight
interest object, relation code 2110 is reversed to recover the actual spotlight.

This stage preserves source renderer intent as glTF extras/JSON metadata. It does
not invent Blender lens-flare, volumetric, glow, star, or projector behavior.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import bz2_dsc_material_gltf as dscmat

FX_LIGHT_MODEL_RELATION = 20000
LIGHT_INTEREST_RELATION = 2110


def _atom(value: str):
    if value == "":
        return ""
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    try:
        return float(value)
    except ValueError:
        return value


def _outer_class(data: bytes) -> tuple[int | None, int | None]:
    marker = data.find(b"HRCH")
    if marker < 0:
        return None, None
    end = data.find(b"\0", marker + 4)
    if end < 0 or end + 5 > len(data):
        return None, None
    return (
        int.from_bytes(data[end + 1 : end + 3], "big"),
        int.from_bytes(data[end + 3 : end + 5], "big"),
    )


def parse_fx_director(data: bytes) -> dict | None:
    marker = data.find(b"CUSB")
    if marker < 0 or marker + 8 > len(data):
        return None
    size = int.from_bytes(data[marker + 4 : marker + 8], "big")
    start = marker + 8
    end = start + size
    if size <= 0 or size % 40 or end > len(data):
        return None

    block = data[start:end]
    cursor = 0
    settings = {}
    raw_settings = []
    property_offsets = {}
    while cursor < len(block):
        terminator = block.find(b"\0", cursor)
        if terminator < 0:
            terminator = len(block)
        raw = block[cursor:terminator].decode("latin-1", errors="replace").strip()
        if raw:
            raw_settings.append(raw)
            key, separator, value = raw.partition(" ")
            settings[key] = _atom(value.strip()) if separator else ""
            property_offsets[key] = cursor
        used = max(1, terminator - cursor + 1)
        cursor += ((used + 39) // 40) * 40

    active = {
        key: bool(settings.get(key))
        for key in (
            "Volume_On",
            "Volume_Shard_On",
            "Flare_On",
            "Glow_On",
            "Star_On",
            "Projector_On",
        )
    }
    return {
        "cusb_size": size,
        "settings": settings,
        "property_offsets": property_offsets,
        "raw_settings": raw_settings,
        "active_features": active,
    }


def _find_model_node(gltf: dict, model_index: int, model_name: str) -> int | None:
    for index, node in enumerate(gltf.get("nodes", [])):
        extras = node.get("extras") or {}
        if extras.get("bz2_dsc_model_index") == model_index:
            return index
        if extras.get("bz2_dsc_model_name") == model_name:
            return index
    return None


def attach_fx_directors(
    input_gltf: Path,
    scene_dsc: Path,
    asset_source: Path,
    scene_prefix: str,
    output_gltf: Path,
) -> dict:
    if output_gltf.parent.resolve() != input_gltf.parent.resolve():
        raise ValueError("output_gltf must remain beside input_gltf so existing buffer/image URIs stay valid")
    gltf = json.loads(input_gltf.read_text(encoding="utf-8"))
    chapters, relations = dscmat.parse_dsc(scene_dsc)
    store = dscmat.open_store(asset_source)
    models = chapters.get("MODELS", [])
    lights = chapters.get("LIGHTS", [])

    # 2110 is actual spotlight -> interest object. Code 20000 sometimes starts
    # at that interest element, so reverse the pair to recover the real light.
    interest_owner = {
        int(relation["target_index"]): int(relation["source_index"])
        for relation in relations
        if relation["source_chapter"] == "LIGHTS"
        and relation["target_chapter"] == "LIGHTS"
        and relation["relation_code"] == LIGHT_INTEREST_RELATION
    }

    relation_pairs = []
    seen = set()
    for relation in relations:
        if not (
            relation["source_chapter"] == "LIGHTS"
            and relation["target_chapter"] == "MODELS"
            and relation["relation_code"] == FX_LIGHT_MODEL_RELATION
        ):
            continue
        pair = (int(relation["source_index"]), int(relation["target_index"]))
        if pair not in seen:
            seen.add(pair)
            relation_pairs.append(pair)

    records = []
    unresolved = []
    for source_light_index, model_index in relation_pairs:
        if not (0 <= source_light_index < len(lights) and 0 <= model_index < len(models)):
            continue
        model_name = models[model_index]
        member = store.find_basename(model_name + ".hrc", f"{scene_prefix}/MODELS")
        if not member:
            unresolved.append({"model_name": model_name, "reason": "source_hrc_missing"})
            continue
        data = store.read(member)
        class_id, subtype = _outer_class(data)
        decoded = parse_fx_director(data)
        if class_id != 2 or decoded is None:
            unresolved.append(
                {
                    "model_name": model_name,
                    "source_hrc": member,
                    "reason": f"not_bounded_fxdirector_class_{class_id}_subtype_{subtype}",
                }
            )
            continue

        effective_light_index = interest_owner.get(source_light_index, source_light_index)
        source_light_name = lights[source_light_index]
        effective_light_name = (
            lights[effective_light_index]
            if 0 <= effective_light_index < len(lights)
            else None
        )
        model_node = _find_model_node(gltf, model_index, model_name)
        effective_light_nodes = [
            index
            for index, node in enumerate(gltf.get("nodes", []))
            if node.get("name") == effective_light_name
        ]
        record = {
            "model_index": model_index,
            "model_name": model_name,
            "source_hrc": member,
            "outer_class_id": class_id,
            "outer_subtype": subtype,
            "gltf_model_node": model_node,
            "relation_code": FX_LIGHT_MODEL_RELATION,
            "source_light_index": source_light_index,
            "source_light_name": source_light_name,
            "effective_light_index": effective_light_index,
            "effective_light_name": effective_light_name,
            "effective_light_gltf_nodes": effective_light_nodes,
            **decoded,
        }
        if model_node is not None:
            gltf["nodes"][model_node].setdefault("extras", {})[
                "bz2_fx_director"
            ] = record
        for light_node in effective_light_nodes:
            gltf["nodes"][light_node].setdefault("extras", {}).setdefault(
                "bz2_fx_directors", []
            ).append(
                {
                    "model_index": model_index,
                    "model_name": model_name,
                    "active_features": decoded["active_features"],
                    "settings": decoded["settings"],
                }
            )
        records.append(record)

    output_gltf.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    summary = {
        "schema": "bz2-fxdirector-scene-v1",
        "input_gltf": str(input_gltf),
        "scene_dsc": str(scene_dsc),
        "asset_source": str(asset_source),
        "scene_prefix": scene_prefix,
        "output_gltf": str(output_gltf),
        "code20000_unique_pair_count": len(relation_pairs),
        "fx_director_count": len(records),
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "records": records,
        "notes": [
            "CUSB size is big-endian and the settings payload is 40-byte slot aligned; long strings may span several slots.",
            "DSC LIGHTS->MODELS relation code 20000 associates a light/light-interest element with the FxDirector model.",
            "When code 20000 starts at a 2110 interest object, the effective source light is the 2110 owner spotlight.",
            "Renderer behavior is preserved as metadata; no Blender flare/volume approximation is generated by this stage.",
        ],
    }
    output_gltf.with_suffix(".fx.json").write_text(
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
    result = attach_fx_directors(
        args.input_gltf,
        args.scene_dsc,
        args.asset_source,
        args.scene_prefix,
        args.output_gltf,
    )
    print(json.dumps(result, indent=2))
    return 1 if result["unresolved_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

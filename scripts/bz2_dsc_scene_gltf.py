#!/usr/bin/env python3
"""Attach recovered DSC camera/light state and SETUP_SOFT render metadata to a BZ2 glTF.

This is a scene-fidelity layer for glTFs produced by ``bz2_dsc_material_gltf.py``.
It intentionally uses only relation codes that have been validated against the
BZ2 Softimage corpus:

* MODELS -> MODELS 110: model parent
* CAMERAS -> CAMERAS 1110: camera -> interest object
* LIGHTS -> LIGHTS 2110: spotlight -> interest object
* LIGHTS -> MODELS 2200: light parent model

Binary camera tagged field 4 and light tagged field 8 are recovered as interest
(target) positions, not Euler rotations/direction vectors. This interpretation
is independently validated by original Softimage PIC framing for the archived
ISDF tank and walker scenes.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import zipfile
from pathlib import Path

MODEL_PARENT = 110
PAIR_CAMERA = 1110
PAIR_LIGHT = 2110
LIGHT_MODEL_PARENT = 2200
VERSION_RE = re.compile(r"\.\d+-\d+$")


class SourceStore:
    def read(self, logical: str) -> bytes:
        raise NotImplementedError

    def find_basename(self, basename: str, prefix: str | None = None) -> str | None:
        raise NotImplementedError


class DirectoryStore(SourceStore):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files = [path for path in self.root.rglob("*") if path.is_file()]

    def read(self, logical: str) -> bytes:
        path = self.root / Path(*logical.replace("\\", "/").split("/"))
        return path.read_bytes()

    def find_basename(self, basename: str, prefix: str | None = None) -> str | None:
        wanted = basename.lower()
        prefix_norm = prefix.replace("\\", "/").strip("/").lower() if prefix else None
        candidates: list[str] = []
        for path in self.files:
            if path.name.lower() != wanted:
                continue
            logical = path.relative_to(self.root).as_posix()
            if prefix_norm and not logical.lower().startswith(prefix_norm + "/"):
                continue
            candidates.append(logical)
        return sorted(candidates, key=lambda value: (len(value), value.lower()))[0] if candidates else None


class ZipStore(SourceStore):
    def __init__(self, path: Path):
        self.path = path
        self.archive = zipfile.ZipFile(path, "r")
        self.names = [info.filename.replace("\\", "/") for info in self.archive.infolist() if not info.is_dir()]
        self.by_lower = {name.lower(): name for name in self.names}

    def read(self, logical: str) -> bytes:
        actual = self.by_lower.get(logical.replace("\\", "/").lower())
        if actual is None:
            raise FileNotFoundError(logical)
        return self.archive.read(actual)

    def find_basename(self, basename: str, prefix: str | None = None) -> str | None:
        wanted = basename.lower()
        prefix_norm = prefix.replace("\\", "/").strip("/").lower() if prefix else None
        candidates = []
        for name in self.names:
            if os.path.basename(name).lower() != wanted:
                continue
            if prefix_norm and not name.lower().startswith(prefix_norm + "/"):
                continue
            candidates.append(name)
        return sorted(candidates, key=lambda value: (len(value), value.lower()))[0] if candidates else None


def open_store(path: Path) -> SourceStore:
    if path.is_dir():
        return DirectoryStore(path)
    if zipfile.is_zipfile(path):
        return ZipStore(path)
    raise ValueError(f"asset source is neither a directory nor ZIP archive: {path}")


def parse_dsc(path: Path) -> tuple[dict[str, list[str]], list[dict]]:
    text = path.read_text(encoding="latin-1", errors="replace")
    if "ELEMENTS" not in text or "RELATIONS" not in text:
        raise ValueError(f"unsupported DSC: {path}")
    element_text = text.split("ELEMENTS", 1)[1].split("EndOfELEMENTS", 1)[0]
    chapters: dict[str, list[str]] = {}
    for match in re.finditer(r"CHAPTER\s+(\S+)\s+NBELEM\s+(\d+)\s+(.*?)EndOfCHAPTER", element_text, re.DOTALL):
        names: list[str] = []
        for line in match.group(3).splitlines():
            entry = re.match(r"\s*(.+?)\s*(?:ROOT\s*)?;\s*$", line)
            if entry:
                names.append(entry.group(1).strip())
        chapters[match.group(1)] = names

    relation_text = text.split("RELATIONS", 1)[1].split("EndOfRELATIONS", 1)[0]
    relations: list[dict] = []
    for match in re.finditer(r"CHAPTER\s+(\S+)\s+CHAPTER\s+(\S+)\s+(.*?)EndOfCHAPTER", relation_text, re.DOTALL):
        source_chapter, target_chapter = match.group(1), match.group(2)
        for line in match.group(3).splitlines():
            entry = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\s*;", line)
            if not entry:
                continue
            source_index, target_index, relation_code = map(int, entry.groups())
            relations.append({
                "source_chapter": source_chapter,
                "target_chapter": target_chapter,
                "source_index": source_index,
                "target_index": target_index,
                "relation_code": relation_code,
            })
    return chapters, relations


def _payload(data: bytes, tag: int, size: int, start: int = 88) -> bytes | None:
    marker = struct.pack(">HH", tag, 1)
    offset = data.find(marker, start)
    if offset < 0 or offset + 4 + size > len(data):
        return None
    return data[offset + 4 : offset + 4 + size]


def _vec3(data: bytes, tag: int) -> list[float] | None:
    raw = _payload(data, tag, 12)
    return list(struct.unpack(">3f", raw)) if raw else None


def _f32(data: bytes, tag: int) -> float | None:
    raw = _payload(data, tag, 4)
    return float(struct.unpack(">f", raw)[0]) if raw else None


def decode_camera(data: bytes) -> dict:
    return {
        "position_xyz": _vec3(data, 3),
        "interest_xyz": _vec3(data, 4),
        "clip_near": _f32(data, 7),
        "clip_far": _f32(data, 8),
        "focal_length": _f32(data, 9),
        "f_stop": _f32(data, 10),
        "focus_distance": _f32(data, 11),
        "fov_radians": _f32(data, 12),
    }


def decode_light(data: bytes) -> dict:
    return {
        "color_rgb": _vec3(data, 3),
        "intensity": _f32(data, 4),
        "range": _f32(data, 5),
        "cone_scale": _f32(data, 6),
        "position_xyz": _vec3(data, 7),
        "interest_xyz": _vec3(data, 8),
        "cone_angle": _f32(data, 9),
        "cone_spread": _f32(data, 10),
    }


def _normalize(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length > 1.0e-12 else [0.0, 0.0, -1.0]


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(left * right for left, right in zip(a, b))


def look_matrix(position: list[float], interest: list[float]) -> list[float]:
    """Return a glTF column-major local matrix with -Z aimed at interest."""
    forward = _normalize([interest[index] - position[index] for index in range(3)])
    up = [0.0, 1.0, 0.0]
    if abs(_dot(forward, up)) > 0.999:
        up = [0.0, 0.0, 1.0]
    right = _normalize(_cross(forward, up))
    corrected_up = _normalize(_cross(right, forward))
    back = [-value for value in forward]
    return [
        right[0], right[1], right[2], 0.0,
        corrected_up[0], corrected_up[1], corrected_up[2], 0.0,
        back[0], back[1], back[2], 0.0,
        position[0], position[1], position[2], 1.0,
    ]


def parse_setup_soft(text: str) -> dict:
    output_match = re.search(r"OUTPUT_FILE\s+'([^']+)'", text)
    resolution_match = re.search(r"RESOLUTION\s+(\d+)\s+(\d+)", text)
    frame_match = re.search(r"RENDERING_FRAME\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)", text)
    perspectives = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "CAMERA_TYPE PERSPECTIVE" not in line:
            continue
        record: dict[str, float] = {}
        for nearby in lines[index + 1 : index + 16]:
            for key, pattern in (
                ("fov_radians", r"CAMERA_FOV\s+([^\s]+)"),
                ("aspect", r"CAMERA_ASPECT\s+([^\s]+)"),
                ("near", r"CAMERA_NEAR\s+([^\s]+)"),
                ("far", r"CAMERA_FAR\s+([^\s]+)"),
            ):
                match = re.search(pattern, nearby)
                if match:
                    record[key] = float(match.group(1))
        perspectives.append(record)
    return {
        "output_file": output_match.group(1) if output_match else None,
        "resolution": [int(resolution_match.group(1)), int(resolution_match.group(2))] if resolution_match else None,
        "rendering_frame": [int(value) for value in frame_match.groups()] if frame_match else None,
        "perspective_views": perspectives,
    }


def _strip_version(name: str) -> str:
    return VERSION_RE.sub("", name)


def resolve_node(dsc_name: str, node_names: list[str]) -> str | None:
    stem = _strip_version(dsc_name)
    if stem in node_names:
        return stem
    candidates = [name for name in node_names if stem.endswith("-" + name)]
    return max(candidates, key=len) if candidates else None


def _find_parameter_member(store: SourceStore, names: list[str], prefix: str, extension: str) -> tuple[str | None, str | None]:
    """Return the first scene-local parameter object and the name that owns it."""
    for name in names:
        member = store.find_basename(name + extension, prefix)
        if member:
            return member, name
    # Global fallback is deliberately last; the report records it so callers can audit ambiguity.
    for name in names:
        member = store.find_basename(name + extension)
        if member:
            return member, name
    return None, None


def augment_scene(scene_dsc: Path, asset_source: Path, scene_prefix: str, input_gltf: Path, output_gltf: Path) -> dict:
    chapters, relations = parse_dsc(scene_dsc)
    store = open_store(asset_source)
    gltf = json.loads(input_gltf.read_text(encoding="utf-8"))
    if output_gltf.parent.resolve() != input_gltf.parent.resolve():
        raise ValueError("output_gltf must be in the same directory as input_gltf so existing buffer/texture URIs remain valid")

    gltf.setdefault("nodes", [])
    gltf.setdefault("scenes", [{"nodes": []}])
    scene_index = int(gltf.get("scene", 0))
    gltf["scenes"][scene_index].setdefault("nodes", [])
    original_node_names = [str(node.get("name") or "") for node in gltf["nodes"]]
    original_node_index = {name: index for index, name in enumerate(original_node_names)}

    report = {
        "schema": "bz2-dsc-scene-gltf-v1",
        "scene_dsc": str(scene_dsc),
        "asset_source": str(asset_source),
        "scene_prefix": scene_prefix,
        "input_gltf": str(input_gltf),
        "output_gltf": str(output_gltf),
        "cameras": [],
        "lights": [],
        "setup_soft": None,
        "relation_semantics": {
            "model_parent": MODEL_PARENT,
            "camera_interest": PAIR_CAMERA,
            "light_interest": PAIR_LIGHT,
            "light_model_parent": LIGHT_MODEL_PARENT,
        },
        "notes": [
            "camera tagged field 4 is recovered as an interest/target position, not Euler rotation",
            "light tagged field 8 is recovered as an interest/target position, not a normalized direction vector",
            "only MODELS->MODELS relation code 110 is authoritative model parenting",
            "spot cone_angle/cone_spread mapping to glTF is provisional; raw values are retained in extras",
            "SETUP_SOFT metadata is preserved for Blender render framing, but Softimage renderer state is not claimed pixel-equivalent",
        ],
    }

    setup_names = chapters.get("SETUP_SOFT", [])
    if setup_names:
        member, owner = _find_parameter_member(store, [setup_names[0]], scene_prefix, ".sts")
        if member:
            report["setup_soft"] = {"member": member, "parameter_object": owner, **parse_setup_soft(store.read(member).decode("latin-1", errors="replace"))}

    cameras = chapters.get("CAMERAS", [])
    camera_defs = list(gltf.get("cameras", []))
    for relation in relations:
        if not (relation["source_chapter"] == "CAMERAS" and relation["target_chapter"] == "CAMERAS" and relation["relation_code"] == PAIR_CAMERA):
            continue
        source_index, target_index = relation["source_index"], relation["target_index"]
        if source_index >= len(cameras) or target_index >= len(cameras):
            continue
        actual_name = cameras[source_index]
        interest_name = cameras[target_index]
        member, backed_name = _find_parameter_member(store, [interest_name, actual_name], scene_prefix, ".cam")
        if not member:
            continue
        decoded = decode_camera(store.read(member))
        if not decoded["position_xyz"] or not decoded["interest_xyz"]:
            continue
        perspective = {
            "yfov": decoded["fov_radians"] or 0.7853981633974483,
            "znear": max(1.0e-5, decoded["clip_near"] or 0.1),
        }
        if decoded["clip_far"] and decoded["clip_far"] > 0:
            perspective["zfar"] = decoded["clip_far"]
        camera_index = len(camera_defs)
        camera_defs.append({
            "name": actual_name,
            "type": "perspective",
            "perspective": perspective,
            "extras": {
                "source_member": member,
                "parameter_object": backed_name,
                "interest_object": interest_name,
                "focal_length": decoded["focal_length"],
                "f_stop": decoded["f_stop"],
                "focus_distance": decoded["focus_distance"],
            },
        })
        node_index = len(gltf["nodes"])
        gltf["nodes"].append({
            "name": actual_name,
            "camera": camera_index,
            "matrix": look_matrix(decoded["position_xyz"], decoded["interest_xyz"]),
            "extras": {"bz2_interest": decoded["interest_xyz"], "source_member": member},
        })
        gltf["scenes"][scene_index]["nodes"].append(node_index)
        report["cameras"].append({
            "name": actual_name,
            "interest_name": interest_name,
            "parameter_object": backed_name,
            "member": member,
            **decoded,
            "gltf_node": node_index,
        })
    if camera_defs:
        gltf["cameras"] = camera_defs

    lights = chapters.get("LIGHTS", [])
    paired_light_indices: set[int] = set()
    light_pairs: dict[int, int] = {}
    for relation in relations:
        if relation["source_chapter"] == "LIGHTS" and relation["target_chapter"] == "LIGHTS" and relation["relation_code"] == PAIR_LIGHT:
            light_pairs[relation["source_index"]] = relation["target_index"]
            paired_light_indices.update((relation["source_index"], relation["target_index"]))

    parent_model = {
        relation["source_index"]: relation["target_index"]
        for relation in relations
        if relation["source_chapter"] == "LIGHTS" and relation["target_chapter"] == "MODELS" and relation["relation_code"] == LIGHT_MODEL_PARENT
    }
    models = chapters.get("MODELS", [])
    light_records: list[tuple[int, str, str | None, str | None, str, dict]] = []
    for source_index, target_index in light_pairs.items():
        if source_index >= len(lights) or target_index >= len(lights):
            continue
        actual_name, interest_name = lights[source_index], lights[target_index]
        member, backed_name = _find_parameter_member(store, [interest_name, actual_name], scene_prefix, ".lig")
        if member:
            light_records.append((source_index, actual_name, interest_name, backed_name, member, decode_light(store.read(member))))
    for index, name in enumerate(lights):
        if index in paired_light_indices:
            continue
        member, backed_name = _find_parameter_member(store, [name], scene_prefix, ".lig")
        if member:
            light_records.append((index, name, None, backed_name, member, decode_light(store.read(member))))

    extension_lights = list(gltf.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", []))
    for source_index, name, interest_name, backed_name, member, decoded in light_records:
        position = decoded["position_xyz"]
        if not position:
            continue
        is_spot = interest_name is not None or decoded["cone_angle"] is not None
        source_color = decoded["color_rgb"] or [1.0, 1.0, 1.0]
        color_scale = max(1.0, max(source_color))
        color = [max(0.0, min(1.0, component / color_scale)) for component in source_color]
        light = {
            "name": name,
            "type": "spot" if is_spot else "point",
            "color": color,
            "intensity": max(0.0, (decoded["intensity"] or 1.0) * color_scale),
            "extras": {
                "source_member": member,
                "source_color_rgb": source_color,
                "source_intensity": decoded["intensity"],
                "source_range": decoded["range"],
                "source_cone_scale": decoded["cone_scale"],
                "source_interest": decoded["interest_xyz"],
                "source_cone_angle": decoded["cone_angle"],
                "source_cone_spread": decoded["cone_spread"],
            },
        }
        if decoded["range"] and 0 < decoded["range"] < 10000:
            light["range"] = decoded["range"]
        if is_spot:
            outer = max(1.0e-5, decoded["cone_angle"] or (math.pi / 4.0))
            spread = max(0.0, decoded["cone_spread"] or 0.0)
            inner = max(0.0, min(outer, outer - spread))
            light["spot"] = {"innerConeAngle": inner, "outerConeAngle": outer}
        light_index = len(extension_lights)
        extension_lights.append(light)

        interest = decoded["interest_xyz"]
        if not interest or all(abs(value) <= 1.0e-6 for value in interest):
            interest = [position[0], position[1], position[2] - 1.0]
        node_index = len(gltf["nodes"])
        gltf["nodes"].append({
            "name": name,
            "extensions": {"KHR_lights_punctual": {"light": light_index}},
            "matrix": look_matrix(position, interest),
            "extras": {"bz2_interest": decoded["interest_xyz"], "source_member": member, "interest_object": interest_name},
        })

        parent_name = None
        parent_node_index = None
        model_index = parent_model.get(source_index)
        if model_index is not None and 0 <= model_index < len(models):
            parent_name = models[model_index]
            resolved = resolve_node(parent_name, original_node_names)
            parent_node_index = original_node_index.get(resolved) if resolved else None
        if parent_node_index is not None:
            gltf["nodes"][parent_node_index].setdefault("children", []).append(node_index)
        else:
            gltf["scenes"][scene_index]["nodes"].append(node_index)

        report["lights"].append({
            "name": name,
            "interest_name": interest_name,
            "parameter_object": backed_name,
            "member": member,
            "parent_model": parent_name,
            **decoded,
            "gltf_node": node_index,
        })

    if extension_lights:
        gltf.setdefault("extensionsUsed", [])
        if "KHR_lights_punctual" not in gltf["extensionsUsed"]:
            gltf["extensionsUsed"].append("KHR_lights_punctual")
        gltf.setdefault("extensions", {})["KHR_lights_punctual"] = {"lights": extension_lights}

    output_gltf.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    sidecar = output_gltf.with_suffix(".scene.json")
    sidecar.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene_dsc", type=Path)
    parser.add_argument("asset_source", type=Path, help="extracted asset root or source ZIP archive")
    parser.add_argument("scene_prefix", help="logical scene family inside asset_source; required to disambiguate duplicate camera/light basenames")
    parser.add_argument("input_gltf", type=Path, help="materialized HRC glTF")
    parser.add_argument("output_gltf", type=Path)
    args = parser.parse_args()
    report = augment_scene(args.scene_dsc, args.asset_source, args.scene_prefix, args.input_gltf, args.output_gltf)
    print(json.dumps({
        "camera_count": len(report["cameras"]),
        "light_count": len(report["lights"]),
        "setup_soft": report["setup_soft"],
        "output": str(args.output_gltf),
    }, indent=2))
    return 0 if report["cameras"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

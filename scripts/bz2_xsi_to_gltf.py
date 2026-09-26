#!/usr/bin/env python3
"""Write a glTF twin of an engine dotXSI export.

The reconstructed ``scene.gltf`` keeps source fidelity: several texture layers
per material, stored UVs next to generated ones, and blend semantics in
sidecars. The engine XSI flattens each material to one texture, which is
baked from the layer stack where needed. This module writes that same
flattened model as glTF 2.0, so glTF/Blender/FBX workflows get exactly what
the engine receives.

What the glTF twin keeps, identical to the XSI:
- the frame hierarchy, with rigid matrices;
- the meshes, UVs and normals;
- one base-colour texture per material, written as PNG copies of the TGAs.

Differences from the XSI:
- V is flipped to glTF's top-left convention;
- the axes stay Softimage-native (Y-up, the same as ``scene.gltf``);
- shading type 0 (constant) is marked ``KHR_materials_unlit``.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "tools" / "io_scene_bz2xsi"))

import bz2xsi  # noqa: E402

bz2xsi.ALLOW_PRINT = False

FLOAT, UINT32 = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


class _Buffer:
    def __init__(self) -> None:
        self.data = bytearray()
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def add(self, array: np.ndarray, component: int, kind: str, target: int, minmax: bool = False) -> int:
        while len(self.data) % 4:
            self.data.append(0)
        raw = array.astype(np.float32 if component == FLOAT else np.uint32).tobytes()
        self.views.append({"buffer": 0, "byteOffset": len(self.data), "byteLength": len(raw), "target": target})
        self.data.extend(raw)
        accessor = {"bufferView": len(self.views) - 1, "componentType": component, "count": int(len(array)), "type": kind}
        if minmax:
            accessor["min"] = [float(v) for v in array.min(axis=0)]
            accessor["max"] = [float(v) for v in array.max(axis=0)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def _png_for(texture: str, texture_dir: Path, out_dir: Path, cache: dict[str, str | None]) -> str | None:
    if texture not in cache:
        source = texture_dir / texture
        if not source.is_file():
            cache[texture] = None
        else:
            from PIL import Image

            name = Path(texture).with_suffix(".png").name
            with Image.open(source) as image:
                image.save(out_dir / name)
            cache[texture] = name
    return cache[texture]


def _winding_matches_normals(mesh: bz2xsi.Mesh) -> bool:
    """True when face winding agrees with the authored normals (CCW = front)."""
    if not mesh.normal_vertices or not mesh.normal_faces:
        return True
    vertices = np.asarray(mesh.vertices, float)
    normals = np.asarray(mesh.normal_vertices, float)
    agree = 0
    total = 0
    for face, normal_face in zip(mesh.faces, mesh.normal_faces):
        if len(face) < 3:
            continue
        a, b, c = vertices[face[0]], vertices[face[1]], vertices[face[2]]
        geometric = np.cross(b - a, c - a)
        authored = normals[list(normal_face)].sum(axis=0)
        agree += float(geometric @ authored) > 0
        total += 1
    return total == 0 or agree * 2 >= total


def convert(xsi: bz2xsi.XSI, texture_dir: Path, out_path: Path) -> dict:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    buffer = _Buffer()
    gltf: dict = {
        "asset": {"version": "2.0", "generator": "bz2_xsi_to_gltf", "extras": {"bz2_uv_convention": "gltf_top_left_v1", "source_xsi": out_path.stem}},
        "scene": 0,
        "scenes": [{"nodes": []}],
        "nodes": [],
        "meshes": [],
        "materials": [],
        "textures": [],
        "images": [],
        "samplers": [{"wrapS": 10497, "wrapT": 10497}],
    }
    material_keys: dict[tuple, int] = {}
    png_cache: dict[str, str | None] = {}
    image_index: dict[str, int] = {}
    flipped_meshes: list[str] = []
    uses_unlit = False

    def material_index(material: bz2xsi.Material | None) -> int:
        nonlocal uses_unlit
        if material is None:
            material = bz2xsi.Material()
        key = (material.texture, tuple(material.diffuse), material.shading_type, tuple(material.emissive))
        if key in material_keys:
            return material_keys[key]
        pbr: dict = {"baseColorFactor": [float(c) for c in material.diffuse], "metallicFactor": 0.0, "roughnessFactor": 0.8}
        entry: dict = {"name": Path(material.texture).stem if material.texture else "untextured", "pbrMetallicRoughness": pbr}
        if material.texture:
            png = _png_for(material.texture, texture_dir, out_dir, png_cache)
            if png:
                if png not in image_index:
                    gltf["images"].append({"uri": png})
                    gltf["textures"].append({"sampler": 0, "source": len(gltf["images"]) - 1})
                    image_index[png] = len(gltf["textures"]) - 1
                pbr["baseColorTexture"] = {"index": image_index[png]}
                pbr["baseColorFactor"] = [1.0, 1.0, 1.0, float(material.diffuse[3])]
        if any(material.emissive):
            entry["emissiveFactor"] = [float(c) for c in material.emissive]
        if material.shading_type == 0:
            entry["extensions"] = {"KHR_materials_unlit": {}}
            uses_unlit = True
        entry["extras"] = {"xsi_shading_type": material.shading_type, "xsi_specular": list(material.specular), "xsi_hardness": material.hardness}
        gltf["materials"].append(entry)
        material_keys[key] = len(gltf["materials"]) - 1
        return material_keys[key]

    def build_mesh(mesh: bz2xsi.Mesh) -> int | None:
        vertices = np.asarray(mesh.vertices, float)
        uv_vertices = np.asarray(mesh.uv_vertices, float) if mesh.uv_vertices else None
        normal_vertices = np.asarray(mesh.normal_vertices, float) if mesh.normal_vertices else None
        reverse = not _winding_matches_normals(mesh)
        if reverse:
            flipped_meshes.append(mesh.name or "")
        groups: dict[int, dict[str, list]] = {}
        for index, face in enumerate(mesh.faces):
            material = mesh.face_materials[index] if index < len(mesh.face_materials) else None
            group = groups.setdefault(material_index(material), {"p": [], "n": [], "t": []})
            uv_face = mesh.uv_faces[index] if uv_vertices is not None and index < len(mesh.uv_faces) else None
            normal_face = mesh.normal_faces[index] if normal_vertices is not None and index < len(mesh.normal_faces) else None
            for corner in range(1, len(face) - 1):
                order = (0, corner + 1, corner) if reverse else (0, corner, corner + 1)
                for i in order:
                    group["p"].append(vertices[face[i]])
                    group["n"].append(normal_vertices[normal_face[i]] if normal_face is not None else (0.0, 1.0, 0.0))
                    uv = uv_vertices[uv_face[i]] if uv_face is not None else (0.0, 0.0)
                    group["t"].append((uv[0], 1.0 - uv[1]))
        primitives = []
        for material, group in groups.items():
            if not group["p"]:
                continue
            positions = np.asarray(group["p"], float)
            normals = np.asarray(group["n"], float)
            lengths = np.linalg.norm(normals, axis=1, keepdims=True)
            normals = np.where(lengths > 1e-12, normals / np.maximum(lengths, 1e-12), [0.0, 1.0, 0.0])
            primitives.append(
                {
                    "attributes": {
                        "POSITION": buffer.add(positions, FLOAT, "VEC3", ARRAY_BUFFER, minmax=True),
                        "NORMAL": buffer.add(normals, FLOAT, "VEC3", ARRAY_BUFFER),
                        "TEXCOORD_0": buffer.add(np.asarray(group["t"], float), FLOAT, "VEC2", ARRAY_BUFFER),
                    },
                    "indices": buffer.add(np.arange(len(positions)), UINT32, "SCALAR", ELEMENT_ARRAY_BUFFER),
                    "material": material,
                }
            )
        if not primitives:
            return None
        gltf["meshes"].append({"name": mesh.name or "mesh", "primitives": primitives})
        return len(gltf["meshes"]) - 1

    def build_node(frame: bz2xsi.Frame) -> int:
        node: dict = {"name": frame.name}
        if frame.transform is not None:
            # Row-vector XSI matrix M (v' = v M): glTF's column-major array of
            # the column-vector matrix M^T is M's rows in order.
            flat = [float(v) for row in frame.transform.to_list() for v in row]
            if flat != [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]:
                node["matrix"] = flat
        if frame.mesh is not None and frame.mesh.faces:
            mesh_index = build_mesh(frame.mesh)
            if mesh_index is not None:
                node["mesh"] = mesh_index
        gltf["nodes"].append(node)
        index = len(gltf["nodes"]) - 1
        children = [build_node(child) for child in frame.frames]
        if children:
            gltf["nodes"][index]["children"] = children
        return index

    for root in xsi.frames:
        gltf["scenes"][0]["nodes"].append(build_node(root))

    bin_name = out_path.with_suffix(".bin").name
    (out_dir / bin_name).write_bytes(bytes(buffer.data))
    gltf["buffers"] = [{"uri": bin_name, "byteLength": len(buffer.data)}]
    gltf["bufferViews"] = buffer.views
    gltf["accessors"] = buffer.accessors
    if uses_unlit:
        gltf["extensionsUsed"] = ["KHR_materials_unlit"]
    for key in ("textures", "images"):
        if not gltf[key]:
            del gltf[key]
    if "textures" not in gltf:
        del gltf["samplers"]
    out_path.write_text(json.dumps(gltf, indent=1), encoding="utf-8")
    return {
        "gltf": str(out_path),
        "nodes": len(gltf["nodes"]),
        "meshes": len(gltf["meshes"]),
        "materials": len(gltf["materials"]),
        "images": len(gltf.get("images", [])),
        "winding_reversed_meshes": flipped_meshes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("xsi", type=Path)
    parser.add_argument("--out", type=Path, help="output .gltf (default: next to the XSI)")
    args = parser.parse_args()
    out = args.out or args.xsi.with_suffix(".gltf")
    print(json.dumps(convert(bz2xsi.read(str(args.xsi)), args.xsi.parent, out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

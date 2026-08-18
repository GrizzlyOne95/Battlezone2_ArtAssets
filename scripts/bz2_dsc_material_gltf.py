#!/usr/bin/env python3
"""Bind original DSC/MTR/TXT/PIC materials to an assembled HRC glTF.

This layer builds on ``bz2_hrc_gltf.py`` / ``bz2_hrc_gltf_parametric.py`` and
preserves the reconstructed HRC node hierarchy. Class-4 polygon primitives are
rebuilt by the material-slot index stored in the upper 16 bits of each source
polygon metadata word, then bound through the original Softimage scene graph:

* DSC MODELS -> MATERIALS relation code 300
* DSC MATERIALS -> TEXTURES2D relation code 401
* binary TEXTURES2D ``.txt`` source-picture reference
* original Softimage PIC converted to PNG via ``softimage_pic.py``

When a class-4 child has no direct code-300 relation, the nearest HRC ancestor
with a DSC material list is used. This matches archived walker assets where
child meshes inherit their parent's Softimage material assignment.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import struct
import zipfile
from pathlib import Path

import bz2_hrc_gltf as assembled
import bz2_hrc_gltf_parametric as parametric
import bz2_hrc_tree_probe as hrc_tree
import softimage_pic

VERSION_RE = re.compile(r"\.\d+-\d+$")
SERVER_PICTURE_RE = re.compile(
    rb"//Server/Battlezone/modelsdirectory/([^\x00]+)", re.IGNORECASE
)
PICTURE_EXTENSIONS = (".pic", ".PIC", ".tga", ".TGA", ".png", ".PNG")


class SourceStore:
    def read(self, path: str) -> bytes:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def find_basename(self, basename: str, prefix: str | None = None) -> str | None:
        raise NotImplementedError

    def copy_to(self, path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.read(path))


class DirectoryStore(SourceStore):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._files = [path for path in self.root.rglob("*") if path.is_file()]
        self._by_relative = {
            path.relative_to(self.root).as_posix().lower(): path for path in self._files
        }

    def read(self, path: str) -> bytes:
        resolved = self._by_relative.get(path.replace("\\", "/").lower())
        if resolved is None:
            raise FileNotFoundError(path)
        return resolved.read_bytes()

    def exists(self, path: str) -> bool:
        return path.replace("\\", "/").lower() in self._by_relative

    def find_basename(self, basename: str, prefix: str | None = None) -> str | None:
        wanted = basename.lower()
        prefix_lower = prefix.replace("\\", "/").strip("/").lower() if prefix else None
        candidates = []
        for relative, path in self._by_relative.items():
            if path.name.lower() != wanted:
                continue
            if prefix_lower and not relative.startswith(prefix_lower + "/"):
                continue
            candidates.append(path.relative_to(self.root).as_posix())
        return candidates[0] if candidates else None


class ZipStore(SourceStore):
    def __init__(self, path: Path):
        self.path = path
        self.archive = zipfile.ZipFile(path, "r")
        self._by_relative = {
            info.filename.replace("\\", "/").lower(): info.filename
            for info in self.archive.infolist()
            if not info.is_dir()
        }

    def read(self, path: str) -> bytes:
        actual = self._by_relative.get(path.replace("\\", "/").lower())
        if actual is None:
            raise FileNotFoundError(path)
        return self.archive.read(actual)

    def exists(self, path: str) -> bool:
        return path.replace("\\", "/").lower() in self._by_relative

    def find_basename(self, basename: str, prefix: str | None = None) -> str | None:
        wanted = basename.lower()
        prefix_lower = prefix.replace("\\", "/").strip("/").lower() if prefix else None
        candidates = []
        for relative, actual in self._by_relative.items():
            if Path(actual).name.lower() != wanted:
                continue
            if prefix_lower and not relative.startswith(prefix_lower + "/"):
                continue
            candidates.append(actual)
        return candidates[0] if candidates else None


def open_store(path: Path) -> SourceStore:
    if path.is_dir():
        return DirectoryStore(path)
    if zipfile.is_zipfile(path):
        return ZipStore(path)
    raise ValueError(f"asset source is neither a directory nor ZIP archive: {path}")


def parse_dsc(path: Path) -> tuple[dict[str, list[str]], list[dict]]:
    text = path.read_text(encoding="latin-1", errors="replace")
    if "ELEMENTS" not in text or "RELATIONS" not in text:
        raise ValueError(f"not a supported DSC scene: {path}")

    element_text = text.split("ELEMENTS", 1)[1].split("EndOfELEMENTS", 1)[0]
    chapters: dict[str, list[str]] = {}
    for match in re.finditer(
        r"CHAPTER\s+(\S+)\s+NBELEM\s+(\d+)\s+(.*?)EndOfCHAPTER",
        element_text,
        re.DOTALL,
    ):
        names = []
        for line in match.group(3).splitlines():
            entry = re.match(r"\s*(.+?)\s*(?:ROOT\s*)?;\s*$", line)
            if entry:
                names.append(entry.group(1).strip())
        chapters[match.group(1)] = names

    relation_text = text.split("RELATIONS", 1)[1].split("EndOfRELATIONS", 1)[0]
    relations = []
    for match in re.finditer(
        r"CHAPTER\s+(\S+)\s+CHAPTER\s+(\S+)\s+(.*?)EndOfCHAPTER",
        relation_text,
        re.DOTALL,
    ):
        source_chapter, target_chapter = match.group(1), match.group(2)
        for line in match.group(3).splitlines():
            entry = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\b", line)
            if not entry:
                continue
            source_index, target_index, relation_code = map(int, entry.groups())
            relations.append(
                {
                    "source_chapter": source_chapter,
                    "target_chapter": target_chapter,
                    "source_index": source_index,
                    "target_index": target_index,
                    "relation_code": relation_code,
                }
            )
    return chapters, relations


def _strip_version(name: str) -> str:
    return VERSION_RE.sub("", name)


def resolve_model_index(node_name: str, models: list[str]) -> int | None:
    """Map an internal HRC node name to its DSC MODELS entry.

    Softimage scene elements normally prepend a scene/model namespace (for
    example ``tank2-Antenae.1-0``) while the HRC record is simply ``Antenae``.
    Prefer exact basename matches, then the shortest ``-<node>`` suffix match.
    """
    exact = []
    suffix = []
    for index, model_name in enumerate(models):
        stem = _strip_version(model_name)
        if stem == node_name:
            exact.append((len(stem), index))
        elif stem.endswith("-" + node_name):
            suffix.append((len(stem), index))
    candidates = exact or suffix
    return min(candidates)[1] if candidates else None


def decode_mtr(data: bytes) -> dict:
    marker = data.find(b"MTRL")
    defaults = {
        "diffuse_rgb": [1.0, 1.0, 1.0],
        "alpha": 1.0,
        "specular_rgb": [0.0, 0.0, 0.0],
        "shininess": 0.0,
        "roughness": 1.0,
        "float_window_be": None,
    }
    if marker < 0:
        return defaults
    name_end = data.find(b"\0", marker + 4)
    if name_end < 0:
        return defaults
    start = name_end + 13
    if start + 48 > len(data):
        return defaults
    values = list(struct.unpack_from(">12f", data, start))
    diffuse = [min(1.0, max(0.0, float(value))) for value in values[2:5]]
    specular = [max(0.0, float(value)) for value in values[5:8]]
    shininess = max(0.0, float(values[8]))
    alpha = min(1.0, max(0.0, float(values[11]))) if 0.0 <= values[11] <= 1.0 else 1.0
    roughness = min(1.0, max(0.03, math.sqrt(2.0 / (shininess + 2.0))))
    return {
        "diffuse_rgb": diffuse,
        "alpha": alpha,
        "specular_rgb": specular,
        "shininess": shininess,
        "roughness": roughness,
        "float_window_be": values,
    }


def find_picture_member(store: SourceStore, texture_map_data: bytes) -> str | None:
    match = SERVER_PICTURE_RE.search(texture_map_data)
    if not match:
        return None
    logical = match.group(1).decode("latin-1", errors="replace").replace("\\", "/")
    candidates = [logical]
    if not Path(logical).suffix:
        candidates.extend(logical + extension for extension in PICTURE_EXTENSIONS)
    for candidate in candidates:
        if store.exists(candidate):
            return candidate
    basename = Path(logical).name
    if not Path(basename).suffix:
        for extension in PICTURE_EXTENSIONS:
            found = store.find_basename(basename + extension)
            if found:
                return found
    return store.find_basename(basename)


def export_picture(
    store: SourceStore,
    source_member: str,
    texture_dir: Path,
) -> tuple[str | None, dict]:
    data = store.read(source_member)
    suffix = Path(source_member).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(source_member).stem)
    if suffix == ".pic":
        info = softimage_pic.inspect_pic_bytes(data)
        if info.get("kind") != "softimage_pic":
            return None, {"source": source_member, "status": info.get("kind")}
        rgba, decoded = softimage_pic.decode_pic_bytes(data)
        destination = texture_dir / f"{stem}.png"
        softimage_pic.write_rgba_png(
            destination, int(decoded["width"]), int(decoded["height"]), rgba
        )
        nonopaque_alpha = any(alpha != 255 for alpha in rgba[3::4])
        return f"textures/{destination.name}", {
            "source": source_member,
            "output": destination.name,
            "width": decoded["width"],
            "height": decoded["height"],
            "has_nonopaque_alpha": nonopaque_alpha,
        }

    # Preserve already-portable source images without interpretation. Other
    # historical formats continue to use the wider extraction pipeline/OIIO.
    if suffix in {".png"}:
        destination = texture_dir / Path(source_member).name
        store.copy_to(source_member, destination)
        return f"textures/{destination.name}", {
            "source": source_member,
            "output": destination.name,
            "has_nonopaque_alpha": False,
        }
    return None, {"source": source_member, "status": f"unsupported_texture_format:{suffix}"}


def build_scene_materials(
    scene_path: Path,
    store: SourceStore,
    scene_prefix: str,
    texture_dir: Path,
) -> tuple[dict[int, list[dict]], dict[str, dict], dict, list[dict]]:
    chapters, relations = parse_dsc(scene_path)
    models = chapters.get("MODELS", [])
    materials = chapters.get("MATERIALS", [])
    texture_objects = chapters.get("TEXTURES2D", [])

    material_to_texture: dict[int, int] = {}
    for relation in relations:
        if (
            relation["source_chapter"] == "MATERIALS"
            and relation["target_chapter"] == "TEXTURES2D"
            and relation["relation_code"] == 401
        ):
            material_to_texture[relation["source_index"]] = relation["target_index"]

    ordered_model_material_indices: dict[int, list[int]] = {}
    for relation in relations:
        if (
            relation["source_chapter"] == "MODELS"
            and relation["target_chapter"] == "MATERIALS"
            and relation["relation_code"] == 300
        ):
            ordered_model_material_indices.setdefault(relation["source_index"], []).append(
                relation["target_index"]
            )

    material_defs: dict[str, dict] = {}
    model_material_defs: dict[int, list[dict]] = {}
    texture_exports: dict[str, dict] = {}

    for model_index, material_indices in ordered_model_material_indices.items():
        resolved = []
        for material_index in material_indices:
            if not (0 <= material_index < len(materials)):
                continue
            material_name = materials[material_index]
            if material_name in material_defs:
                resolved.append(material_defs[material_name])
                continue

            material_member = store.find_basename(
                material_name + ".mtr", f"{scene_prefix}/MATERIALS"
            ) or store.find_basename(material_name + ".mtr")
            decoded = decode_mtr(store.read(material_member)) if material_member else decode_mtr(b"")
            definition = {
                "name": material_name,
                "source_mtr": material_member,
                **decoded,
                "texture_object": None,
                "source_texture_map": None,
                "source_picture": None,
                "texture_uri": None,
                "texture_has_alpha": False,
            }

            texture_index = material_to_texture.get(material_index)
            if texture_index is not None and 0 <= texture_index < len(texture_objects):
                texture_name = texture_objects[texture_index]
                definition["texture_object"] = texture_name
                texture_member = store.find_basename(
                    texture_name + ".txt", f"{scene_prefix}/TEXTURES2D"
                ) or store.find_basename(texture_name + ".txt")
                definition["source_texture_map"] = texture_member
                if texture_member:
                    picture_member = find_picture_member(store, store.read(texture_member))
                    definition["source_picture"] = picture_member
                    if picture_member:
                        if picture_member not in texture_exports:
                            uri, texture_meta = export_picture(store, picture_member, texture_dir)
                            texture_exports[picture_member] = {"uri": uri, **texture_meta}
                        texture_meta = texture_exports[picture_member]
                        definition["texture_uri"] = texture_meta.get("uri")
                        definition["texture_has_alpha"] = bool(
                            texture_meta.get("has_nonopaque_alpha")
                        )

            material_defs[material_name] = definition
            resolved.append(definition)
        model_material_defs[model_index] = resolved

    return model_material_defs, material_defs, chapters, relations


def decode_class4_with_slots(data: bytes, payload_offset: int, end: int) -> dict:
    if payload_offset + 8 > end:
        raise ValueError("short class-4 mesh header")
    vertex_count = int.from_bytes(data[payload_offset + 4 : payload_offset + 8], "big")
    cursor = payload_offset + 8
    vertex_end = cursor + vertex_count * 14
    if vertex_count > 2_000_000 or vertex_end > end:
        raise ValueError("invalid class-4 vertex array")
    vertices = []
    for offset in range(cursor, vertex_end, 14):
        xyz = struct.unpack_from(">fff", data, offset)
        if not all(math.isfinite(value) for value in xyz):
            raise ValueError("non-finite class-4 vertex")
        vertices.append(tuple(float(value) for value in xyz))
    cursor = vertex_end
    if vertex_count == 0 or cursor + 4 > end:
        return {"vertices": vertices, "polygons": []}

    polygon_count = int.from_bytes(data[cursor : cursor + 4], "big")
    cursor += 4
    polygons = []
    for _ in range(polygon_count):
        if cursor + 2 > end:
            raise ValueError("class-4 polygon header overrun")
        corner_count = int.from_bytes(data[cursor : cursor + 2], "big")
        cursor += 2
        if corner_count < 3 or cursor + corner_count * 28 + 4 > end:
            raise ValueError("class-4 polygon overrun")
        contours: list[list[dict]] = [[]]
        for corner_index in range(corner_count):
            offset = cursor + corner_index * 28
            vertex_index = int.from_bytes(data[offset : offset + 4], "big")
            if vertex_index == assembled.CONTOUR_SEPARATOR:
                contours.append([])
                continue
            if vertex_index >= vertex_count:
                raise ValueError("class-4 vertex index out of range")
            nx, ny, nz = struct.unpack_from(">fff", data, offset + 4)
            normal = None
            if all(math.isnan(value) for value in (nx, ny, nz)):
                normal = None
            elif any(math.isnan(value) for value in (nx, ny, nz)):
                raise ValueError("mixed NaN class-4 normal")
            else:
                normal = (float(nx), float(ny), float(nz))
            u, v = struct.unpack_from(">ff", data, offset + 16)
            contours[-1].append(
                {
                    "vertex_index": vertex_index,
                    "normal": normal,
                    "uv": (float(u), float(v)),
                }
            )
        metadata_offset = cursor + corner_count * 28
        metadata = int.from_bytes(data[metadata_offset : metadata_offset + 4], "big")
        material_slot = (metadata >> 16) & 0xFFFF
        polygons.append(
            {
                "contours": [contour for contour in contours if contour],
                "material_slot": material_slot,
                "metadata": metadata,
            }
        )
        cursor = metadata_offset + 4
    return {"vertices": vertices, "polygons": polygons}


def _append_accessor(
    gltf: dict,
    buffer: bytearray,
    payload: bytes,
    component_type: int,
    accessor_type: str,
    count: int,
    *,
    target: int | None = None,
    minimum: list[float] | None = None,
    maximum: list[float] | None = None,
) -> int:
    offset, length = assembled._add_chunk(buffer, payload)
    view = {"buffer": 0, "byteOffset": offset, "byteLength": length}
    if target is not None:
        view["target"] = target
    gltf["bufferViews"].append(view)
    accessor = {
        "bufferView": len(gltf["bufferViews"]) - 1,
        "componentType": component_type,
        "count": count,
        "type": accessor_type,
    }
    if minimum is not None:
        accessor["min"] = minimum
    if maximum is not None:
        accessor["max"] = maximum
    gltf["accessors"].append(accessor)
    return len(gltf["accessors"]) - 1


def emit_slotted_mesh(
    gltf: dict,
    buffer: bytearray,
    name: str,
    mesh: dict,
    material_indices: list[int],
) -> tuple[int | None, list[int]]:
    by_slot: dict[int, list[list[dict]]] = {}
    for polygon in mesh["polygons"]:
        triangles = assembled._triangulate(mesh, polygon["contours"])
        by_slot.setdefault(int(polygon["material_slot"]), []).extend(triangles)

    primitives = []
    used_slots = []
    for material_slot, triangles in sorted(by_slot.items()):
        primitive_vertices = []
        vertex_map = {}
        indices = []
        for triangle in triangles:
            positions = [mesh["vertices"][corner["vertex_index"]] for corner in triangle]
            fallback_normal = assembled._face_normal(positions)
            for corner, position in zip(triangle, positions):
                normal = corner["normal"] or fallback_normal
                key = position, corner["uv"], normal
                if key not in vertex_map:
                    vertex_map[key] = len(primitive_vertices)
                    primitive_vertices.append(key)
                indices.append(vertex_map[key])
        if not indices:
            continue

        positions = [value for position, _, _ in primitive_vertices for value in position]
        texcoords = [value for _, uv, _ in primitive_vertices for value in uv]
        normals = [value for _, _, normal in primitive_vertices for value in normal]
        points = [position for position, _, _ in primitive_vertices]
        primitive = {
            "attributes": {
                "POSITION": _append_accessor(
                    gltf,
                    buffer,
                    struct.pack(f"<{len(positions)}f", *positions),
                    5126,
                    "VEC3",
                    len(primitive_vertices),
                    target=34962,
                    minimum=[min(point[axis] for point in points) for axis in range(3)],
                    maximum=[max(point[axis] for point in points) for axis in range(3)],
                ),
                "TEXCOORD_0": _append_accessor(
                    gltf,
                    buffer,
                    struct.pack(f"<{len(texcoords)}f", *texcoords),
                    5126,
                    "VEC2",
                    len(primitive_vertices),
                    target=34962,
                ),
                "NORMAL": _append_accessor(
                    gltf,
                    buffer,
                    struct.pack(f"<{len(normals)}f", *normals),
                    5126,
                    "VEC3",
                    len(primitive_vertices),
                    target=34962,
                ),
            },
            "indices": _append_accessor(
                gltf,
                buffer,
                struct.pack(f"<{len(indices)}I", *indices),
                5125,
                "SCALAR",
                len(indices),
                target=34963,
            ),
            "extras": {"source_material_slot": material_slot},
        }
        if material_slot < len(material_indices):
            primitive["material"] = material_indices[material_slot]
        primitives.append(primitive)
        used_slots.append(material_slot)

    if not primitives:
        return None, used_slots
    gltf["meshes"].append({"name": name, "primitives": primitives})
    return len(gltf["meshes"]) - 1, used_slots


def append_source_materials(
    gltf: dict,
    material_defs: dict[str, dict],
) -> dict[str, int]:
    gltf.setdefault("materials", [])
    gltf.setdefault("images", [])
    gltf.setdefault("textures", [])
    image_by_uri = {
        image.get("uri"): index
        for index, image in enumerate(gltf["images"])
        if image.get("uri")
    }
    texture_by_image = {
        texture.get("source"): index
        for index, texture in enumerate(gltf["textures"])
        if texture.get("source") is not None
    }
    material_index = {}

    for material_name, definition in material_defs.items():
        material = {
            "name": material_name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [
                    *[float(value) for value in definition["diffuse_rgb"]],
                    float(definition["alpha"]),
                ],
                "metallicFactor": 0.0,
                "roughnessFactor": float(definition["roughness"]),
            },
            "doubleSided": True,
            "extras": {
                "source_mtr": definition.get("source_mtr"),
                "source_texture_map": definition.get("source_texture_map"),
                "source_picture": definition.get("source_picture"),
                "source_shininess": definition.get("shininess"),
                "source_specular_rgb": definition.get("specular_rgb"),
            },
        }
        texture_uri = definition.get("texture_uri")
        if texture_uri:
            if texture_uri not in image_by_uri:
                image_by_uri[texture_uri] = len(gltf["images"])
                gltf["images"].append({"uri": texture_uri})
            image_index = image_by_uri[texture_uri]
            if image_index not in texture_by_image:
                texture_by_image[image_index] = len(gltf["textures"])
                gltf["textures"].append({"source": image_index})
            material["pbrMetallicRoughness"]["baseColorTexture"] = {
                "index": texture_by_image[image_index]
            }
        if definition.get("alpha", 1.0) < 0.999 or definition.get("texture_has_alpha"):
            material["alphaMode"] = "BLEND"
        gltf["materials"].append(material)
        material_index[material_name] = len(gltf["materials"]) - 1
    return material_index


def compact_referenced_meshes(gltf: dict) -> None:
    referenced = sorted(
        {
            int(node["mesh"])
            for node in gltf.get("nodes", [])
            if node.get("mesh") is not None
        }
    )
    remap = {old: new for new, old in enumerate(referenced)}
    gltf["meshes"] = [gltf["meshes"][old] for old in referenced]
    for node in gltf.get("nodes", []):
        if node.get("mesh") is not None:
            node["mesh"] = remap[int(node["mesh"])]


def bind_materials(
    source_hrc: Path,
    scene_dsc: Path,
    asset_source: Path,
    scene_prefix: str,
    output: Path,
    *,
    include_parametric: bool = True,
    curve_steps: int = 64,
    surface_steps_u: int = 32,
    surface_steps_v: int = 32,
) -> dict:
    if include_parametric:
        base_summary = parametric.export_parametric(
            source_hrc,
            output,
            curve_steps=curve_steps,
            surface_steps_u=surface_steps_u,
            surface_steps_v=surface_steps_v,
        )
    else:
        base_summary = assembled.export_hrc(source_hrc, output)

    gltf = json.loads(output.read_text(encoding="utf-8"))
    bin_path = output.with_suffix(".bin")
    buffer = bytearray(bin_path.read_bytes())
    texture_dir = output.parent / "textures"
    texture_dir.mkdir(parents=True, exist_ok=True)

    store = open_store(asset_source)
    model_material_defs, material_defs, chapters, relations = build_scene_materials(
        scene_dsc, store, scene_prefix, texture_dir
    )
    source_material_index = append_source_materials(gltf, material_defs)

    data = source_hrc.read_bytes()
    report = hrc_tree.probe(source_hrc)
    outer = dict(report.get("outer_model") or {})
    records = [dict(item) for item in report.get("tree", [])]
    all_items = []
    if outer:
        all_items.append(outer)
    all_items.extend(records)

    gltf_node_by_offset = {
        int(node.get("extras", {}).get("source_offset")): index
        for index, node in enumerate(gltf.get("nodes", []))
        if node.get("extras", {}).get("source_offset") is not None
    }

    models = chapters.get("MODELS", [])
    direct_materials_by_node: dict[str, list[dict]] = {}
    for item in all_items:
        model_index = resolve_model_index(str(item.get("name") or ""), models)
        if model_index is not None and model_index in model_material_defs:
            direct_materials_by_node[str(item["name"])] = model_material_defs[model_index]

    parent_by_name = {
        str(item["name"]): item.get("parent_name") for item in records if item.get("name")
    }
    inherited_materials: dict[str, str] = {}
    slot_errors = []
    rebound = []
    decode_failures = []

    for item_index, item in enumerate(all_items):
        if item.get("class_id") != 4:
            continue
        name = str(item.get("name") or f"class4_{item_index}")
        source_offset = item.get("offset")
        node_index = gltf_node_by_offset.get(int(source_offset)) if source_offset is not None else None
        if node_index is None:
            decode_failures.append({"name": name, "error": "missing_gltf_node_for_source_offset"})
            continue

        definitions = direct_materials_by_node.get(name, [])
        if not definitions:
            parent = parent_by_name.get(name)
            visited = set()
            while parent and parent not in visited:
                visited.add(parent)
                if direct_materials_by_node.get(str(parent)):
                    definitions = direct_materials_by_node[str(parent)]
                    inherited_materials[name] = str(parent)
                    break
                parent = parent_by_name.get(str(parent))

        if item is outer:
            payload_offset = assembled._outer_payload_offset(data, outer)
            end = (
                int(records[0]["offset"]) - int(records[0]["zero_run"])
                if records
                else len(data)
            )
        else:
            record_index = records.index(item)
            payload_offset = int(item["payload_offset"])
            end = assembled._record_end(records, record_index, len(data))

        try:
            mesh = decode_class4_with_slots(data, payload_offset, end)
            material_indices = [source_material_index[definition["name"]] for definition in definitions]
            mesh_index, used_slots = emit_slotted_mesh(
                gltf, buffer, name, mesh, material_indices
            )
        except Exception as exc:
            decode_failures.append(
                {"name": name, "error": f"{type(exc).__name__}: {exc}"}
            )
            continue

        unresolved_slots = [slot for slot in used_slots if slot >= len(definitions)]
        if unresolved_slots:
            slot_errors.append(
                {
                    "name": name,
                    "used_slots": used_slots,
                    "material_count": len(definitions),
                    "unresolved_slots": unresolved_slots,
                }
            )
        if mesh_index is not None:
            gltf["nodes"][node_index]["mesh"] = mesh_index
            gltf["nodes"][node_index].setdefault("extras", {})[
                "source_material_binding"
            ] = "inherited" if name in inherited_materials else "direct"
            rebound.append(name)

    gltf["buffers"][0]["byteLength"] = len(buffer)
    compact_referenced_meshes(gltf)
    output.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    bin_path.write_bytes(buffer)

    referenced_materials = sorted(
        {
            primitive.get("material")
            for mesh in gltf.get("meshes", [])
            for primitive in mesh.get("primitives", [])
            if primitive.get("material") is not None
        }
    )
    summary = {
        "schema": "bz2-dsc-material-gltf-v1",
        "source_hrc": str(source_hrc),
        "scene_dsc": str(scene_dsc),
        "asset_source": str(asset_source),
        "scene_prefix": scene_prefix,
        "output": str(output),
        "base_export": base_summary,
        "class4_nodes_rebound": len(rebound),
        "inherited_material_nodes": inherited_materials,
        "slot_error_count": len(slot_errors),
        "slot_errors": slot_errors,
        "decode_failure_count": len(decode_failures),
        "decode_failures": decode_failures,
        "source_material_count": len(material_defs),
        "source_texture_count": len(
            {definition.get("texture_uri") for definition in material_defs.values() if definition.get("texture_uri")}
        ),
        "referenced_gltf_material_count": len(referenced_materials),
        "final_referenced_mesh_count": len(gltf.get("meshes", [])),
        "notes": [
            "DSC relation code 300 supplies ordered per-model material slots",
            "DSC relation code 401 links materials to TEXTURES2D objects",
            "class-4 polygon metadata upper 16 bits select the material slot",
            "children without direct model-material links inherit the nearest HRC ancestor material list",
            "MTR-to-PBR conversion is provisional; original MTR fields are retained in material extras",
            "PIC texture alpha triggers glTF BLEND mode when any source alpha pixel is non-opaque",
            "source HRC UVs are preserved unchanged and should be checked against original render PICs",
        ],
    }
    output.with_suffix(".materials.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_hrc", type=Path)
    parser.add_argument("scene_dsc", type=Path)
    parser.add_argument("asset_source", type=Path, help="extracted asset root or source ZIP archive")
    parser.add_argument("scene_prefix", help="logical scene folder inside asset_source, e.g. adconcept")
    parser.add_argument("output", type=Path)
    parser.add_argument("--no-parametric", action="store_true")
    parser.add_argument("--curve-steps", type=int, default=64)
    parser.add_argument("--surface-steps-u", type=int, default=32)
    parser.add_argument("--surface-steps-v", type=int, default=32)
    args = parser.parse_args()
    summary = bind_materials(
        args.source_hrc,
        args.scene_dsc,
        args.asset_source,
        args.scene_prefix,
        args.output,
        include_parametric=not args.no_parametric,
        curve_steps=max(2, args.curve_steps),
        surface_steps_u=max(2, args.surface_steps_u),
        surface_steps_v=max(2, args.surface_steps_v),
    )
    print(json.dumps(summary, indent=2))
    return 1 if summary["slot_error_count"] or summary["decode_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

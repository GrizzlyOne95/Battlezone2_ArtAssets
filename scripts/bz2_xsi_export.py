#!/usr/bin/env python3
"""Export a reconstructed BZ2 scene bundle as an engine-ready dotXSI model.

Battlezone II (1.3) and Battlezone: Combat Commander load text dotXSI 3.2
models (``xsi 0101txt 0032``) with the conventions used by the community
``io_scene_bz2xsi`` tools vendored in ``tools/io_scene_bz2xsi``:

* one root ``Frame`` per file;
* rigid ``FrameTransformMatrix`` values (BZ2 does not support frame scale);
* per-face ``SI_Material`` entries with at most one ``SI_Texture2D`` filename;
* one ``SI_MeshTextureCoords`` set in Softimage (bottom-left) UV space;
* per-corner ``SI_MeshNormals``.

The reconstructed ``scene.gltf`` already uses Softimage-native axes and units:
the Stasis Truck reconstruction matches the original game file
``ISDF_vehicles/PICTURES/ivstas00.xsi`` in frame names, placement, vertex
positions and UVs. This exporter therefore performs no axis conversion. It
only restructures the scene for the engine contract:

* cameras, lights and mesh-less subtrees are omitted;
* additional DSC roots are attached below the primary (largest) root with
  their world placement preserved, as in the shipped game XSI files;
* any frame scale/shear/mirroring is baked into that frame's vertices and
  normals so every written frame matrix is rigid (face winding is reversed for
  mirrored frames);
* each material's texture stack is reduced to its bound base layer and that
  layer's *effective* UV set is baked, using exactly the same decisions as the
  Blender asset-fidelity stage (``blender_apply_bz2_asset_uvs``):

  - special material modes 7/8 keep the source UVs;
  - usable authored CurrentUV receives the live TXMP effects
    (texture-matrix rotation, repeat, +6 scale/offset, crop);
  - all-zero/parametric UVs with a supported projection code get generated
    projection UVs in native object space;
  - otherwise repeat/+6/crop are composed onto the source UVs;
  - untextured materials on nodes with a model-local code-400 base projection
    use that projection's texture and generated UVs.

Textures are written once per distinct picture as ``<picture>.tga`` beside the
``.xsi``; the XSI references the bare filename, as the engines expect.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "tools" / "io_scene_bz2xsi"))

import bz2_projection_uv as projection_uv  # noqa: E402
import bz2xsi  # noqa: E402

bz2xsi.ALLOW_PRINT = False

SCHEMA = "bz2-engine-xsi-export-v1"
GLTF_UV_CONVENTION = "gltf_top_left_v1"
IDENTITY_TOLERANCE = 1.0e-6
_COMPONENT_DTYPES = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_TYPE_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")


class ExportError(RuntimeError):
    pass


class NoGeometryError(ExportError):
    """The scene has no triangle geometry (camera/light/FX-only shot): no engine model."""


# ---------------------------------------------------------------------------
# glTF access


class GltfDocument:
    def __init__(self, gltf_path: Path):
        self.path = gltf_path
        self.doc = json.loads(gltf_path.read_text(encoding="utf-8"))
        self.buffers = [
            (gltf_path.parent / buffer["uri"]).read_bytes() for buffer in self.doc.get("buffers", [])
        ]
        extras = (self.doc.get("asset") or {}).get("extras") or {}
        self.uv_is_gltf_space = extras.get("bz2_uv_convention") == GLTF_UV_CONVENTION

    def accessor(self, index: int) -> np.ndarray:
        accessor = self.doc["accessors"][index]
        view = self.doc["bufferViews"][accessor["bufferView"]]
        dtype = np.dtype(_COMPONENT_DTYPES[accessor["componentType"]]).newbyteorder("<")
        width = _TYPE_WIDTH[accessor["type"]]
        count = int(accessor["count"])
        base = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
        stride = int(view.get("byteStride") or dtype.itemsize * width)
        data = self.buffers[int(view["buffer"])]
        if stride == dtype.itemsize * width:
            array = np.frombuffer(data, dtype=dtype, count=count * width, offset=base)
        else:
            array = np.lib.stride_tricks.as_strided(
                np.frombuffer(data, dtype=np.uint8, offset=base).view(dtype),
                shape=(count, width),
                strides=(stride, dtype.itemsize),
            ).copy().reshape(-1)
        array = array.reshape(count, width) if width > 1 else array
        return array.astype(np.float64) if accessor["componentType"] == 5126 else array.astype(np.int64)

    def softimage_uv(self, index: int) -> np.ndarray:
        uv = self.accessor(index).copy()
        if self.uv_is_gltf_space:
            uv[:, 1] = 1.0 - uv[:, 1]
        return uv


def node_local_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
    matrix = np.eye(4)
    if "scale" in node:
        matrix = np.diag([*map(float, node["scale"]), 1.0]) @ matrix
    if "rotation" in node:
        x, y, z, w = (float(v) for v in node["rotation"])
        rotation = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ]
        )
        block = np.eye(4)
        block[:3, :3] = rotation
        matrix = block @ matrix
    if "translation" in node:
        block = np.eye(4)
        block[:3, 3] = [float(v) for v in node["translation"]]
        matrix = block @ matrix
    return matrix


def rigid_split(world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split a world matrix into rigid placement G and linear residual M (W = G @ M)."""
    linear = world[:3, :3]
    u, _s, vt = np.linalg.svd(linear)
    rotation = u @ vt
    if np.linalg.det(rotation) < 0:
        u = u.copy()
        u[:, -1] *= -1.0
        rotation = u @ vt
    rigid = np.eye(4)
    rigid[:3, :3] = rotation
    rigid[:3, 3] = world[:3, 3]
    residual = rotation.T @ linear
    return rigid, residual


def xsi_matrix(matrix: np.ndarray) -> bz2xsi.Matrix:
    # dotXSI rows are the transposed column-vector matrix (right, up, front, posit).
    rows = [tuple(float(value) for value in row) for row in matrix.T]
    return bz2xsi.Matrix(*rows)


def safe_name(name: str) -> str:
    return _SAFE_NAME.sub("_", name or "unnamed") or "unnamed"


# ---------------------------------------------------------------------------
# Sidecars and UV policy


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _base_layer(layer_record: dict | None) -> dict | None:
    if not layer_record:
        return None
    layers = layer_record.get("layers") or []
    return next((layer for layer in layers if layer.get("bound_as_gltf_base_color")), None)


def _combined_source_transform(uv: np.ndarray, layer: dict) -> np.ndarray:
    repeats = layer.get("si_texture2d_repeat_uv") or [1, 1]
    scale = layer.get("si_texture2d_uv_scale") or [1.0, 1.0]
    offset = layer.get("si_texture2d_uv_offset") or [0.0, 0.0]
    out = uv * np.array([float(repeats[0]), float(repeats[1])]) * np.array([float(scale[0]), float(scale[1])])
    out = out + np.array([float(offset[0]), float(offset[1])])
    size = (layer.get("width"), layer.get("height"))
    if size[0] and size[1]:
        out = np.array([projection_uv.apply_crop(tuple(item), layer.get("crop_rect_pixels_raw"), size) for item in out])
    return out


def _projected_uvs(positions: np.ndarray, triangles: np.ndarray, all_points: np.ndarray, projection: dict) -> np.ndarray:
    """Generate per-corner projection UVs; supports fitted to the node mesh."""
    _prepared_all, bounds = projection_uv.prepare_projection_points(all_points.tolist(), projection)
    prepared = (
        positions.tolist()
        if projection_uv.matrix_srt_is_identity(projection)
        else [projection_uv.projection_space_point(point, projection) for point in positions.tolist()]
    )
    corners = np.zeros((len(triangles), 3, 2))
    for face, tri in enumerate(triangles):
        corners[face] = projection_uv.project_prepared_polygon([prepared[i] for i in tri], bounds, projection)
    return corners


def _can_generate(projection: dict) -> bool:
    code = int(projection.get("projection_or_mapping_code_candidate") or 0)
    return projection_uv.projection_type_name(code) is not None and (
        projection_uv.matrix_srt_is_identity(projection) or projection_uv.projection_rotation_supported(projection)
    )


def effective_uvs(
    *,
    source_corner_uv: np.ndarray | None,
    positions: np.ndarray,
    triangles: np.ndarray,
    node_points: np.ndarray,
    node_uv_usable: bool,
    layer: dict | None,
    model_projection: dict | None,
) -> tuple[np.ndarray | None, str]:
    """Return (triangle-corner UVs [F,3,2] in Softimage space, decision label)."""
    if layer is not None:
        code = int(layer.get("projection_or_mapping_code_candidate") or 0)
        if code in {7, 8}:
            return source_corner_uv, f"special_material_mode_{code}_source_uv"
        if node_uv_usable and source_corner_uv is not None:
            try:
                flat = source_corner_uv.reshape(-1, 2)
                out = np.array([projection_uv.apply_current_uv_effects((u, v, 0.0), layer)[:2] for u, v in flat])
                return out.reshape(-1, 3, 2), "source_currentuv_with_live_txmp_effects"
            except ValueError:
                return _combined_source_transform(source_corner_uv.reshape(-1, 2), layer).reshape(-1, 3, 2), (
                    "source_uv_plus_confirmed_repeat_image_transform"
                )
        if _can_generate(layer):
            return _projected_uvs(positions, triangles, node_points, layer), f"generated_projection_code_{code}"
        if source_corner_uv is None:
            return None, "no_source_uv_and_unsupported_projection"
        reason = (
            "nonidentity_matrix_srt_fallback"
            if not projection_uv.matrix_srt_is_identity(layer)
            else f"unsupported_projection_code_{code}_fallback"
        )
        return _combined_source_transform(source_corner_uv.reshape(-1, 2), layer).reshape(-1, 3, 2), reason
    if model_projection is not None:
        return _projected_uvs(positions, triangles, node_points, model_projection), (
            "model_local_code400_projection_generated"
        )
    return source_corner_uv, "untextured_source_uv"


# ---------------------------------------------------------------------------
# Textures


class TextureLibrary:
    def __init__(self, bundle_dir: Path, out_dir: Path):
        self.bundle_dir = bundle_dir
        self.out_dir = out_dir
        self.by_hash: dict[str, str] = {}
        self.used_names: set[str] = set()
        self.records: list[dict] = []
        self.missing: list[str] = []

    def filename_for(self, uri: str | None, source_picture: str | None) -> str | None:
        if not uri:
            return None
        path = (self.bundle_dir / uri).resolve()
        if not path.is_file():
            self.missing.append(uri)
            return None
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("RGBA")
            digest = hashlib.sha1(image.tobytes() + repr(image.size).encode()).hexdigest()
            if digest in self.by_hash:
                return self.by_hash[digest]
            stem = safe_name(Path(source_picture).stem if source_picture else path.stem.split("__")[-1])
            name, index = f"{stem}.tga", 2
            while name.lower() in self.used_names:
                name, index = f"{stem}_{index}.tga", index + 1
            self.used_names.add(name.lower())
            alpha = image.getchannel("A")
            opaque = alpha.getextrema() == (255, 255)
            self.out_dir.mkdir(parents=True, exist_ok=True)
            (image.convert("RGB") if opaque else image).save(self.out_dir / name)
            self.by_hash[digest] = name
            self.records.append(
                {"file": name, "source_picture": source_picture, "bundle_png": uri, "size": list(image.size), "alpha": not opaque}
            )
            return name


# ---------------------------------------------------------------------------
# Materials


def _xsi_material(material: dict | None, texture: str | None) -> bz2xsi.Material:
    if material is None:
        return bz2xsi.Material(texture=texture)
    pbr = material.get("pbrMetallicRoughness") or {}
    extras = material.get("extras") or {}
    mtr = extras.get("bz2_softimage_mtr") or {}
    diffuse = list(mtr.get("diffuse_rgb") or pbr.get("baseColorFactor", [0.7, 0.7, 0.7, 1.0])[:3])
    alpha = float((pbr.get("baseColorFactor") or [0, 0, 0, 1.0])[3])
    if mtr.get("transparency"):
        alpha = min(alpha, 1.0 - float(mtr["transparency"]))
    specular = list(mtr.get("specular_rgb") or extras.get("source_specular_rgb") or bz2xsi.DEFAULT_SPECULAR)
    hardness = float(mtr.get("shininess") or extras.get("source_shininess") or bz2xsi.DEFAULT_HARDNESS)
    ambient = list(mtr.get("ambient_rgb") or bz2xsi.DEFAULT_AMBIENT)
    emissive = list(material.get("emissiveFactor") or bz2xsi.DEFAULT_EMISSIVE)
    shading = mtr.get("xsi_shading_type")
    return bz2xsi.Material(
        diffuse=[float(c) for c in diffuse[:3]] + [alpha],
        hardness=hardness,
        specular=[float(c) for c in specular[:3]],
        ambient=[float(c) for c in ambient[:3]],
        emissive=[float(c) for c in emissive[:3]],
        shading_type=int(shading) if shading is not None else bz2xsi.DEFAULT_SHADING_TYPE,
        texture=texture,
    )


# ---------------------------------------------------------------------------
# Export


def _quantize(values: np.ndarray) -> list[tuple]:
    # The writer prints %f (6 decimals); weld on exactly what will be written.
    return [tuple(row) for row in np.round(values, 6).tolist()]


def _index_list(values: list[tuple]) -> tuple[list[tuple], list[int]]:
    table: dict[tuple, int] = {}
    unique: list[tuple] = []
    indices = []
    for value in values:
        index = table.get(value)
        if index is None:
            index = table[value] = len(unique)
            unique.append(value)
        indices.append(index)
    return unique, indices


def _is_staging_ground(node: dict) -> bool:
    """ROOT class-1 primitive grids are the render-stage ground planes of presentation scenes."""
    extras = node.get("extras") or {}
    return extras.get("class_id") == 1 and bool(extras.get("class1_grid_geometry_emitted"))


def export_bundle(
    bundle_dir: Path,
    out_dir: Path | None = None,
    name: str | None = None,
    *,
    include_staging: bool = False,
) -> dict:
    bundle_dir = bundle_dir.resolve()
    gltf_path = bundle_dir / "scene.gltf"
    if not gltf_path.is_file():
        raise ExportError(f"missing reconstructed scene: {gltf_path}")
    out_dir = (out_dir or bundle_dir / "engine").resolve()
    manifest = _load_json(bundle_dir / "reconstruction.json")
    scene_stem = name or Path(str(manifest.get("scene_dsc") or bundle_dir.name)).stem
    xsi_name = safe_name(scene_stem.replace(".", "_")) + ".xsi"

    gltf = GltfDocument(gltf_path)
    doc = gltf.doc
    nodes = doc.get("nodes", [])
    materials = doc.get("materials", [])
    layer_records = {
        int(record["gltf_material_index"]): record
        for record in _load_json(bundle_dir / "scene.texture_layers.json").get("materials") or []
        if record.get("gltf_material_index") is not None
    }
    model_records = {
        int(record["gltf_node_index"]): record
        for record in _load_json(bundle_dir / "scene.model_textures.json").get("models") or []
        if record.get("gltf_node_index") is not None
    }
    textures = TextureLibrary(bundle_dir, out_dir)

    # --- hierarchy and inclusion -------------------------------------------
    parent_of: dict[int, int] = {}
    for index, node in enumerate(nodes):
        for child in node.get("children", []):
            parent_of[int(child)] = index
    scene_roots = [int(i) for i in doc["scenes"][doc.get("scene", 0)]["nodes"]]

    world: dict[int, np.ndarray] = {}

    def compute_world(index: int, parent_world: np.ndarray) -> None:
        world[index] = parent_world @ node_local_matrix(nodes[index])
        for child in nodes[index].get("children", []):
            compute_world(int(child), world[index])

    for root in scene_roots:
        compute_world(root, np.eye(4))

    def vertex_count(index: int) -> int:
        mesh_index = nodes[index].get("mesh")
        if mesh_index is None:
            return 0
        return sum(
            int(doc["accessors"][p["attributes"]["POSITION"]]["count"])
            for p in doc["meshes"][mesh_index]["primitives"]
            if p.get("mode", 4) == 4
        )

    subtree_vertices: dict[int, int] = {}

    def measure(index: int) -> int:
        total = vertex_count(index) + sum(measure(int(c)) for c in nodes[index].get("children", []))
        subtree_vertices[index] = total
        return total

    for root in scene_roots:
        measure(root)
    kept_roots = [root for root in scene_roots if subtree_vertices.get(root, 0) > 0]
    staging = [root for root in kept_roots if _is_staging_ground(nodes[root])]
    if staging and not include_staging and len(staging) < len(kept_roots):
        # Presentation scenes place the unit on a large reflective ground grid
        # (e.g. the tank/walker 13x13 floors). It is not part of the engine model.
        kept_roots = [root for root in kept_roots if root not in staging]
        for root in staging:
            subtree_vertices[root] = 0
    omitted = [
        {
            "node": nodes[i].get("name"),
            "reason": (
                "camera" if nodes[i].get("camera") is not None
                else "staging_ground_grid" if i in staging and not include_staging
                else "no_mesh_geometry"
            ),
        }
        for i in world
        if subtree_vertices.get(i, 0) == 0 and (i in scene_roots or subtree_vertices.get(parent_of.get(i, -1), 0) > 0)
    ]
    if not kept_roots:
        raise NoGeometryError("scene contains no triangle mesh geometry to export")
    primary = max(kept_roots, key=lambda i: (subtree_vertices[i], -scene_roots.index(i)))

    # --- frames ---------------------------------------------------------------
    xsi = bz2xsi.XSI()
    used_names: set[str] = set()
    report_nodes: list[dict] = []
    uv_decisions: dict[str, int] = {}
    skipped_primitives: list[dict] = []
    stats = {"frames": 0, "meshes": 0, "triangles": 0, "degenerate_triangles_dropped": 0, "baked_residual_frames": 0, "mirrored_frames": 0}

    def unique_name(raw: str) -> str:
        base = safe_name(raw)
        name, index = base, 2
        while name.lower() in used_names:
            name, index = f"{base}_{index}", index + 1
        used_names.add(name.lower())
        return name

    def build_mesh(index: int, residual: np.ndarray, frame_name: str) -> bz2xsi.Mesh | None:
        node = nodes[index]
        mesh_index = node.get("mesh")
        if mesh_index is None:
            return None
        gltf_mesh = doc["meshes"][mesh_index]
        parametric = str((gltf_mesh.get("extras") or {}).get("uv_source") or "") == "normalized_parameter_space"

        primitives = []
        for p_index, primitive in enumerate(gltf_mesh["primitives"]):
            mode = primitive.get("mode", 4)
            if mode != 4:
                skipped_primitives.append({"node": node.get("name"), "primitive": p_index, "mode": mode})
                continue
            attributes = primitive["attributes"]
            positions = gltf.accessor(attributes["POSITION"])
            triangles = (
                gltf.accessor(primitive["indices"]).reshape(-1, 3)
                if primitive.get("indices") is not None
                else np.arange(len(positions)).reshape(-1, 3)
            )
            normals = gltf.accessor(attributes["NORMAL"]) if "NORMAL" in attributes else None
            uv = gltf.softimage_uv(attributes["TEXCOORD_0"]) if "TEXCOORD_0" in attributes else None
            primitives.append((p_index, primitive, positions, triangles, normals, uv))
        if not primitives:
            return None

        node_points = np.concatenate([item[2] for item in primitives])
        all_uv = [item[5] for item in primitives if item[5] is not None]
        node_uv_usable = bool(all_uv) and not parametric and bool(np.any(np.abs(np.concatenate(all_uv)) > 1.0e-12))
        model_record = model_records.get(index) or {}
        model_base = next(
            (
                projection
                for projection in model_record.get("local_texture_projections") or []
                if projection.get("role_candidate") == "base_or_default_candidate"
                and projection.get("uri")
                and _can_generate(projection)
            ),
            None,
        )

        mirrored = np.linalg.det(residual) < 0
        bake = not np.allclose(residual, np.eye(3), atol=IDENTITY_TOLERANCE)
        normal_matrix = np.linalg.inv(residual).T if bake else None

        positions_out: list[np.ndarray] = []
        normals_out: list[np.ndarray] = []
        uvs_out: list[np.ndarray] = []
        face_materials: list[bz2xsi.Material] = []
        for p_index, primitive, positions, triangles, normals, uv in primitives:
            material_index = primitive.get("material")
            material = materials[material_index] if material_index is not None else None
            layer = _base_layer(layer_records.get(material_index)) if material_index is not None else None
            texture_file = None
            if layer is not None:
                texture_file = textures.filename_for(layer.get("uri"), layer.get("resolved_picture") or layer.get("source_picture"))
            projection = None
            if layer is None and model_base is not None and not (
                (material or {}).get("pbrMetallicRoughness") or {}
            ).get("baseColorTexture"):
                projection = model_base
                texture_file = textures.filename_for(model_base.get("uri"), model_base.get("resolved_picture"))
            corner_uv = uv[triangles] if uv is not None else None
            try:
                tri_uv, decision = effective_uvs(
                    source_corner_uv=corner_uv,
                    positions=positions,
                    triangles=triangles,
                    node_points=node_points,
                    node_uv_usable=node_uv_usable,
                    layer=layer if texture_file else None,
                    model_projection=projection if texture_file else None,
                )
            except ValueError as exc:
                tri_uv, decision = corner_uv, f"uv_generation_error_source_uv:{exc}"
            uv_decisions[decision] = uv_decisions.get(decision, 0) + 1
            if texture_file is None and layer is not None:
                uv_decisions["textured_material_picture_missing"] = uv_decisions.get("textured_material_picture_missing", 0) + 1

            corner_pos = positions[triangles]
            corner_nrm = normals[triangles] if normals is not None else None
            if bake:
                corner_pos = corner_pos @ residual.T
                if corner_nrm is not None:
                    corner_nrm = corner_nrm @ normal_matrix.T
            if corner_nrm is None:
                edge_a = corner_pos[:, 1] - corner_pos[:, 0]
                edge_b = corner_pos[:, 2] - corner_pos[:, 0]
                face_normal = np.cross(edge_a, edge_b)
                corner_nrm = np.repeat(face_normal[:, None, :], 3, axis=1)
            length = np.linalg.norm(corner_nrm, axis=2, keepdims=True)
            corner_nrm = np.divide(corner_nrm, length, out=np.zeros_like(corner_nrm), where=length > 1.0e-12)
            if tri_uv is None:
                tri_uv = np.zeros((len(triangles), 3, 2))
            if mirrored:
                corner_pos, corner_nrm, tri_uv = corner_pos[:, ::-1], corner_nrm[:, ::-1], tri_uv[:, ::-1]
            positions_out.append(corner_pos)
            normals_out.append(corner_nrm)
            uvs_out.append(np.asarray(tri_uv, dtype=np.float64))
            xsi_material = _xsi_material(material, texture_file)
            face_materials.extend([xsi_material] * len(triangles))

        corner_pos = np.concatenate(positions_out).reshape(-1, 3)
        corner_nrm = np.concatenate(normals_out).reshape(-1, 3)
        corner_uv = np.concatenate(uvs_out).reshape(-1, 2)
        vertices, position_index = _index_list(_quantize(corner_pos))
        normal_values, normal_index = _index_list(_quantize(corner_nrm))
        uv_values, uv_index = _index_list(_quantize(corner_uv))

        mesh = bz2xsi.Mesh(frame_name)
        mesh.vertices = vertices
        mesh.normal_vertices = normal_values
        mesh.uv_vertices = uv_values
        for face in range(len(position_index) // 3):
            corners = slice(face * 3, face * 3 + 3)
            face_positions = tuple(position_index[corners])
            if len(set(face_positions)) < 3:
                stats["degenerate_triangles_dropped"] += 1
                continue
            mesh.faces.append(face_positions)
            mesh.normal_faces.append(tuple(normal_index[corners]))
            mesh.uv_faces.append(tuple(uv_index[corners]))
            mesh.face_materials.append(face_materials[face])
        if not mesh.faces:
            return None
        stats["meshes"] += 1
        stats["triangles"] += len(mesh.faces)
        stats["baked_residual_frames"] += bool(bake)
        stats["mirrored_frames"] += bool(mirrored)
        return mesh

    def build_frame(index: int, parent_rigid: np.ndarray) -> bz2xsi.Frame:
        node = nodes[index]
        rigid, residual = rigid_split(world[index])
        frame = bz2xsi.Frame(unique_name(str(node.get("name") or f"node{index}")))
        frame.transform = xsi_matrix(np.linalg.inv(parent_rigid) @ rigid)
        frame.mesh = build_mesh(index, residual, frame.name)
        stats["frames"] += 1
        report_nodes.append(
            {
                "gltf_node": index,
                "source_name": node.get("name"),
                "frame": frame.name,
                "dsc_model": (node.get("extras") or {}).get("bz2_dsc_model_name"),
                "residual_scale": [round(float(v), 6) for v in np.linalg.svd(residual, compute_uv=False)],
                "mirrored": bool(np.linalg.det(residual) < 0),
            }
        )
        for child in node.get("children", []):
            if subtree_vertices.get(int(child), 0) > 0:
                child_frame = build_frame(int(child), rigid)
                child_frame.parent = frame
                frame.frames.append(child_frame)
        return frame

    root_frame = build_frame(primary, np.eye(4))
    primary_rigid, _ = rigid_split(world[primary])
    for root in kept_roots:
        if root == primary:
            continue
        frame = build_frame(root, primary_rigid)
        frame.parent = root_frame
        root_frame.frames.append(frame)
    xsi.frames.append(root_frame)

    out_dir.mkdir(parents=True, exist_ok=True)
    xsi_path = out_dir / xsi_name
    xsi.write(str(xsi_path))

    # Round-trip through the reference parser as a structural self-check.
    parsed = bz2xsi.read(str(xsi_path))
    parsed_meshes = list(parsed.get_all_meshes())
    if len(parsed.frames) != 1 or len(parsed_meshes) != stats["meshes"]:
        raise ExportError("written XSI failed round-trip structure check")

    report = {
        "schema": SCHEMA,
        "bundle": str(bundle_dir),
        "scene_dsc": manifest.get("scene_dsc"),
        "xsi": str(xsi_path),
        "root_frame": root_frame.name,
        "attached_secondary_roots": [nodes[r].get("name") for r in kept_roots if r != primary],
        "source_uv_convention": "gltf_top_left_v1_flipped_back" if gltf.uv_is_gltf_space else "softimage_raw",
        **stats,
        "texture_count": len(textures.records),
        "textures": textures.records,
        "missing_texture_files": sorted(set(textures.missing)),
        "uv_decisions": uv_decisions,
        "skipped_non_triangle_primitives": skipped_primitives,
        "omitted_nodes": omitted,
        "frames_detail": report_nodes,
        "notes": [
            "Axes/units are Softimage-native (identical to the reconstructed glTF); no axis conversion is applied.",
            "Frame matrices are rigid; any scale/shear/mirror is baked into that frame's mesh (winding reversed when mirrored).",
            "Each material exports its bound base texture layer only; overlay/bump layers remain in the glTF/Blender reconstruction.",
            "UVs are the effective base-layer UVs in Softimage bottom-left space, matching the Blender asset-fidelity stage decisions.",
        ],
    }
    (out_dir / "xsi_export.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def batch_summary(report: dict) -> dict:
    """Compact per-scene XSI record stored in batch_reconstruction.json."""
    return {
        "status": "ok",
        "path": report["xsi"],
        "frames": report["frames"],
        "meshes": report["meshes"],
        "triangles": report["triangles"],
        "textures": report["texture_count"],
        "missing_texture_files": len(report["missing_texture_files"]),
        "uv_decisions": report["uv_decisions"],
    }


def failure_summary(exc: Exception) -> dict:
    # Camera/light/FX-only shots have no engine model; that is not an export fault.
    status = "skipped_no_geometry" if isinstance(exc, NoGeometryError) else "error"
    return {"status": status, "error": f"{type(exc).__name__}: {exc}"}


def _export_record(bundle: str, include_staging: bool) -> dict:
    try:
        return batch_summary(export_bundle(Path(bundle), include_staging=include_staging))
    except Exception as exc:  # noqa: BLE001 - recorded per scene
        return failure_summary(exc)


def refresh_batch(batch_path: Path, *, jobs: int = 1, include_staging: bool = False) -> dict:
    """Re-export XSI for every successful scene of a batch and update its records."""
    import concurrent.futures

    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    targets = [item for item in batch.get("results", []) if item.get("status") == "ok"]
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(_export_record, item["output_dir"], include_staging): item for item in targets}
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            item = futures[future]
            item["xsi"] = future.result()
            if done % 100 == 0 or done == len(futures):
                print(f"[{done}/{len(futures)}] xsi refreshed", flush=True)
    batch["xsi_success_count"] = sum((item.get("xsi") or {}).get("status") == "ok" for item in batch.get("results", []))
    batch_path.write_text(json.dumps(batch, indent=2), encoding="utf-8")
    return batch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundles", nargs="*", type=Path, help="reconstructed scene bundle directories")
    parser.add_argument("--batch", type=Path, help="refresh every successful scene in a batch_reconstruction.json")
    parser.add_argument("--jobs", type=int, default=1, help="parallel exports for --batch")
    parser.add_argument("--out", type=Path, help="output directory (single bundle only; default <bundle>/engine)")
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--include-staging", action="store_true", help="keep class-1 ROOT ground grids in multi-root scenes")
    args = parser.parse_args()
    if args.batch:
        batch = refresh_batch(args.batch, jobs=args.jobs, include_staging=args.include_staging)
        statuses: dict[str, int] = {}
        for item in batch.get("results", []):
            key = (item.get("xsi") or {}).get("status", "not_reconstructed")
            statuses[key] = statuses.get(key, 0) + 1
        print(json.dumps({"batch": str(args.batch), "xsi_status_counts": statuses}, indent=2))
        return 0 if not statuses.get("error") else 1
    if not args.bundles:
        parser.error("give bundle directories or --batch")
    if args.out and len(args.bundles) != 1:
        parser.error("--out requires exactly one bundle")
    failures = 0
    for bundle in args.bundles:
        try:
            report = export_bundle(bundle, args.out, include_staging=args.include_staging)
            print(json.dumps({k: report[k] for k in ("xsi", "frames", "meshes", "triangles", "texture_count", "uv_decisions")}))
        except Exception as exc:  # noqa: BLE001 - batch reporting
            failures += 1
            print(json.dumps({"bundle": str(bundle), **failure_summary(exc)}))
            if not args.keep_going:
                return 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

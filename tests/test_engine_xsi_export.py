import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "io_scene_bz2xsi"))

import bz2_gltf_uv_convention as uv_convention  # noqa: E402
import bz2_xsi_export as xsi_export  # noqa: E402
import bz2xsi  # noqa: E402

bz2xsi.ALLOW_PRINT = False


class _GltfBuilder:
    def __init__(self):
        self.doc = {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [],
            "meshes": [],
            "materials": [],
            "buffers": [{"uri": "scene.bin"}],
            "bufferViews": [],
            "accessors": [],
        }
        self.blob = bytearray()

    def accessor(self, values, kind, component=5126):
        fmt = "f" if component == 5126 else "I"
        flat = [v for row in values for v in (row if isinstance(row, (list, tuple)) else [row])]
        offset = len(self.blob)
        self.blob += struct.pack(f"<{len(flat)}{fmt}", *flat)
        self.doc["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": len(flat) * 4})
        self.doc["accessors"].append(
            {"bufferView": len(self.doc["bufferViews"]) - 1, "componentType": component, "count": len(values), "type": kind}
        )
        return len(self.doc["accessors"]) - 1

    def mesh(self, positions, uvs, material=None):
        primitive = {
            "attributes": {
                "POSITION": self.accessor(positions, "VEC3"),
                "TEXCOORD_0": self.accessor(uvs, "VEC2"),
            },
            "indices": self.accessor(list(range(len(positions))), "SCALAR", 5125),
        }
        if material is not None:
            primitive["material"] = material
        self.doc["meshes"].append({"primitives": [primitive]})
        return len(self.doc["meshes"]) - 1

    def write(self, directory: Path):
        self.doc["buffers"][0]["byteLength"] = len(self.blob)
        (directory / "scene.bin").write_bytes(bytes(self.blob))
        (directory / "scene.gltf").write_text(json.dumps(self.doc), encoding="utf-8")


def _world(frame):
    matrix = np.array(frame.transform.to_list()).T
    return _world(frame.parent) @ matrix if frame.parent else matrix


class UvConventionTests(unittest.TestCase):
    def test_flip_texture_transform_matches_flipped_softimage_mapping(self):
        transform = {"scale": [2.0, 3.0], "offset": [0.25, 0.1]}
        flipped = uv_convention.flip_texture_transform(transform)
        for u, v in [(0.0, 0.0), (0.3, 0.7), (1.0, 1.0)]:
            soft = (u * 2.0 + 0.25, v * 3.0 + 0.1)
            t = 1.0 - v
            gltf = (u * flipped["scale"][0] + flipped["offset"][0], t * flipped["scale"][1] + flipped["offset"][1])
            self.assertAlmostEqual(gltf[0], soft[0])
            self.assertAlmostEqual(gltf[1], 1.0 - soft[1])

    def test_normalize_flips_once_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            builder = _GltfBuilder()
            mesh = builder.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0.0, 0.0), (1.0, 0.25), (0.5, 1.0)], material=0)
            builder.doc["materials"].append(
                {"pbrMetallicRoughness": {"baseColorTexture": {"index": 0, "extensions": {"KHR_texture_transform": {"scale": [1.0, 0.5], "offset": [0.0, 0.25]}}}}}
            )
            builder.doc["nodes"].append({"mesh": mesh})
            builder.doc["scenes"][0]["nodes"].append(0)
            builder.write(directory)
            gltf_path = directory / "scene.gltf"
            first = uv_convention.normalize(gltf_path)
            self.assertEqual(first["flipped_accessor_count"], 1)
            second = uv_convention.normalize(gltf_path)
            self.assertEqual(second["status"], "already_normalized")
            document = xsi_export.GltfDocument(gltf_path)
            np.testing.assert_allclose(document.accessor(1)[:, 1], [1.0, 0.75, 0.0])
            np.testing.assert_allclose(document.softimage_uv(1), [(0.0, 0.0), (1.0, 0.25), (0.5, 1.0)])
            doc = json.loads(gltf_path.read_text())
            transform = doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["extensions"]["KHR_texture_transform"]
            self.assertAlmostEqual(transform["offset"][1], 0.25)


class XsiExportTests(unittest.TestCase):
    def _bundle(self, directory: Path) -> None:
        from PIL import Image

        (directory / "textures").mkdir()
        Image.new("RGB", (4, 4), (200, 10, 10)).save(directory / "textures" / "t2d1__hull.png")
        builder = _GltfBuilder()
        triangle = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        body = builder.mesh(triangle, [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], material=0)
        wing = builder.mesh(triangle, [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)])
        builder.doc["materials"].append(
            {
                "name": "ship-mat1.1-0",
                "pbrMetallicRoughness": {"baseColorFactor": [0.7, 0.7, 0.7, 1.0], "baseColorTexture": {"index": 0}},
                "extras": {"bz2_softimage_mtr": {"diffuse_rgb": [0.7, 0.7, 0.7], "specular_rgb": [0.3, 0.3, 0.3], "shininess": 50.0, "ambient_rgb": [0, 0, 0], "transparency": 0.0, "xsi_shading_type": 0}},
            }
        )
        builder.doc["nodes"] = [
            {"name": "body", "mesh": body, "children": [1]},
            # Mirrored/scaled child: must become a rigid frame with baked geometry.
            {"name": "wing", "mesh": wing, "matrix": [-2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 0, 0, 1]},
            {"name": "hp_gun_1", "mesh": builder.mesh(triangle, [(0.0, 0.0)] * 3), "translation": [0.0, 3.0, 0.0]},
            {"name": "Camera1", "camera": 0},
            {
                "name": "grid1",
                "mesh": builder.mesh([(-50.0, 0.0, -50.0), (50.0, 0.0, -50.0), (0.0, 0.0, 50.0)] * 20, [(0.0, 0.0)] * 60),
                "extras": {"class_id": 1, "class1_grid_geometry_emitted": True},
            },
        ]
        builder.doc["scenes"][0]["nodes"] = [0, 2, 3, 4]
        builder.write(directory)
        uv_convention.normalize(directory / "scene.gltf")
        layers = {
            "materials": [
                {
                    "gltf_material_index": 0,
                    "material_name": "ship-mat1.1-0",
                    "layers": [
                        {
                            "texture_object": "ship-t2d1.1-0",
                            "uri": "textures/t2d1__hull.png",
                            "resolved_picture": "SHIP/PICTURES/hull.pic",
                            "bound_as_gltf_base_color": True,
                            "projection_or_mapping_code_candidate": 1,
                            "si_texture2d_repeat_uv": [2, 1],
                            "si_texture2d_uv_scale": [1.0, 1.0],
                            "si_texture2d_uv_offset": [0.0, 0.0],
                        }
                    ],
                }
            ]
        }
        (directory / "scene.texture_layers.json").write_text(json.dumps(layers), encoding="utf-8")

    def test_engine_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._bundle(directory)
            report = xsi_export.export_bundle(directory, name="ship.1-0")
            xsi = bz2xsi.read(report["xsi"])
            self.assertEqual(len(xsi.frames), 1)
            root = xsi.frames[0]
            self.assertEqual(root.name, "body")
            self.assertEqual(sorted(frame.name for frame in root.frames), ["hp_gun_1", "wing"])
            self.assertIsNone(xsi.find_frame("Camera1"))
            # The staging ground grid has the most vertices but is not part of the unit.
            self.assertIsNone(xsi.find_frame("grid1"))
            self.assertIn({"node": "grid1", "reason": "staging_ground_grid"}, report["omitted_nodes"])
            for frame in xsi.get_all_frames():
                linear = np.array(frame.transform.to_list()).T[:3, :3]
                np.testing.assert_allclose(linear.T @ linear, np.eye(3), atol=1e-6)
            wing = xsi.find_frame("wing")
            world = [(_world(wing) @ np.r_[vertex, 1.0])[:3] for vertex in (wing.mesh.vertices[i] for i in wing.mesh.faces[0])]
            np.testing.assert_allclose(sorted(map(tuple, np.round(world, 6))), sorted([(5, 0, 0), (3, 0, 0), (5, 1, 0)]), atol=1e-6)
            # An X mirror keeps the +Z facing; winding is reversed so it stays front-facing.
            a, b, c = (np.array(p) for p in world)
            self.assertGreater(np.cross(b - a, c - a)[2], 0.0)
            body_material = root.mesh.face_materials[0]
            self.assertEqual(body_material.texture, "hull.tga")
            self.assertEqual(body_material.shading_type, 0)
            self.assertTrue((directory / "engine" / "hull.tga").is_file())
            # Authored UV + URepeat 2 -> effective U doubled, in Softimage space.
            uvs = sorted(tuple(root.mesh.uv_vertices[i]) for i in root.mesh.uv_faces[0])
            self.assertEqual(uvs, [(0.0, 0.0), (0.0, 1.0), (2.0, 0.0)])
            self.assertEqual(report["mirrored_frames"], 1)

    def test_rigid_split_recovers_world(self):
        world = np.array([[0, -2, 0, 1], [2, 0, 0, 2], [0, 0, -3, 3], [0, 0, 0, 1]], dtype=float)
        rigid, residual = xsi_export.rigid_split(world)
        np.testing.assert_allclose(rigid[:3, :3] @ residual, world[:3, :3], atol=1e-12)
        self.assertAlmostEqual(np.linalg.det(rigid[:3, :3]), 1.0)


if __name__ == "__main__":
    unittest.main()

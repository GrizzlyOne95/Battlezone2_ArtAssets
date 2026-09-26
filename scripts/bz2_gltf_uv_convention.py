#!/usr/bin/env python3
"""Convert reconstructed glTF texture coordinates from Softimage to glTF space.

Softimage (and BZ2 dotXSI) texture space has V increasing upward from the
bottom-left of the picture. glTF defines UV (0, 0) as the top-left of the image.
Every earlier reconstruction stage deliberately stores raw Softimage
``CurrentUV`` values in ``TEXCOORD_n`` so provenance checks (for example the
all-zero projection-dependent sentinel) stay exact. This final stage converts
the portable product once:

* ``TEXCOORD_n``: ``(u, v) -> (u, 1 - v)``;
* ``KHR_texture_transform`` authored in Softimage space as
  ``uv' = scale * uv + offset`` becomes, for glTF-space ``t = 1 - v``,
  ``t' = scale_v * t + (1 - scale_v - offset_v)``.

Validation anchor: the reconstructed ISDF Stasis Truck ``main_body`` matches the
original game ``ISDF_vehicles/PICTURES/ivstas00.xsi`` positions and UVs to
~1e-7, and those UV islands only land on the matching ``ivstas00.pic`` features
when V is measured from the bottom of the upright decoded image.

Blender's glTF importer applies ``v = 1 - v`` again, so Blender-side stages see
true Softimage-space UVs, which is what ``bz2_projection_uv`` assumes. The
sidecar JSON reports remain in Softimage space.
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

SCHEMA = "bz2-gltf-uv-convention-v1"
CONVENTION = "gltf_top_left_v1"
SOURCE_CONVENTION = "softimage_bottom_left"

_TEXTURE_INFO_KEYS = {
    "baseColorTexture",
    "metallicRoughnessTexture",
    "normalTexture",
    "occlusionTexture",
    "emissiveTexture",
    "specularTexture",
    "specularColorTexture",
    "transmissionTexture",
    "clearcoatTexture",
    "clearcoatRoughnessTexture",
    "clearcoatNormalTexture",
    "sheenColorTexture",
    "sheenRoughnessTexture",
    "thicknessTexture",
    "iridescenceTexture",
    "iridescenceThicknessTexture",
    "anisotropyTexture",
    "diffuseTexture",
    "specularGlossinessTexture",
}


def flip_texture_transform(transform: dict) -> dict:
    """Return a KHR_texture_transform re-expressed for glTF top-left UV space."""
    if transform.get("rotation"):
        raise ValueError("rotated KHR_texture_transform cannot be flipped by this stage")
    scale = list(transform.get("scale") or [1.0, 1.0])
    offset = list(transform.get("offset") or [0.0, 0.0])
    converted = dict(transform)
    converted["scale"] = [float(scale[0]), float(scale[1])]
    converted["offset"] = [float(offset[0]), 1.0 - float(scale[1]) - float(offset[1])]
    return converted


def _iter_texture_infos(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _TEXTURE_INFO_KEYS and isinstance(value, dict) and "index" in value:
                yield value
            yield from _iter_texture_infos(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_texture_infos(item)


def _flip_accessor(gltf: dict, buffers: dict[int, bytearray], accessor_index: int) -> int:
    accessor = gltf["accessors"][accessor_index]
    if accessor.get("type") != "VEC2" or accessor.get("componentType") != 5126:
        raise ValueError(
            f"accessor {accessor_index} is not float VEC2 texture coordinates: "
            f"{accessor.get('type')}/{accessor.get('componentType')}"
        )
    if accessor.get("sparse"):
        raise ValueError(f"sparse texture-coordinate accessor {accessor_index} is not supported")
    view = gltf["bufferViews"][accessor["bufferView"]]
    data = buffers[int(view["buffer"])]
    stride = int(view.get("byteStride") or 8)
    base = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    count = int(accessor["count"])
    v_min, v_max = float("inf"), float("-inf")
    for index in range(count):
        position = base + index * stride + 4
        (v,) = struct.unpack_from("<f", data, position)
        flipped = 1.0 - v
        struct.pack_into("<f", data, position, flipped)
        (stored,) = struct.unpack_from("<f", data, position)
        v_min, v_max = min(v_min, stored), max(v_max, stored)
    if count and "min" in accessor and "max" in accessor:
        accessor["min"] = [accessor["min"][0], v_min]
        accessor["max"] = [accessor["max"][0], v_max]
    return count


def normalize(input_gltf: Path, output_gltf: Path | None = None) -> dict:
    """Flip every TEXCOORD accessor and texture transform once, in place by default."""
    output_gltf = output_gltf or input_gltf
    gltf = json.loads(input_gltf.read_text(encoding="utf-8"))
    asset_extras = gltf.setdefault("asset", {}).setdefault("extras", {})
    if asset_extras.get("bz2_uv_convention") == CONVENTION:
        return {
            "schema": SCHEMA,
            "status": "already_normalized",
            "flipped_accessor_count": 0,
            "flipped_texture_transform_count": 0,
        }

    buffer_paths: dict[int, Path] = {}
    buffers: dict[int, bytearray] = {}
    for index, buffer in enumerate(gltf.get("buffers", [])):
        uri = buffer.get("uri")
        if not uri or uri.startswith("data:"):
            raise ValueError("only external .bin buffers are supported")
        path = input_gltf.parent / uri
        buffer_paths[index] = path
        buffers[index] = bytearray(path.read_bytes())

    accessor_ids: set[int] = set()
    for mesh in gltf.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            for name, accessor_index in (primitive.get("attributes") or {}).items():
                if name.startswith("TEXCOORD_"):
                    accessor_ids.add(int(accessor_index))
            for target in primitive.get("targets") or []:
                for name in target:
                    if name.startswith("TEXCOORD_"):
                        raise ValueError("morph-target texture coordinates are not supported")

    seen_regions: set[tuple[int, int]] = set()
    flipped_values = 0
    for accessor_index in sorted(accessor_ids):
        accessor = gltf["accessors"][accessor_index]
        region = (int(accessor["bufferView"]), int(accessor.get("byteOffset", 0)))
        if region in seen_regions:
            continue
        seen_regions.add(region)
        flipped_values += _flip_accessor(gltf, buffers, accessor_index)

    transforms = 0
    for info in _iter_texture_infos(gltf.get("materials", [])):
        extension = (info.get("extensions") or {}).get("KHR_texture_transform")
        if extension is None:
            continue
        info["extensions"]["KHR_texture_transform"] = flip_texture_transform(extension)
        transforms += 1

    asset_extras["bz2_uv_convention"] = CONVENTION
    asset_extras["bz2_uv_source_convention"] = SOURCE_CONVENTION
    asset_extras["bz2_uv_convention_note"] = (
        "TEXCOORD_n and KHR_texture_transform are glTF top-left space; "
        "Softimage/dotXSI CurrentUV is (u, 1 - v). Sidecar reports stay in Softimage space."
    )

    for index, data in buffers.items():
        target = output_gltf.parent / Path(gltf["buffers"][index]["uri"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(data))
    output_gltf.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    return {
        "schema": SCHEMA,
        "status": "normalized",
        "flipped_accessor_count": len(seen_regions),
        "flipped_uv_count": flipped_values,
        "flipped_texture_transform_count": transforms,
        "convention": CONVENTION,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("gltf", type=Path)
    args = parser.parse_args()
    print(json.dumps(normalize(args.gltf), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

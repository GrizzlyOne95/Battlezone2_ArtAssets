#!/usr/bin/env python3
"""Refine recovered BZ2 glTF materials using the corrected Softimage MTR layout.

The binary MTR scalar block begins eight bytes after the material-name NUL and
contains 13 big-endian float32 values:

    ambient RGB
    diffuse RGB
    specular RGB
    shininess
    transparency
    reflectivity
    refractive index

This alignment is corpus-validated against 14,773 direct/archive material
records. The script deliberately keeps Softimage reflectivity as source
metadata instead of conflating it with glTF metallic. Defensible mappings are
made through KHR_materials_specular, KHR_materials_transmission and (for
non-default glass-like values) KHR_materials_ior.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import zipfile
from pathlib import Path


class SourceStore:
    def read(self, logical: str) -> bytes:
        raise NotImplementedError


class DirectoryStore(SourceStore):
    def __init__(self, root: Path):
        self.root = root.resolve()

    def read(self, logical: str) -> bytes:
        return (self.root / Path(*logical.replace("\\", "/").split("/"))).read_bytes()


class ZipStore(SourceStore):
    def __init__(self, path: Path):
        self.archive = zipfile.ZipFile(path, "r")
        self.by_lower = {
            info.filename.replace("\\", "/").lower(): info.filename
            for info in self.archive.infolist()
            if not info.is_dir()
        }

    def read(self, logical: str) -> bytes:
        actual = self.by_lower.get(logical.replace("\\", "/").lower())
        if actual is None:
            raise FileNotFoundError(logical)
        return self.archive.read(actual)


def open_store(path: Path) -> SourceStore:
    if path.is_dir():
        return DirectoryStore(path)
    if zipfile.is_zipfile(path):
        return ZipStore(path)
    raise ValueError(f"asset source is neither a directory nor ZIP archive: {path}")


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def decode_mtr(data: bytes) -> dict:
    marker = data.find(b"MTRL")
    if marker < 0:
        raise ValueError("MTRL marker not found")
    name_end = data.find(b"\0", marker + 4)
    if name_end < 0:
        raise ValueError("MTRL name terminator not found")
    # NUL + eight-byte material-record prefix, then 13 BE float32 values.
    start = name_end + 9
    if start + 52 > len(data):
        raise ValueError("short MTR scalar block")
    values = list(struct.unpack_from(">13f", data, start))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("non-finite MTR scalar")
    name = data[marker + 4 : name_end].decode("latin-1", errors="replace")
    return {
        "material_name": name,
        "scalar_offset": start,
        "ambient_rgb": values[0:3],
        "diffuse_rgb": values[3:6],
        "specular_rgb": values[6:9],
        "shininess": values[9],
        "transparency": values[10],
        "reflectivity": values[11],
        "refractive_index": values[12],
        "raw_13f": values,
    }


def phong_to_roughness(shininess: float) -> float:
    return clamp01(math.sqrt(2.0 / (max(0.0, shininess) + 2.0)))


def specular_extension(rgb: list[float]) -> dict | None:
    peak = max(0.0, max(rgb, default=0.0))
    if peak <= 1.0e-8:
        return None
    factor = min(1.0, peak)
    divisor = peak if peak > 1.0 else max(peak, 1.0e-8)
    color = [clamp01(value / divisor) for value in rgb]
    if peak <= 1.0:
        color = [clamp01(value / peak) for value in rgb]
    return {"specularFactor": factor, "specularColorFactor": color}


def refine_material(material: dict, decoded: dict) -> list[str]:
    extensions_used: list[str] = []
    pbr = material.setdefault("pbrMetallicRoughness", {})
    diffuse = [clamp01(value) for value in decoded["diffuse_rgb"]]
    old_factor = list(pbr.get("baseColorFactor") or [1.0, 1.0, 1.0, 1.0])
    alpha_factor = float(old_factor[3]) if len(old_factor) > 3 else 1.0
    pbr["baseColorFactor"] = [*diffuse, alpha_factor]
    pbr["roughnessFactor"] = phong_to_roughness(decoded["shininess"])
    pbr.setdefault("metallicFactor", 0.0)

    extensions = material.setdefault("extensions", {})
    specular = specular_extension(decoded["specular_rgb"])
    if specular:
        extensions["KHR_materials_specular"] = specular
        extensions_used.append("KHR_materials_specular")

    transparency = clamp01(decoded["transparency"])
    if transparency > 1.0e-6:
        extensions["KHR_materials_transmission"] = {"transmissionFactor": transparency}
        extensions_used.append("KHR_materials_transmission")

    ior = float(decoded["refractive_index"])
    if ior >= 1.0 and abs(ior - 1.0) > 1.0e-5:
        extensions["KHR_materials_ior"] = {"ior": ior}
        extensions_used.append("KHR_materials_ior")

    material.setdefault("extras", {})["bz2_softimage_mtr"] = {
        "ambient_rgb": decoded["ambient_rgb"],
        "diffuse_rgb": decoded["diffuse_rgb"],
        "specular_rgb": decoded["specular_rgb"],
        "shininess": decoded["shininess"],
        "transparency": decoded["transparency"],
        "reflectivity": decoded["reflectivity"],
        "refractive_index": decoded["refractive_index"],
        "mapping_notes": {
            "ambient": "preserved only; PBR has no direct Softimage ambient-color equivalent",
            "reflectivity": "preserved only; not equivalent to metallic",
            "transparency": "mapped provisionally to KHR_materials_transmission",
            "shininess": "mapped to roughness with sqrt(2/(n+2))",
        },
    }
    if not extensions:
        material.pop("extensions", None)
    return extensions_used


def refine_gltf(input_gltf: Path, asset_source: Path, output_gltf: Path) -> dict:
    if output_gltf.parent.resolve() != input_gltf.parent.resolve():
        raise ValueError("output_gltf must be in the same directory as input_gltf so buffer/texture URIs remain valid")
    gltf = json.loads(input_gltf.read_text(encoding="utf-8"))
    store = open_store(asset_source)
    refined = []
    failures = []
    extension_names: set[str] = set(gltf.get("extensionsUsed") or [])

    for index, material in enumerate(gltf.get("materials") or []):
        source_mtr = str((material.get("extras") or {}).get("source_mtr") or "")
        if not source_mtr:
            continue
        try:
            decoded = decode_mtr(store.read(source_mtr))
            used = refine_material(material, decoded)
            extension_names.update(used)
            refined.append({
                "material_index": index,
                "material_name": material.get("name"),
                "source_mtr": source_mtr,
                "ambient_rgb": decoded["ambient_rgb"],
                "diffuse_rgb": decoded["diffuse_rgb"],
                "specular_rgb": decoded["specular_rgb"],
                "shininess": decoded["shininess"],
                "transparency": decoded["transparency"],
                "reflectivity": decoded["reflectivity"],
                "refractive_index": decoded["refractive_index"],
                "extensions": used,
            })
        except Exception as exc:
            failures.append({"material_index": index, "material_name": material.get("name"), "source_mtr": source_mtr, "error": f"{type(exc).__name__}: {exc}"})

    if extension_names:
        gltf["extensionsUsed"] = sorted(extension_names)
    output_gltf.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    summary = {
        "schema": "bz2-mtr-gltf-refinement-v1",
        "input_gltf": str(input_gltf),
        "asset_source": str(asset_source),
        "output_gltf": str(output_gltf),
        "material_count": len(gltf.get("materials") or []),
        "refined_count": len(refined),
        "failure_count": len(failures),
        "transmission_material_count": sum(item["transparency"] > 1.0e-6 for item in refined),
        "reflective_source_material_count": sum(item["reflectivity"] > 1.0e-6 for item in refined),
        "nondefault_ior_material_count": sum(item["refractive_index"] >= 1.0 and abs(item["refractive_index"] - 1.0) > 1.0e-5 for item in refined),
        "refined": refined,
        "failures": failures,
        "notes": [
            "MTR scalar block is read from name_end+9; the previous provisional decoder began four bytes late",
            "source reflectivity remains metadata rather than being mislabeled as metallic",
            "source transparency is provisionally represented by KHR_materials_transmission",
            "ambient color remains metadata because glTF PBR has no direct equivalent",
        ],
    }
    output_gltf.with_suffix(".mtr.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_gltf", type=Path)
    parser.add_argument("asset_source", type=Path)
    parser.add_argument("output_gltf", type=Path)
    args = parser.parse_args()
    summary = refine_gltf(args.input_gltf, args.asset_source, args.output_gltf)
    print(json.dumps(summary, indent=2))
    return 1 if summary["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

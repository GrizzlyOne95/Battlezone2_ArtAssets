#!/usr/bin/env python3
"""Inventory mesh/object topology from Blender files without Blender or bpy.

This intentionally reads only enough of Blender's embedded SDNA schema to expose
mesh topology counts and object-to-datablock links. It is used for geometry-only
regression comparisons against manually converted BZ2 reference files; it does
not interpret materials, evaluated modifiers, or Blender scene semantics.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path


class BlendReader:
    def __init__(self, path: Path):
        self.path = path
        self.handle = path.open("rb")
        header = self.handle.read(12)
        if header[:7] != b"BLENDER":
            raise ValueError(f"not an uncompressed Blender file: {path}")
        self.pointer_size = 8 if header[7:8] == b"-" else 4
        self.endian = "<" if header[8:9] == b"v" else ">"
        self.version = int(header[9:12])
        block_struct = struct.Struct(
            self.endian + ("4sIQII" if self.pointer_size == 8 else "4sIIII")
        )

        self.blocks = []
        while True:
            raw = self.handle.read(block_struct.size)
            if len(raw) != block_struct.size:
                break
            code, size, address, sdna_index, count = block_struct.unpack(raw)
            code = code.split(b"\0", 1)[0].decode("latin-1")
            offset = self.handle.tell()
            self.blocks.append(
                {
                    "code": code,
                    "size": size,
                    "address": address,
                    "sdna_index": sdna_index,
                    "count": count,
                    "offset": offset,
                }
            )
            if code == "ENDB":
                break
            self.handle.seek(size, 1)

        dna = next(block for block in self.blocks if block["code"] == "DNA1")
        self.handle.seek(dna["offset"])
        self._parse_dna(self.handle.read(dna["size"]))
        self.block_by_address = {
            block["address"]: block for block in self.blocks if block["address"]
        }

    def close(self) -> None:
        self.handle.close()

    def _u32(self, data: bytes, offset: int) -> tuple[int, int]:
        return struct.unpack_from(self.endian + "I", data, offset)[0], offset + 4

    def _u16(self, data: bytes, offset: int) -> tuple[int, int]:
        return struct.unpack_from(self.endian + "H", data, offset)[0], offset + 2

    @staticmethod
    def _align4(offset: int) -> int:
        return (offset + 3) & ~3

    @staticmethod
    def _strings(data: bytes, offset: int, count: int) -> tuple[list[str], int]:
        output = []
        for _ in range(count):
            end = data.index(0, offset)
            output.append(data[offset:end].decode("latin-1"))
            offset = end + 1
        return output, offset

    def _parse_dna(self, data: bytes) -> None:
        offset = 0
        if data[offset:offset + 4] != b"SDNA":
            raise ValueError("invalid Blender SDNA block")
        offset += 4
        if data[offset:offset + 4] != b"NAME":
            raise ValueError("missing Blender SDNA NAME section")
        offset += 4
        count, offset = self._u32(data, offset)
        self.names, offset = self._strings(data, offset, count)
        offset = self._align4(offset)

        if data[offset:offset + 4] != b"TYPE":
            raise ValueError("missing Blender SDNA TYPE section")
        offset += 4
        count, offset = self._u32(data, offset)
        self.types, offset = self._strings(data, offset, count)
        offset = self._align4(offset)

        if data[offset:offset + 4] != b"TLEN":
            raise ValueError("missing Blender SDNA TLEN section")
        offset += 4
        self.type_lengths = []
        for _ in range(count):
            value, offset = self._u16(data, offset)
            self.type_lengths.append(value)
        offset = self._align4(offset)

        if data[offset:offset + 4] != b"STRC":
            raise ValueError("missing Blender SDNA STRC section")
        offset += 4
        struct_count, offset = self._u32(data, offset)
        self.structs = []
        for _ in range(struct_count):
            type_index, offset = self._u16(data, offset)
            field_count, offset = self._u16(data, offset)
            fields = []
            for _ in range(field_count):
                field_type, offset = self._u16(data, offset)
                field_name, offset = self._u16(data, offset)
                fields.append((field_type, field_name))
            self.structs.append(
                {
                    "type": self.types[type_index],
                    "type_index": type_index,
                    "fields": fields,
                }
            )
        self.struct_by_type = {item["type"]: item for item in self.structs}

    @staticmethod
    def _array_size(name: str) -> int:
        size = 1
        for value in re.findall(r"\[(\d+)\]", name):
            size *= int(value)
        return size

    @staticmethod
    def _short_name(name: str) -> str:
        return re.sub(r"\[.*", "", name).replace("*", "").replace("(", "").replace(")", "")

    def _field_size(self, type_index: int, name_index: int) -> int:
        name = self.names[name_index]
        unit = self.pointer_size if "*" in name else self.type_lengths[type_index]
        return unit * self._array_size(name)

    def _field(self, struct_type: str, path: str):
        base = 0
        current_type = struct_type
        parts = path.split(".")
        for part_index, part in enumerate(parts):
            structure = self.struct_by_type.get(current_type)
            if not structure:
                return None
            relative = 0
            found = None
            for type_index, name_index in structure["fields"]:
                if self._short_name(self.names[name_index]) == part:
                    found = type_index, name_index, relative
                    break
                relative += self._field_size(type_index, name_index)
            if found is None:
                return None
            type_index, name_index, relative = found
            base += relative
            if part_index + 1 < len(parts):
                current_type = self.types[type_index]
            else:
                return base, type_index, name_index
        return None

    def _data(self, block: dict) -> bytes:
        self.handle.seek(block["offset"])
        return self.handle.read(block["size"])

    def scalar(self, block: dict, path: str, default=None):
        structure = self.structs[block["sdna_index"]]
        info = self._field(structure["type"], path)
        if info is None:
            return default
        offset, type_index, name_index = info
        name = self.names[name_index]
        type_name = self.types[type_index]
        data = self._data(block)

        if "*" in name:
            fmt = "Q" if self.pointer_size == 8 else "I"
        elif type_name in {"int", "int32_t"}:
            fmt = "i"
        elif type_name in {"uint", "uint32_t", "unsigned int"}:
            fmt = "I"
        elif type_name == "short":
            fmt = "h"
        elif type_name == "ushort":
            fmt = "H"
        elif type_name == "float":
            fmt = "f"
        elif type_name == "char":
            size = self._array_size(name)
            return data[offset:offset + size].split(b"\0", 1)[0].decode("latin-1", errors="replace")
        else:
            return default
        return struct.unpack_from(self.endian + fmt, data, offset)[0]

    def id_name(self, block: dict | None) -> str | None:
        if not block:
            return None
        name = self.scalar(block, "id.name")
        return name[2:] if name and len(name) >= 2 else name

    def inventory(self) -> dict:
        meshes = []
        objects = []
        for block in self.blocks:
            if block["sdna_index"] >= len(self.structs):
                continue
            block_type = self.structs[block["sdna_index"]]["type"]
            if block_type == "Mesh":
                counts = {}
                for field in (
                    "totvert", "totedge", "totloop", "totpoly",
                    "verts_num", "edges_num", "corners_num", "faces_num",
                ):
                    value = self.scalar(block, field)
                    if value is not None:
                        counts[field] = value
                meshes.append(
                    {
                        "name": self.id_name(block),
                        "address": block["address"],
                        "counts": counts,
                    }
                )
            elif block_type == "Object":
                name = self.id_name(block)
                if not name:
                    continue
                data_address = self.scalar(block, "data")
                parent_address = self.scalar(block, "parent")
                target = self.block_by_address.get(data_address)
                objects.append(
                    {
                        "name": name,
                        "data_address": data_address,
                        "data_type": (
                            self.structs[target["sdna_index"]]["type"]
                            if target and target["sdna_index"] < len(self.structs)
                            else None
                        ),
                        "data_name": self.id_name(target),
                        "parent_address": parent_address,
                    }
                )

        def count_value(item: dict, old: str, new: str) -> int:
            return int(item["counts"].get(old, item["counts"].get(new, 0)) or 0)

        return {
            "source": str(self.path),
            "blender_version": self.version,
            "mesh_count": len(meshes),
            "object_count": len(objects),
            "vertex_count": sum(count_value(item, "totvert", "verts_num") for item in meshes),
            "polygon_count": sum(count_value(item, "totpoly", "faces_num") for item in meshes),
            "corner_count": sum(count_value(item, "totloop", "corners_num") for item in meshes),
            "meshes": meshes,
            "objects": objects,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("blend_files", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = []
    for path in args.blend_files:
        reader = BlendReader(path)
        try:
            payload.append(reader.inventory())
        finally:
            reader.close()
    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Decode Softimage|3D PIC images to portable PNG using only Python stdlib.

Supports the PIC packet layouts observed in the Battlezone II art archive:
uncompressed, pure RLE, and mixed RLE; RGB and RGBA channel packets.
"""
from __future__ import annotations

import argparse
import binascii
import json
import struct
import zlib
from pathlib import Path

MAGIC = b"\x53\x80\xF6\x34"
PICT = b"PICT"
CHANNEL_MASKS = (0x80, 0x40, 0x20, 0x10)  # R, G, B, A


class PicError(ValueError):
    pass


def _read_be_u16(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 2 > len(data):
        raise PicError("truncated u16")
    return struct.unpack_from(">H", data, offset)[0], offset + 2


def _read_u8(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise PicError("truncated byte")
    return data[offset], offset + 1


def inspect_pic_bytes(data: bytes) -> dict:
    if len(data) == 0:
        return {"kind": "empty_placeholder", "size": 0}
    if data.startswith(b"ray") and len(data) <= 512:
        line = data.split(b"\n", 1)[0].decode("latin-1", errors="replace")
        fields = line.split(",")
        payload = {"kind": "softimage_ray_reference", "size": len(data), "raw": line}
        if len(fields) >= 7:
            payload.update(
                {
                    "ray_version": fields[0],
                    "width": int(fields[1]) if fields[1].isdigit() else None,
                    "height": int(fields[2]) if fields[2].isdigit() else None,
                    "renderer_code": fields[3],
                }
            )
        return payload
    if len(data) < 104 or data[:4] != MAGIC or data[88:92] != PICT:
        return {"kind": "unknown", "size": len(data), "magic_hex": data[:16].hex()}

    width, height = struct.unpack_from(">HH", data, 92)
    ratio_bits = struct.unpack_from(">I", data, 96)[0]
    ratio = struct.unpack(">f", struct.pack(">I", ratio_bits))[0]
    fields, padding = struct.unpack_from(">HH", data, 100)
    offset = 104
    packets = []
    channels = 0
    for _ in range(10):
        if offset + 4 > len(data):
            raise PicError("truncated packet header")
        chained, size, packet_type, channel = data[offset : offset + 4]
        offset += 4
        packets.append(
            {
                "chained": bool(chained),
                "size": size,
                "type": packet_type,
                "channel": channel,
            }
        )
        channels |= channel
        if not chained:
            break
    else:
        raise PicError("too many channel packets")

    return {
        "kind": "softimage_pic",
        "size": len(data),
        "width": width,
        "height": height,
        "ratio": ratio,
        "fields": fields,
        "padding": padding,
        "components": 4 if channels & 0x10 else 3,
        "packet_count": len(packets),
        "packets": packets,
        "pixel_data_offset": offset,
    }


def decode_pic_bytes(data: bytes) -> tuple[bytes, dict]:
    info = inspect_pic_bytes(data)
    if info.get("kind") != "softimage_pic":
        raise PicError(f"not decodable Softimage PIC: {info.get('kind')}")
    width, height = int(info["width"]), int(info["height"])
    if width <= 0 or height <= 0 or width > 32768 or height > 32768:
        raise PicError(f"implausible dimensions {width}x{height}")
    packets = info["packets"]
    for packet in packets:
        if packet["size"] != 8:
            raise PicError(f"unsupported packet bit depth {packet['size']}")
        if packet["type"] not in (0, 1, 2):
            raise PicError(f"unsupported compression type {packet['type']}")

    offset = int(info["pixel_data_offset"])
    output = bytearray([255]) * (width * height * 4)

    def read_value(channel: int) -> list[int | None]:
        nonlocal offset
        values: list[int | None] = [None, None, None, None]
        for index, mask in enumerate(CHANNEL_MASKS):
            if channel & mask:
                value, offset = _read_u8(data, offset)
                values[index] = value
        return values

    def write_value(pixel_offset: int, values: list[int | None], channel: int) -> None:
        for index, mask in enumerate(CHANNEL_MASKS):
            if channel & mask:
                value = values[index]
                if value is None:
                    raise PicError("channel value missing")
                output[pixel_offset + index] = value

    for y in range(height):
        for packet in packets:
            packet_type = int(packet["type"])
            channel = int(packet["channel"])
            x = 0
            if packet_type == 0:
                while x < width:
                    value = read_value(channel)
                    write_value((y * width + x) * 4, value, channel)
                    x += 1
                continue

            if packet_type == 1:
                while x < width:
                    count, offset = _read_u8(data, offset)
                    count = min(count, width - x)
                    value = read_value(channel)
                    for _ in range(count):
                        write_value((y * width + x) * 4, value, channel)
                        x += 1
                continue

            while x < width:  # mixed RLE
                count, offset = _read_u8(data, offset)
                if count >= 128:
                    if count == 128:
                        count, offset = _read_be_u16(data, offset)
                    else:
                        count -= 127
                    if count > width - x:
                        raise PicError("mixed-RLE scanline overrun")
                    value = read_value(channel)
                    for _ in range(count):
                        write_value((y * width + x) * 4, value, channel)
                        x += 1
                else:
                    count += 1
                    if count > width - x:
                        raise PicError("mixed-RLE raw scanline overrun")
                    for _ in range(count):
                        value = read_value(channel)
                        write_value((y * width + x) * 4, value, channel)
                        x += 1

    info = dict(info)
    info["decoded_bytes"] = len(output)
    info["source_bytes_consumed"] = offset
    info["trailing_bytes"] = len(data) - offset
    return bytes(output), info


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    )


def write_rgba_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    expected = width * height * 4
    if len(rgba) != expected:
        raise PicError(f"RGBA buffer has {len(rgba)} bytes, expected {expected}")
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # PNG filter: None
        raw.extend(rgba[y * stride : (y + 1) * stride])
    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
    png.extend(_png_chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
    png.extend(_png_chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def convert_file(source: Path, destination: Path) -> dict:
    rgba, info = decode_pic_bytes(source.read_bytes())
    write_rgba_png(destination, int(info["width"]), int(info["height"]), rgba)
    result = dict(info)
    result["source"] = str(source)
    result["output"] = str(destination)
    return result


def convert_tree(source_root: Path, output_root: Path) -> dict:
    entries, failures, references, placeholders = [], [], [], []
    paths = sorted(
        {path for pattern in ("*.pic", "*.PIC") for path in source_root.rglob(pattern) if path.is_file()}
    )
    for source in paths:
        relative = source.relative_to(source_root)
        destination = output_root / relative.with_suffix(".png")
        data = source.read_bytes()
        info = inspect_pic_bytes(data)
        if info["kind"] == "softimage_ray_reference":
            references.append({"path": relative.as_posix(), **info})
            continue
        if info["kind"] == "empty_placeholder":
            placeholders.append({"path": relative.as_posix(), **info})
            continue
        try:
            result = convert_file(source, destination)
        except Exception as exc:
            failures.append({"path": relative.as_posix(), "error": f"{type(exc).__name__}: {exc}"})
            continue
        entries.append(
            {
                "path": relative.as_posix(),
                "output": destination.relative_to(output_root).as_posix(),
                "width": result["width"],
                "height": result["height"],
                "components": result["components"],
                "packet_count": result["packet_count"],
            }
        )
    return {
        "schema": "softimage-pic-conversion-v1",
        "source_root": str(source_root),
        "output_root": str(output_root),
        "source_pic_count": len(paths),
        "converted_count": len(entries),
        "reference_count": len(references),
        "placeholder_count": len(placeholders),
        "failure_count": len(failures),
        "entries": entries,
        "ray_references": references,
        "placeholders": placeholders,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--tree", action="store_true", help="recursively convert a source directory, preserving relative paths")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--info", action="store_true", help="inspect one PIC without decoding pixels")
    args = parser.parse_args()

    if args.info:
        payload = inspect_pic_bytes(args.source.read_bytes())
        print(json.dumps(payload, indent=2))
        return 0 if payload["kind"] != "unknown" else 1

    if args.tree:
        if args.destination is None:
            raise SystemExit("--tree requires destination directory")
        payload = convert_tree(args.source, args.destination)
    else:
        if args.destination is None:
            raise SystemExit("single-file conversion requires destination .png")
        payload = convert_file(args.source, args.destination)

    text = json.dumps(payload, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    if isinstance(payload, dict) and payload.get("failure_count"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

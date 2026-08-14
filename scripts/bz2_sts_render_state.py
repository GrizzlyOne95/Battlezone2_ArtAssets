#!/usr/bin/env python3
"""Extract authoritative Softimage SETUP_SOFT render state for Blender reconstruction.

The parser intentionally preserves Mental Ray-era settings as source metadata.
Only renderer-independent facts (output resolution/frame, camera/lens shader
names, fog enable state, ambient scene value, reflection/refraction/shadow
switches) are promoted semantically. Mental Ray sampling/ray settings remain
named source fields instead of being guessed into Cycles/Eevee equivalents.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _bool(value: str):
    upper = value.upper()
    if upper in {"YES", "ACTIVE"}:
        return True
    if upper in {"NO", "INACTIVE"}:
        return False
    return value


def _atom(value: str):
    value = value.strip().strip("'")
    try:
        if re.fullmatch(r"[-+]?\d+", value):
            return int(value)
        return float(value)
    except ValueError:
        return _bool(value)


def _values(raw: str):
    parts = re.findall(r"'[^']*'|\S+", raw.strip())
    decoded = [_atom(part) for part in parts]
    return decoded[0] if len(decoded) == 1 else decoded


def parse_sts(path: Path) -> dict:
    text = path.read_text(encoding="latin-1", errors="replace")
    lines = text.splitlines()

    def first(pattern: str):
        rx = re.compile(pattern, re.IGNORECASE)
        for line in lines:
            match = rx.match(line.strip())
            if match:
                return _values(match.group(1))
        return None

    rendering_type = first(r"RENDERING_TYPE\s+(.+)$")
    rendering_frame = first(r"RENDERING_FRAME\s+(.+)$")
    output_file = first(r"OUTPUT_FILE\s+(.+)$")
    preference_picture_format = first(r"PREFERENCE_PICT_FMT\s+(.+)$")
    explicit_resolution = first(r"RESOLUTION\s+(.+)$")
    ambience = first(r"AMBIENCE\s+(.+)$")
    fog_active = first(r"FOG\s+(.+)$")
    fog_start = first(r"FOG_START\s+(.+)$")
    fog_end = first(r"FOG_END\s+(.+)$")
    fog_colour = first(r"FOG_COLOUR\s+(.+)$")
    fog_density = first(r"FOG_DENSITY\s+(.+)$")
    glg_reflect = first(r"GLG_REFLECT\s+(.+)$")
    glg_refract = first(r"GLG_REFRACT\s+(.+)$")
    glg_shadow = first(r"GLG_SHADOW\s+(.+)$")

    picture_resolution = None
    if isinstance(explicit_resolution, list) and len(explicit_resolution) >= 2:
        width, height = explicit_resolution[:2]
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            picture_resolution = [int(width), int(height)]
    if picture_resolution is None and isinstance(preference_picture_format, list) and len(preference_picture_format) >= 4:
        width, height = preference_picture_format[-2:]
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            if int(width) >= 16 and int(height) >= 16:
                picture_resolution = [int(width), int(height)]

    mental_ray: dict[str, object] = {}
    source_switches: dict[str, object] = {}
    for line in lines:
        stripped = line.strip()
        match = re.match(r"(MR_[A-Z0-9_]+)\s+(.+)$", stripped)
        if match:
            key = match.group(1)
            value = _values(match.group(2))
            if key in mental_ray:
                old = mental_ray[key]
                mental_ray[key] = old + [value] if isinstance(old, list) else [old, value]
            else:
                mental_ray[key] = value
            continue
        match = re.match(r"((?:GLG|PRW)_[A-Z0-9_]+)\s+(.+)$", stripped)
        if match:
            source_switches[match.group(1)] = _values(match.group(2))

    lens_shaders = [
        {"order": index, "name": match.group(1)}
        for index, match in enumerate(re.finditer(r"\bUDF_NAME\s+'([^']+)'", text))
    ]

    return {
        "schema": "bz2-softimage-sts-render-state-v1",
        "source_sts": str(path),
        "rendering_type": rendering_type,
        "rendering_frame": rendering_frame,
        "output_file": output_file,
        "picture_format_raw": preference_picture_format,
        "resolution": picture_resolution,
        "ambience_rgb": ambience,
        "fog": {
            "active": fog_active,
            "start": fog_start,
            "end": fog_end,
            "colour_rgb": fog_colour,
            "density": fog_density,
        },
        "global_render_switches": {
            "reflection": glg_reflect,
            "refraction": glg_refract,
            "shadow": glg_shadow,
        },
        "mental_ray": mental_ray,
        "preview_switches": source_switches,
        "lens_shaders": lens_shaders,
        "notes": [
            "Mental Ray settings are preserved under their original names and are not directly mapped to Blender renderer knobs.",
            "Softimage AMBIENCE is source scene-light metadata; it is not automatically treated as a Blender World background color.",
            "Referenced lens shaders are preserved by name; their .shd semantics require separate reconstruction.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sts", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = parse_sts(args.sts)
    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Import a recovered BZ2 glTF into Blender and apply recovered render metadata.

Run inside Blender, for example:

    blender --background --python scripts/blender_reconstruct_scene.py -- \
        recovered_scene.gltf recovered_scene.scene.json recovered_scene.blend

The glTF already carries recovered hierarchy, cameras, KHR punctual lights,
materials and source textures.  This helper makes the result Blender-native,
selects the recovered source camera, applies the Softimage SETUP_SOFT output
resolution/frame range, preserves provenance as custom properties, and saves a
`.blend` file.  It deliberately does not claim to reproduce Softimage shading
or force a Blender color-management/render-engine choice.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import bpy  # type: ignore
except ImportError:  # pragma: no cover - only available inside Blender
    bpy = None


def _argv() -> list[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


def _find_object(name: str):
    obj = bpy.data.objects.get(name)
    if obj is not None:
        return obj
    # Blender may suffix duplicate imported names. Prefer an exact source-name prefix.
    matches = [candidate for candidate in bpy.data.objects if candidate.name.startswith(name + ".")]
    return matches[0] if matches else None


def reconstruct(gltf_path: Path, sidecar_path: Path, output_blend: Path) -> dict:
    if bpy is None:
        raise RuntimeError("this script must be executed by Blender Python")
    metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))

    # Start from an empty scene so imported source names/hierarchy are deterministic.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(gltf_path.resolve()))
    scene = bpy.context.scene

    recovered_cameras = metadata.get("cameras") or []
    active_camera = None
    if recovered_cameras:
        active_camera = _find_object(str(recovered_cameras[0].get("name") or ""))
        if active_camera and getattr(active_camera, "type", None) == "CAMERA":
            scene.camera = active_camera

    setup = metadata.get("setup_soft") or {}
    resolution = setup.get("resolution")
    if resolution and len(resolution) == 2:
        scene.render.resolution_x = int(resolution[0])
        scene.render.resolution_y = int(resolution[1])
        scene.render.resolution_percentage = 100

    frame_spec = setup.get("rendering_frame")
    if frame_spec and len(frame_spec) == 3:
        start, end, step = [int(value) for value in frame_spec]
        scene.frame_start = start
        scene.frame_end = end
        scene.frame_step = max(1, abs(step))
        scene.frame_set(start)

    scene["bz2_source_dsc"] = str(metadata.get("scene_dsc") or metadata.get("scene") or "")
    scene["bz2_scene_prefix"] = str(metadata.get("scene_prefix") or "")
    scene["bz2_softimage_output_file"] = str(setup.get("output_file") or "")
    scene["bz2_reconstruction_schema"] = str(metadata.get("schema") or "")
    scene["bz2_shading_status"] = "source materials/textures reconstructed; Softimage renderer semantics still provisional"

    for record in recovered_cameras:
        obj = _find_object(str(record.get("name") or ""))
        if obj:
            obj["bz2_interest_xyz"] = list(record.get("interest_xyz") or [])
            obj["bz2_source_member"] = str(record.get("member") or "")
            obj["bz2_focal_length"] = float(record.get("focal_length") or 0.0)
            obj["bz2_f_stop"] = float(record.get("f_stop") or 0.0)
            obj["bz2_focus_distance"] = float(record.get("focus_distance") or 0.0)

    for record in metadata.get("lights") or []:
        obj = _find_object(str(record.get("name") or ""))
        if obj:
            obj["bz2_interest_xyz"] = list(record.get("interest_xyz") or [])
            obj["bz2_source_member"] = str(record.get("member") or "")
            obj["bz2_source_cone_angle"] = float(record.get("cone_angle") or 0.0)
            obj["bz2_source_cone_spread"] = float(record.get("cone_spread") or 0.0)

    output_blend.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend.resolve()))
    return {
        "output_blend": str(output_blend),
        "object_count": len(bpy.data.objects),
        "material_count": len(bpy.data.materials),
        "image_count": len(bpy.data.images),
        "camera_count": len(bpy.data.cameras),
        "light_count": len(bpy.data.lights),
        "active_camera": active_camera.name if active_camera else None,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "frame_range": [scene.frame_start, scene.frame_end, scene.frame_step],
    }


def main() -> int:
    args = _argv()
    if len(args) != 3:
        raise SystemExit("usage: blender --background --python blender_reconstruct_scene.py -- <scene.gltf> <scene.scene.json> <output.blend>")
    summary = reconstruct(Path(args[0]), Path(args[1]), Path(args[2]))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

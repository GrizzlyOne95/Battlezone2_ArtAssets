"""Build a ready-to-open .blend from a bundle's engine glTF twin (run inside Blender).

    blender --background --factory-startup --python scripts/blender_engine_blend.py -- \
        <bundle>/engine/<scene>.gltf <bundle>/<scene>.blend [scene.scene.json] [scene.render_state.json]

The model is the engine export: baked textures, the same geometry and UVs as
the .xsi. The recovered Softimage camera (vertical FOV, aimed at its
interest) and lights are added, along with the STS render resolution and
ambience. Texture paths are stored relative to the .blend, so the bundle
folder can be moved as a whole. Softimage Y-up data is converted to
Blender Z-up the same way the glTF importer converts the model:
(x, y, z) -> (x, -z, y).
"""

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1 :]
gltf_path, blend_path = Path(argv[0]), Path(argv[1])
scene_json = Path(argv[2]) if len(argv) > 2 and argv[2] else None
render_json = Path(argv[3]) if len(argv) > 3 and argv[3] else None


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else {}
    except (OSError, ValueError):
        return {}


def to_blender(xyz):
    x, y, z = (float(v) for v in xyz)
    return Vector((x, -z, y))


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = blend_path.stem
bpy.ops.import_scene.gltf(filepath=str(gltf_path))
meshes = [o for o in scene.objects if o.type == "MESH"]

points = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box] or [Vector((0, 0, 0))]
low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
centre, radius = (low + high) / 2, max((high - low).length / 2, 1e-3)

info = load(scene_json)
render = load(render_json)

# camera: the recovered Softimage camera, else a three-quarter framing
camera_data = bpy.data.cameras.new("Camera")
camera = bpy.data.objects.new("Camera", camera_data)
scene.collection.objects.link(camera)
source_cameras = [c for c in info.get("cameras") or [] if c.get("position_xyz") and c.get("interest_xyz")]
if source_cameras:
    source = source_cameras[0]
    camera.name = source.get("name") or "Camera"
    camera.location = to_blender(source["position_xyz"])
    target = to_blender(source["interest_xyz"])
    camera_data.sensor_fit = "VERTICAL"
    if source.get("fov_radians"):
        camera_data.angle_y = float(source["fov_radians"])
    camera["bz2_source_camera"] = json.dumps({k: source.get(k) for k in ("name", "member", "fov_radians")})
else:
    target = centre
    camera.location = centre + Vector((1.0, -1.3, 0.8)).normalized() * radius * 2.6
camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
distance = (target - camera.location).length
camera_data.clip_start = max(distance / 10000.0, 1e-3)
camera_data.clip_end = max(distance + radius * 4.0, 100.0)
scene.camera = camera

# lights: the recovered scene lights as point lights. Each is scaled to its
# distance (constant irradiance at the model) and the total is shared across
# the lights, so many-light scenes (the 16-light Pluto hangar) are not blown out.
source_lights = [light for light in info.get("lights") or [] if light.get("position_xyz")]
for index, light in enumerate(source_lights):
    if not light.get("position_xyz"):
        continue
    data = bpy.data.lights.new(light.get("name") or f"Light{index}", "POINT")
    data.color = [max(0.0, min(1.0, float(c))) for c in (light.get("color_rgb") or [1, 1, 1])[:3]]
    obj = bpy.data.objects.new(data.name, data)
    obj.location = to_blender(light["position_xyz"])
    reach = max((obj.location - centre).length, radius)
    data.energy = 4.0 * math.pi * reach * reach * 3.0 / max(1, len(source_lights))
    data.shadow_soft_size = radius * 0.05
    obj["bz2_source_light"] = json.dumps({k: light.get(k) for k in ("name", "member", "color_rgb", "intensity")})
    scene.collection.objects.link(obj)
if not source_lights:
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    scene.collection.objects.link(sun)

# world ambience (STS AMBIENCE) and render settings
world = bpy.data.worlds.new("World")
scene.world = world
ambience = render.get("ambience_rgb") or [0.3, 0.3, 0.3]
try:
    background = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    background.inputs[0].default_value = (*[float(c) for c in ambience[:3]], 1.0)
except (AttributeError, StopIteration):
    world.color = [float(c) for c in ambience[:3]]
resolution = render.get("resolution")
if resolution and len(resolution) == 2 and resolution[0] and resolution[1]:
    scene.render.resolution_x, scene.render.resolution_y = int(resolution[0]), int(resolution[1])

# open in Material Preview so textures show immediately
for screen in bpy.data.screens:
    for area in screen.areas:
        for space in area.spaces:
            if space.type == "VIEW_3D":
                space.shading.type = "MATERIAL"
                space.clip_end = max(space.clip_end, camera_data.clip_end)
                # frame the model, not Blender's default view
                space.region_3d.view_location = centre
                space.region_3d.view_distance = radius * 3.0

scene["bz2_bundle"] = str(blend_path.parent.name)
scene["bz2_source_gltf"] = str(gltf_path.relative_to(blend_path.parent)) if gltf_path.is_relative_to(blend_path.parent) else str(gltf_path)
bpy.ops.file.make_paths_relative()
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), relative_remap=True, compress=True)
print("BLEND_OK", blend_path, len(meshes))

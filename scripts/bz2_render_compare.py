#!/usr/bin/env python3
"""Score an exported engine XSI against Softimage's own render of its scene.

The archive preserves the mental ray frames each scene produced
(``<group>/RENDER_PICTURES/<output>.<frame>.pic``) and the pipeline recovers
the scene's camera (``scene.scene.json``) and render setup
(``scene.render_state.json``). This tool rasterizes the exported XSI through
that camera -- unlit texture colour, z-buffered, perspective-correct UVs --
and measures it against the reference:

* ``edge_alignment``: normalized correlation of luminance gradients. It
  validates the camera and geometry before any texture score means anything.
  (The reference alpha is not a silhouette: mental ray's reflective floor is
  opaque.) Softimage's ``fov_radians`` is the vertical angle: on
  NewTank/TANK.1 the edge correlation peaks at the recovered camera with zero
  pixel offset for vertical, and nowhere near it for horizontal.
* ``gain_fit_error``: per part, the reference is modelled as ``k * ours``
  (one lighting gain per part absorbs mental ray's diffuse shading); the
  remaining relative RMS error measures texture colour and placement.
* ``chroma_error``: mean chromaticity distance, independent of brightness.

Scores are also reported per exported frame, so a single mis-mapped part
(e.g. a fin whose projection lands on the wrong part of its picture) stands
out. A side-by-side PNG (reference | ours | error) is written for review.

Specular sheen, reflections and glow are renderer effects the export cannot
carry; they show up as residual error on every candidate equally, so compare
scores between pipeline variants rather than reading them as absolutes.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "tools" / "io_scene_bz2xsi"))

import bz2xsi  # noqa: E402
import softimage_pic  # noqa: E402

bz2xsi.ALLOW_PRINT = False

MODELS_MARKER = "/modelsdirectory/"


# --------------------------------------------------------------------------- reference


def _source_tree(bundle_dir: Path, source_root: Path) -> tuple[Path | zipfile.ZipFile, str]:
    """Where the bundle's scene came from: the primary tree or an embedded ZIP."""
    match = re.match(r"^(.+?\.zip)__", bundle_dir.name, re.IGNORECASE)
    if match:
        for candidate in source_root.glob(f"**/{match.group(1)}"):
            return zipfile.ZipFile(candidate), match.group(1)
    return source_root, ""


_MEMBER_CACHE: dict[str, dict[str, str]] = {}


def _members(tree: Path | zipfile.ZipFile) -> dict[str, str]:
    key = tree.filename if isinstance(tree, zipfile.ZipFile) else str(tree)
    if key not in _MEMBER_CACHE:
        _MEMBER_CACHE[key] = _list_members(tree)
    return _MEMBER_CACHE[key]


def _list_members(tree: Path | zipfile.ZipFile) -> dict[str, str]:
    if isinstance(tree, zipfile.ZipFile):
        names = tree.namelist()
    else:
        names = [p.relative_to(tree).as_posix() for p in tree.glob("**/RENDER_PICTURES/**/*") if p.is_file()]
    return {name.lower(): name for name in names}


def reference_candidates(bundle_dir: Path) -> list[str]:
    """Relative member paths the scene's STS output would have written."""
    state_path = bundle_dir / "scene.render_state.json"
    if not state_path.is_file():
        return []
    state = json.loads(state_path.read_text(encoding="utf-8"))
    output = (state.get("output_file") or "").replace("\\", "/")
    frames = state.get("rendering_frame") or [1, 1, 1]
    if not output:
        return []
    lowered = output.lower()
    if MODELS_MARKER in lowered:
        relative = output[lowered.index(MODELS_MARKER) + len(MODELS_MARKER):]
    elif "/render_pictures/" in lowered:
        # an artist's local drive (E:/WALKER/walkerstuff/walker_final/...): keep
        # "<group>/RENDER_PICTURES/<name>" and match it as a member-path suffix
        at = lowered.rindex("/render_pictures/")
        group = output[:at].rstrip("/").rsplit("/", 1)[-1]
        relative = "*/" + group + output[at:]
    else:
        relative = output.lstrip("/")
    start, end = int(frames[0]), int(frames[1])
    return [f"{relative}.{frame}.pic" for frame in range(start, max(start, end) + 1)]


def _match_member(members: dict[str, str], candidate: str) -> str | None:
    key = candidate.lower()
    if not key.startswith("*/"):
        return members.get(key)
    suffix = key[1:]
    found = sorted(name for lower, name in members.items() if lower.endswith(suffix) or lower == suffix[1:])
    return found[0] if found else None


def load_reference(bundle_dir: Path, source_root: Path) -> tuple[np.ndarray, str] | None:
    """(H, W, 4) float RGBA of the scene's original render, top row first."""
    tree, prefix = _source_tree(bundle_dir, source_root)
    members = _members(tree)
    for candidate in reference_candidates(bundle_dir):
        name = _match_member(members, candidate)
        if name is None:
            continue
        data = tree.read(name) if isinstance(tree, zipfile.ZipFile) else (tree / name).read_bytes()
        rgba, info = softimage_pic.decode_pic_bytes(data)
        width, height = int(info["width"]), int(info["height"])
        image = np.frombuffer(rgba, dtype=np.uint8).reshape(height, width, 4).astype(np.float64) / 255.0
        return image, f"{prefix + '/' if prefix else ''}{name}"
    return None


# --------------------------------------------------------------------------- scene


@dataclass
class Camera:
    position: np.ndarray
    interest: np.ndarray
    fov: float
    roll: float = 0.0


def load_camera(bundle_dir: Path) -> Camera | None:
    path = bundle_dir / "scene.scene.json"
    if not path.is_file():
        return None
    cameras = json.loads(path.read_text(encoding="utf-8")).get("cameras") or []
    for camera in cameras:
        if camera.get("position_xyz") and camera.get("interest_xyz") and camera.get("fov_radians"):
            return Camera(np.array(camera["position_xyz"], float), np.array(camera["interest_xyz"], float), float(camera["fov_radians"]))
    return None


@dataclass
class Light:
    colour: np.ndarray
    position: np.ndarray


def load_lighting(bundle_dir: Path) -> tuple[np.ndarray, list[Light]]:
    """Scene ambience (STS AMBIENCE) and the recovered lights (colour, position)."""
    ambience = np.array([0.3, 0.3, 0.3])
    state_path = bundle_dir / "scene.render_state.json"
    if state_path.is_file():
        ambience = np.array(json.loads(state_path.read_text(encoding="utf-8")).get("ambience_rgb") or ambience, float)
    lights = []
    scene_path = bundle_dir / "scene.scene.json"
    if scene_path.is_file():
        for light in json.loads(scene_path.read_text(encoding="utf-8")).get("lights") or []:
            if light.get("position_xyz") and light.get("color_rgb"):
                lights.append(Light(np.array(light["color_rgb"], float), np.array(light["position_xyz"], float)))
    return ambience, lights


@dataclass
class Triangles:
    positions: np.ndarray  # (T, 3, 3) world
    uvs: np.ndarray  # (T, 3, 2) Softimage bottom-left
    colour: np.ndarray  # (T, 3) material diffuse
    texture: list[str | None]  # per triangle
    part: np.ndarray  # (T,) frame index
    normals: np.ndarray  # (T, 3, 3) world corner normals
    part_names: list[str] = field(default_factory=list)


def _matrix(matrix: bz2xsi.Matrix | None) -> np.ndarray:
    if matrix is None:
        return np.eye(4)
    return np.array(matrix.to_list(), dtype=np.float64)


def load_xsi(path: Path) -> Triangles:
    """World-space triangles of an engine XSI (row-vector matrices, child @ parent)."""
    xsi = bz2xsi.read(str(path))
    positions, uvs, colours, textures, parts, names, normals = [], [], [], [], [], [], []

    def visit(frame: bz2xsi.Frame, parent: np.ndarray) -> None:
        world = _matrix(frame.transform) @ parent
        mesh = frame.mesh
        if mesh is not None and mesh.faces:
            part = len(names)
            names.append(frame.name)
            vertices = np.array(mesh.vertices, dtype=np.float64)
            homogeneous = np.c_[vertices, np.ones(len(vertices))] @ world
            uv_vertices = np.array(mesh.uv_vertices, dtype=np.float64) if mesh.uv_vertices else None
            normal_vertices = np.array(mesh.normal_vertices, dtype=np.float64) if mesh.normal_vertices else None
            if normal_vertices is not None:
                normal_vertices = normal_vertices @ world[:3, :3]
            for face_index, face in enumerate(mesh.faces):
                material = mesh.face_materials[face_index] if face_index < len(mesh.face_materials) else None
                uv_face = mesh.uv_faces[face_index] if uv_vertices is not None and face_index < len(mesh.uv_faces) else None
                normal_face = mesh.normal_faces[face_index] if normal_vertices is not None and face_index < len(mesh.normal_faces) else None
                for corner in range(1, len(face) - 1):
                    order = (0, corner, corner + 1)
                    positions.append(homogeneous[[face[i] for i in order], :3])
                    uvs.append(uv_vertices[[uv_face[i] for i in order]] if uv_face is not None else np.zeros((3, 2)))
                    corners = homogeneous[[face[i] for i in order], :3]
                    if normal_face is not None:
                        normals.append(normal_vertices[[normal_face[i] for i in order]])
                    else:
                        flat = np.cross(corners[1] - corners[0], corners[2] - corners[0])
                        normals.append(np.repeat(flat[None], 3, axis=0))
                    colours.append((material.diffuse[:3] if material else (0.7, 0.7, 0.7)))
                    textures.append(material.texture if material else None)
                    parts.append(part)
        for child in frame.frames:
            visit(child, world)

    for root in xsi.frames:
        visit(root, np.eye(4))
    return Triangles(
        np.array(positions).reshape(-1, 3, 3),
        np.array(uvs).reshape(-1, 3, 2),
        np.array(colours, dtype=np.float64).reshape(-1, 3),
        textures,
        np.array(parts, dtype=np.int32),
        np.array(normals, dtype=np.float64).reshape(-1, 3, 3),
        names,
    )


# --------------------------------------------------------------------------- raster


def view_projection(camera: Camera, width: int, height: int, fov_axis: str = "vertical") -> tuple[np.ndarray, float, float]:
    """World->camera rotation rows plus focal lengths in pixels (Softimage is Y-up)."""
    forward = camera.interest - camera.position
    forward /= np.linalg.norm(forward)
    up = np.array([0.0, 1.0, 0.0])
    if abs(forward @ up) > 0.999:
        up = np.array([0.0, 0.0, 1.0])
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    true_up = np.cross(right, forward)
    if camera.roll:
        c, s = math.cos(camera.roll), math.sin(camera.roll)
        right, true_up = c * right + s * true_up, -s * right + c * true_up
    rotation = np.stack([right, true_up, forward])
    extent = {"horizontal": width, "vertical": height, "diagonal": math.hypot(width, height)}[fov_axis]
    focal = (extent / 2.0) / math.tan(camera.fov / 2.0)
    return rotation, focal, focal


def rasterize(
    tris: Triangles,
    textures: dict[str, np.ndarray | None],
    camera: Camera,
    width: int,
    height: int,
    fov_axis: str = "vertical",
    lighting: tuple[np.ndarray, list[Light]] | None = None,
):
    """Texture colour, coverage and part id buffers (top row first).

    With ``lighting`` the colour is Softimage-style diffuse: texture/material
    colour times (ambience + sum of light colour * max(0, N.L)), two-sided,
    no shadows, specular, reflection or glow.
    """
    rotation, fx, fy = view_projection(camera, width, height, fov_axis)
    local = (tris.positions - camera.position) @ rotation.T  # (T, 3, 3): x right, y up, z depth
    depth = local[..., 2]
    keep = np.all(depth > 1e-3, axis=1)
    sx = width / 2.0 + fx * local[..., 0] / np.where(depth > 1e-3, depth, 1.0)
    sy = height / 2.0 - fy * local[..., 1] / np.where(depth > 1e-3, depth, 1.0)

    colour = np.zeros((height, width, 3))
    zbuffer = np.full((height, width), np.inf)
    part = np.full((height, width), -1, dtype=np.int32)
    world = np.zeros((height, width, 3))
    normal = np.zeros((height, width, 3))
    for index in np.nonzero(keep)[0]:
        x, y, z = sx[index], sy[index], depth[index]
        x0, x1 = max(int(math.floor(x.min())), 0), min(int(math.ceil(x.max())), width - 1)
        y0, y1 = max(int(math.floor(y.min())), 0), min(int(math.ceil(y.max())), height - 1)
        if x0 > x1 or y0 > y1:
            continue
        area = (x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0])
        if abs(area) < 1e-12:
            continue
        px, py = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w0 = ((x[1] - px) * (y[2] - py) - (x[2] - px) * (y[1] - py)) / area
        w1 = ((x[2] - px) * (y[0] - py) - (x[0] - px) * (y[2] - py)) / area
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        if not inside.any():
            continue
        # perspective-correct interpolation
        p0, p1, p2 = w0 / z[0], w1 / z[1], w2 / z[2]
        norm = p0 + p1 + p2
        zi = 1.0 / norm
        region_z = zbuffer[y0 : y1 + 1, x0 : x1 + 1]
        visible = inside & (zi < region_z)
        if not visible.any():
            continue
        region_z[visible] = zi[visible]
        part[y0 : y1 + 1, x0 : x1 + 1][visible] = tris.part[index]
        if lighting is not None:
            weights = np.stack([p0[visible], p1[visible], p2[visible]], axis=1) / norm[visible][:, None]
            world[y0 : y1 + 1, x0 : x1 + 1][visible] = weights @ tris.positions[index]
            normal[y0 : y1 + 1, x0 : x1 + 1][visible] = weights @ tris.normals[index]
        image = textures.get(tris.texture[index]) if tris.texture[index] else None
        if image is None:
            colour[y0 : y1 + 1, x0 : x1 + 1][visible] = tris.colour[index]
            continue
        uv = tris.uvs[index]
        u = (p0 * uv[0, 0] + p1 * uv[1, 0] + p2 * uv[2, 0]) / norm
        v = (p0 * uv[0, 1] + p1 * uv[1, 1] + p2 * uv[2, 1]) / norm
        th, tw = image.shape[:2]
        tx = np.clip((np.mod(u[visible], 1.0) * tw).astype(int), 0, tw - 1)
        ty = np.clip(((1.0 - np.mod(v[visible], 1.0)) * th).astype(int), 0, th - 1)
        colour[y0 : y1 + 1, x0 : x1 + 1][visible] = image[ty, tx, :3]
    covered = part >= 0
    if lighting is not None and covered.any():
        ambience, lights = lighting
        n = normal[covered]
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        points = world[covered]
        # two-sided: face the normal toward the camera
        n *= np.where(((camera.position - points) * n).sum(1) < 0, -1.0, 1.0)[:, None]
        shade = np.repeat(ambience[None], len(n), axis=0)
        for light in lights:
            direction = light.position - points
            direction /= np.maximum(np.linalg.norm(direction, axis=1, keepdims=True), 1e-12)
            shade += light.colour[None] * np.clip((n * direction).sum(1), 0, None)[:, None]
        colour[covered] = colour[covered] * shade
    return colour, covered, part


def load_textures(engine_dir: Path, names: set[str | None]) -> dict[str, np.ndarray | None]:
    from PIL import Image

    out: dict[str, np.ndarray | None] = {}
    for name in names:
        if not name:
            continue
        path = engine_dir / name
        if path.is_file():
            with Image.open(path) as image:
                out[name] = np.asarray(image.convert("RGB"), dtype=np.float64) / 255.0
        else:
            out[name] = None
    return out


# --------------------------------------------------------------------------- scoring


def _resize(image: np.ndarray, width: int, height: int) -> np.ndarray:
    from PIL import Image

    channels = image.shape[2]
    pil = Image.fromarray((np.clip(image, 0, 1) * 255).astype(np.uint8), "RGBA" if channels == 4 else "RGB")
    return np.asarray(pil.resize((width, height), Image.BILINEAR), dtype=np.float64) / 255.0


def _gradient(image: np.ndarray) -> np.ndarray:
    luminance = image[..., :3].mean(axis=2)
    gx, gy = np.zeros_like(luminance), np.zeros_like(luminance)
    gx[:, 1:-1] = luminance[:, 2:] - luminance[:, :-2]
    gy[1:-1] = luminance[2:] - luminance[:-2]
    return np.hypot(gx, gy)


def edge_alignment(reference: np.ndarray, ours: np.ndarray, covered: np.ndarray) -> float:
    a = _gradient(reference[..., :3] * (reference[..., 3:4] > 0.5))
    b = _gradient(ours * covered[..., None])
    a, b = a - a.mean(), b - b.mean()
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float((a * b).sum() / denominator) if denominator else 0.0


def score(reference: np.ndarray, ours: np.ndarray, covered: np.ndarray, part: np.ndarray, part_names: list[str]) -> dict:
    ref_rgb, ref_alpha = reference[..., :3], reference[..., 3] > 0.5
    both = ref_alpha & covered

    def gain_fit(mask: np.ndarray) -> tuple[float, float] | None:
        a, b = ours[mask].ravel(), ref_rgb[mask].ravel()
        if a.size < 30 or float(a @ a) < 1e-9:
            return None
        gain = float(a @ b / (a @ a))
        rms = float(np.sqrt(np.mean((b - gain * a) ** 2)))
        scale = float(np.sqrt(np.mean(b**2))) or 1.0
        return gain, rms / scale

    def chroma(mask: np.ndarray) -> float | None:
        a, b = ours[mask], ref_rgb[mask]
        bright = (a.sum(1) > 0.06) & (b.sum(1) > 0.06)
        if bright.sum() < 30:
            return None
        ca = a[bright] / a[bright].sum(1, keepdims=True)
        cb = b[bright] / b[bright].sum(1, keepdims=True)
        return float(np.mean(np.linalg.norm(ca - cb, axis=1)))

    overall = gain_fit(both)
    parts = []
    for index, name in enumerate(part_names):
        mask = both & (part == index)
        pixels = int(mask.sum())
        if pixels < 30:
            continue
        fit = gain_fit(mask)
        parts.append(
            {
                "part": name,
                "pixels": pixels,
                "gain": round(fit[0], 4) if fit else None,
                "gain_fit_error": round(fit[1], 4) if fit else None,
                "chroma_error": round(chroma(mask), 4) if chroma(mask) is not None else None,
            }
        )
    parts.sort(key=lambda item: -item["pixels"] * (item["gain_fit_error"] or 0))
    return {
        "edge_alignment": round(edge_alignment(reference, ours, covered), 4),
        "gain_fit_error": round(overall[1], 4) if overall else None,
        "chroma_error": round(chroma(both), 4) if chroma(both) is not None else None,
        "overlap_pixels": int(both.sum()),
        "parts": parts,
    }


def compare_bundle(
    bundle_dir: Path,
    source_root: Path,
    *,
    xsi_path: Path | None = None,
    max_size: int = 900,
    fov_axis: str = "vertical",
    image_out: Path | None = None,
) -> dict:
    bundle_dir = Path(bundle_dir)
    reference = load_reference(bundle_dir, source_root)
    if reference is None:
        return {"bundle": bundle_dir.name, "status": "no_reference", "candidates": reference_candidates(bundle_dir)}
    camera = load_camera(bundle_dir)
    if camera is None:
        return {"bundle": bundle_dir.name, "status": "no_camera"}
    ref_image, ref_name = reference
    engine_dir = bundle_dir / "engine"
    xsi_path = xsi_path or next(iter(sorted(engine_dir.glob("*.xsi"))), None)
    if xsi_path is None:
        return {"bundle": bundle_dir.name, "status": "no_xsi"}
    tris = load_xsi(xsi_path)
    lighting = load_lighting(bundle_dir)
    textures = load_textures(xsi_path.parent, set(tris.texture))

    full_h, full_w = ref_image.shape[:2]
    factor = min(1.0, max_size / max(full_w, full_h))
    width, height = max(1, round(full_w * factor)), max(1, round(full_h * factor))
    ref_small = _resize(ref_image, width, height)

    axes = ["horizontal", "vertical", "diagonal"] if fov_axis == "auto" else [fov_axis]
    best = None
    for axis in axes:
        colour, covered, part = rasterize(tris, textures, camera, width, height, axis, lighting)
        result = score(ref_small, colour, covered, part, tris.part_names)
        if best is None or result["edge_alignment"] > best[0]["edge_alignment"]:
            best = (result, axis, colour, covered)
    result, axis, colour, covered = best
    if image_out is not None:
        image_out.parent.mkdir(parents=True, exist_ok=True)
        write_side_by_side(image_out, ref_small, colour, covered)
    return {
        "bundle": bundle_dir.name,
        "status": "ok",
        "reference": ref_name,
        "xsi": xsi_path.name,
        "resolution": [width, height],
        "fov_axis": axis,
        **result,
    }


def write_side_by_side(path: Path, reference: np.ndarray, ours: np.ndarray, covered: np.ndarray) -> None:
    from PIL import Image

    ref_rgb = reference[..., :3] * (reference[..., 3:4] > 0.5)
    shown = ours * covered[..., None]
    both = (reference[..., 3] > 0.5) & covered
    a, b = shown[both].ravel(), ref_rgb[both].ravel()
    gain = float(a @ b / (a @ a)) if both.any() and float(a @ a) > 0 else 1.0
    error = np.abs(ref_rgb - gain * shown).mean(axis=2, keepdims=True) * 3.0
    silhouette = np.zeros_like(ours)
    silhouette[..., 0] = (reference[..., 3] > 0.5) & ~covered  # red: reference only
    silhouette[..., 2] = covered & ~(reference[..., 3] > 0.5)  # blue: ours only
    error_rgb = np.clip(np.repeat(error, 3, axis=2) * both[..., None] + silhouette, 0, 1)
    strip = np.concatenate([ref_rgb, np.clip(shown * gain, 0, 1), error_rgb], axis=1)
    Image.fromarray((strip * 255).astype(np.uint8), "RGB").save(path)


def find_pairs(reconstructed: Path, source_root: Path) -> list[Path]:
    """Bundles whose STS output names a render that exists in the archive."""
    pairs = []
    for bundle in sorted(p for p in reconstructed.iterdir() if p.is_dir()):
        candidates = reference_candidates(bundle)
        if not candidates:
            continue
        tree, _prefix = _source_tree(bundle, source_root)
        members = _members(tree)
        if any(_match_member(members, candidate) for candidate in candidates):
            pairs.append(bundle)
    return pairs


def _compare_job(bundle: str, source: str, max_size: int, images: str | None) -> dict:
    image_out = Path(images) / f"{Path(bundle).name}.png" if images else None
    try:
        return compare_bundle(Path(bundle), Path(source), max_size=max_size, image_out=image_out)
    except Exception as exc:  # keep the batch going; the failure is reported
        return {"bundle": Path(bundle).name, "status": "error", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundles", nargs="*", type=Path, help="reconstructed scene bundle directories")
    parser.add_argument("--all", type=Path, metavar="RECONSTRUCTED", help="score every bundle under this directory that has a reference render")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--source", type=Path, default=Path(".bz2-source-cache/modelsdirectory"))
    parser.add_argument("--xsi", type=Path, help="score this XSI instead of the bundle's engine export")
    parser.add_argument("--max-size", type=int, default=900)
    parser.add_argument("--fov-axis", choices=["auto", "horizontal", "vertical", "diagonal"], default="vertical")
    parser.add_argument("--images", type=Path, help="write reference|ours|error PNGs here")
    parser.add_argument("--json", type=Path, help="write the results here")
    parser.add_argument("--top-parts", type=int, default=8)
    args = parser.parse_args()

    bundles = list(args.bundles) + (find_pairs(args.all, args.source) if args.all else [])
    if args.jobs > 1 and not args.xsi:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(args.jobs) as pool:
            futures = [
                pool.submit(_compare_job, str(b), str(args.source), args.max_size, str(args.images) if args.images else None)
                for b in bundles
            ]
            computed = [future.result() for future in futures]
    else:
        computed = []
        for bundle in bundles:
            image_out = args.images / f"{bundle.name}.png" if args.images else None
            computed.append(
                compare_bundle(bundle, args.source, xsi_path=args.xsi, max_size=args.max_size, fov_axis=args.fov_axis, image_out=image_out)
            )
    results = []
    for result in computed:
        bundle = Path(result["bundle"])
        results.append(result)
        if result["status"] != "ok":
            print(f"{bundle.name}: {result['status']}")
            continue
        print(
            f"{bundle.name}: edges={result['edge_alignment']} gain_fit_error={result['gain_fit_error']} "
            f"chroma={result['chroma_error']} fov={result['fov_axis']} ref={result['reference']}"
        )
        for item in result["parts"][: args.top_parts]:
            print(f"    {item['part']:<32} px={item['pixels']:<7} err={item['gain_fit_error']} chroma={item['chroma_error']}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

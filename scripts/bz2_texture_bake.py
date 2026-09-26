#!/usr/bin/env python3
"""Bake a Softimage multi-layer texture stack into one atlas texture per mesh.

The engines (and dotXSI/FBX/glTF base colour) take one texture per material,
while Softimage composited several: model-local code-400 textures (own or
branch-inherited) and ordered material code-401 layers, each with its own UV
mapping and blending. This module:

1. charts the mesh (flood fill over shared edges, splitting at >45 degree
   facing changes), flattens each chart orthographically and shelf-packs the
   charts into a square atlas with padding;
2. rasterizes every atlas triangle, interpolates each layer's own corner UVs,
   samples the layer (bilinear, wrapped, Softimage bottom-left UV space) and
   composites bottom-to-top from the material diffuse colour with the
   Softimage blending rules recovered from TXMP:

       mask   = 1 (type 3, no mask), alpha (type 1) or luminance*alpha (type 2)
       colour = lerp(colour, texel * diffuse_factor, mask * blending)

3. dilates the result into the padding so mipmaps and bilinear filtering do not
   bleed background into chart edges.

Everything is renderer-independent numpy; UVs are returned in Softimage space.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MIN_ATLAS = 128
MAX_ATLAS = 1024
PADDING_PX = 4
CHART_COS = math.cos(math.radians(45.0))


@dataclass
class Layer:
    """One texture in the stack: image (H, W, 4) floats 0..1 and per-corner UVs [F, 3, 2]."""

    image: np.ndarray
    corner_uv: np.ndarray
    blending_type: int = 3
    blending: float = 1.0
    diffuse: float = 1.0
    label: str = ""


# ---------------------------------------------------------------------------
# Charting and packing


def _face_normals(corners: np.ndarray) -> np.ndarray:
    normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    length = np.linalg.norm(normal, axis=1, keepdims=True)
    return np.divide(normal, length, out=np.zeros_like(normal), where=length > 1.0e-12)


def build_charts(corners: np.ndarray) -> list[list[int]]:
    """Group triangles into charts of edge-connected, similarly facing faces."""
    normals = _face_normals(corners)
    keys = np.round(corners.reshape(-1, 3), 5)
    _, vertex_ids = np.unique(keys, axis=0, return_inverse=True)
    vertex_ids = vertex_ids.reshape(-1, 3)
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for face, tri in enumerate(vertex_ids):
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            edge_faces.setdefault((min(a, b), max(a, b)), []).append(face)
    neighbours: list[list[int]] = [[] for _ in range(len(corners))]
    for faces in edge_faces.values():
        for face in faces:
            neighbours[face].extend(other for other in faces if other != face)

    chart_of = np.full(len(corners), -1)
    charts: list[list[int]] = []
    for seed in range(len(corners)):
        if chart_of[seed] >= 0:
            continue
        chart_id = len(charts)
        seed_normal = normals[seed]
        stack, members = [seed], []
        chart_of[seed] = chart_id
        while stack:
            face = stack.pop()
            members.append(face)
            for other in neighbours[face]:
                if chart_of[other] < 0 and float(normals[other] @ seed_normal) >= CHART_COS:
                    chart_of[other] = chart_id
                    stack.append(other)
        charts.append(members)
    return charts


def _chart_basis(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if np.linalg.norm(normal) < 1.0e-9:
        normal = np.array([0.0, 0.0, 1.0])
    helper = np.eye(3)[int(np.argmin(np.abs(normal)))]
    u = np.cross(normal, helper)
    u /= np.linalg.norm(u)
    return u, np.cross(normal, u)


def layout_atlas(corners: np.ndarray, texels_per_unit: float) -> tuple[np.ndarray, int]:
    """Return per-corner atlas pixel coordinates [F, 3, 2] (x right, y down) and atlas size."""
    normals = _face_normals(corners)
    areas = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1)
    flattened = []
    for members in build_charts(corners):
        weights = areas[members][:, None]
        normal = (normals[members] * weights).sum(axis=0)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        u, v = _chart_basis(normal)
        points = corners[members]
        flat = np.stack([points @ u, points @ v], axis=-1)
        flat -= flat.reshape(-1, 2).min(axis=0)
        flattened.append((members, flat))

    total_area = float(areas.sum()) or 1.0
    size = int(2 ** math.ceil(math.log2(max(MIN_ATLAS, min(MAX_ATLAS, math.sqrt(total_area) * texels_per_unit * 1.4)))))
    size = max(MIN_ATLAS, min(MAX_ATLAS, size))
    # The atlas size already follows the source texel density; fill it, then
    # shrink until the shelf packer fits.
    scale = (size * 0.95) / math.sqrt(total_area)
    order = sorted(range(len(flattened)), key=lambda i: -float(flattened[i][1][..., 1].max()))
    for _attempt in range(40):
        placed = _shelf_pack([flattened[i][1] * scale for i in order], size)
        if placed is not None:
            break
        scale *= 0.9
    else:
        raise RuntimeError("could not pack atlas charts")
    atlas = np.zeros((len(corners), 3, 2))
    for slot, index in enumerate(order):
        members, flat = flattened[index]
        atlas[members] = flat * scale + placed[slot]
    return atlas, size


def _shelf_pack(charts: list[np.ndarray], size: int):
    offsets = []
    x = y = shelf = float(PADDING_PX)
    for chart in charts:
        width, height = float(chart[..., 0].max()), float(chart[..., 1].max())
        if width + 2 * PADDING_PX > size or height + 2 * PADDING_PX > size:
            return None
        if x + width + PADDING_PX > size:
            x, y = float(PADDING_PX), y + shelf + PADDING_PX
            shelf = 0.0
        if y + height + PADDING_PX > size:
            return None
        offsets.append(np.array([x, y]))
        x += width + 2 * PADDING_PX
        shelf = max(shelf, height)
    return offsets


def texel_density(corners: np.ndarray, layer: Layer) -> float:
    """Texels per world unit of a layer, so the bake keeps the source detail level."""
    height, width = layer.image.shape[:2]
    world = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1)
    uv = layer.corner_uv
    texel = 0.5 * np.abs(
        (uv[:, 1, 0] - uv[:, 0, 0]) * (uv[:, 2, 1] - uv[:, 0, 1]) - (uv[:, 2, 0] - uv[:, 0, 0]) * (uv[:, 1, 1] - uv[:, 0, 1])
    ) * width * height
    valid = world > 1.0e-12
    if not valid.any() or texel[valid].sum() <= 0:
        return 0.0
    return math.sqrt(float(texel[valid].sum()) / float(world[valid].sum()))


# ---------------------------------------------------------------------------
# Sampling and compositing


def _sample(image: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Bilinear, wrapped sample of an (H, W, 4) image at Softimage-space UVs [N, 2]."""
    height, width = image.shape[:2]
    x = (uv[:, 0] % 1.0) * width - 0.5
    y = (1.0 - (uv[:, 1] % 1.0)) * height - 0.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    x0, x1 = x0 % width, (x0 + 1) % width
    y0, y1 = y0 % height, (y0 + 1) % height
    top = image[y0, x0] * (1 - fx) + image[y0, x1] * fx
    bottom = image[y1, x0] * (1 - fx) + image[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


def blend_mask(rgb: np.ndarray, alpha: np.ndarray, blending_type: int) -> np.ndarray:
    """Softimage texture blending mask (TXMP +86): 1 alpha, 2 intensity, 3 none.

    Intensity is luminance times alpha. Plain luminance, mean RGB and max RGB
    scored within noise of it against walker_final/walker.1, the only aligned
    reference render that uses intensity masks.
    """
    if blending_type == 2:
        return (rgb @ np.array([0.299, 0.587, 0.114]))[..., None] * alpha
    if blending_type == 1:
        return alpha
    # Type 3 "no mask": the picture's alpha channel is ignored.
    return np.ones_like(alpha)


def composite(base_rgb, texels: list[np.ndarray], layers: list[Layer]) -> np.ndarray:
    colour = np.broadcast_to(np.asarray(base_rgb, dtype=np.float64), (texels[0].shape[0], 3)).copy()
    for texel, layer in zip(texels, layers):
        rgb, alpha = texel[:, :3], texel[:, 3:4]
        mask = np.clip(blend_mask(rgb, alpha, layer.blending_type) * layer.blending, 0.0, 1.0)
        colour = colour * (1.0 - mask) + np.clip(rgb * layer.diffuse, 0.0, 1.0) * mask
    return np.clip(colour, 0.0, 1.0)


def _dilate(image: np.ndarray, filled: np.ndarray, passes: int) -> tuple[np.ndarray, np.ndarray]:
    for _ in range(passes):
        if filled.all():
            break
        total = np.zeros_like(image)
        count = np.zeros(filled.shape)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                shifted_fill = np.roll(np.roll(filled, dy, 0), dx, 1)
                total += np.roll(np.roll(image, dy, 0), dx, 1) * shifted_fill[..., None]
                count += shifted_fill
        grow = (~filled) & (count > 0)
        image[grow] = total[grow] / count[grow][:, None]
        filled = filled | grow
    return image, filled


def bake(corners: np.ndarray, layers: list[Layer], base_rgb) -> tuple[np.ndarray, np.ndarray]:
    """Bake the stack. Returns (RGB uint8 atlas image, per-corner Softimage atlas UVs [F, 3, 2])."""
    density = max((texel_density(corners, layer) for layer in layers), default=0.0)
    atlas_px, size = layout_atlas(corners, density)
    image = np.zeros((size, size, 3))
    filled = np.zeros((size, size), dtype=bool)
    for face in range(len(corners)):
        tri = atlas_px[face]
        x_min, y_min = np.floor(tri.min(axis=0) - 1).astype(int)
        x_max, y_max = np.ceil(tri.max(axis=0) + 1).astype(int)
        x_min, y_min = max(x_min, 0), max(y_min, 0)
        x_max, y_max = min(x_max, size - 1), min(y_max, size - 1)
        if x_max < x_min or y_max < y_min:
            continue
        xs, ys = np.meshgrid(np.arange(x_min, x_max + 1) + 0.5, np.arange(y_min, y_max + 1) + 0.5)
        points = np.stack([xs.ravel(), ys.ravel()], axis=1)
        a, b, c = tri
        denom = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(denom) < 1.0e-12:
            continue
        w0 = ((b[1] - c[1]) * (points[:, 0] - c[0]) + (c[0] - b[0]) * (points[:, 1] - c[1])) / denom
        w1 = ((c[1] - a[1]) * (points[:, 0] - c[0]) + (a[0] - c[0]) * (points[:, 1] - c[1])) / denom
        w2 = 1.0 - w0 - w1
        # Slightly conservative coverage; dilation fills the remaining edge texels.
        inside = (w0 >= -0.02) & (w1 >= -0.02) & (w2 >= -0.02)
        if not inside.any():
            continue
        weights = np.stack([w0, w1, w2], axis=1)[inside]
        weights = np.clip(weights, 0.0, None)
        weights /= weights.sum(axis=1, keepdims=True)
        texels = [_sample(layer.image, weights @ layer.corner_uv[face]) for layer in layers]
        colour = composite(base_rgb, texels, layers)
        px = points[inside].astype(int)
        image[px[:, 1], px[:, 0]] = colour
        filled[px[:, 1], px[:, 0]] = True
    image, filled = _dilate(image, filled, PADDING_PX * 2)
    image[~filled] = np.asarray(base_rgb, dtype=np.float64)
    uv = np.empty_like(atlas_px)
    uv[..., 0] = atlas_px[..., 0] / size
    uv[..., 1] = 1.0 - atlas_px[..., 1] / size
    return (image * 255.0 + 0.5).astype(np.uint8), uv

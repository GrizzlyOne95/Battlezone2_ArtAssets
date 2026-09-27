#!/usr/bin/env python3
"""Compare engine XSI exports with the shipped Battlezone II ``.msh`` models.

The retail game ships compiled ``.msh`` files built from the same art. Each
one is an independent ground truth for the final model's geometry, UVs and
texturing. This tool:

1. parses the retail ``.msh`` files with the ``io_scene_bz2msh`` parser
   (``--msh-tool``, default ``../io_scene_bz2msh``);
2. pairs each multi-node ``.msh`` with the reconstructed bundle whose engine
   XSI frame names overlap most (Jaccard >= ``--min-jaccard``);
3. finds the axis mapping (sign flips) that best aligns the ``.msh``
   block-level geometry with the XSI world triangles;
4. reports geometry agreement (symmetric nearest-point distance, relative to
   the model size) and texture agreement: at every ``.msh`` face centre the
   shipped texture is sampled at the shipped UV, our texture at the nearest
   point on our surface, and colours are compared. That comparison stays
   valid where our UVs were re-packed into a baked atlas.

Shipped textures come from the retail pictures staged by
``bz2_retail_pictures.py``; the picture names in the ``.msh`` are matched by
stem.

    python scripts/bz2_msh_compare.py --retail "<extracted data.pak dir>" --json out/msh_compare.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import bz2_render_compare as rc  # noqa: E402
import bz2_texture_layers_gltf as texture_layers  # noqa: E402
import softimage_pic  # noqa: E402

DEFAULT_MSH_TOOL = SCRIPT_DIR.parents[1] / "io_scene_bz2msh"


def _stem(name: str) -> str:
    return re.sub(r"\.(tga|pic|bmp|dds)$", "", Path(name.replace("\\", "/")).name.lower())


# --------------------------------------------------------------------------- inputs


def load_msh(path: Path, msh_tool: Path) -> dict:
    """World-space triangles, per-corner UVs and per-face texture stems of the node meshes.

    The per-node meshes (vertex groups + indices) are what the game renders.
    The block-level face table does not index the same UV list reliably (the
    rocket tank, worm and scavenger come out scrambled), so it is not used.
    Node matrices are row-vector (right, up, front, posit) and local to the
    parent, like dotXSI.
    """
    sys.path.insert(0, str(msh_tool))
    import bz2msh  # noqa: WPS433

    msh = bz2msh.MSH(str(path))
    block = msh.blocks[0]
    nodes, triangles, uvs, textures = [], [], [], []
    children = {id(child) for mesh, _level in block.walk() for child in mesh.meshes}
    roots = [mesh for mesh, _level in block.walk() if id(mesh) not in children]

    def visit(mesh, parent: np.ndarray) -> None:
        world = np.array(list(mesh.matrix), dtype=np.float64) @ parent
        if len(mesh.vertex):
            nodes.append(mesh.name)
            positions = np.array([tuple(v.pos) for v in mesh.vertex], dtype=np.float64)
            positions = (np.c_[positions, np.ones(len(positions))] @ world)[:, :3]
            corner_uv = np.array([tuple(v.uv) for v in mesh.vertex], dtype=np.float64)
            start_index = start_vertex = 0
            for group in mesh.vert_groups:
                stem = _stem(group.texture.name) if group.texture else None
                end_index = start_index + group.index_count.value
                for i in range(start_index, end_index - 2, 3):
                    corner = [start_vertex + mesh.indices[i + k] for k in range(3)]
                    if max(corner) >= len(positions):
                        continue
                    triangles.append(positions[corner])
                    uvs.append(corner_uv[corner])
                    textures.append(stem)
                start_vertex += group.vert_count.value
                start_index = end_index
        for child in mesh.meshes:
            visit(child, world)

    for root in roots:
        visit(root, np.eye(4))
    return {
        "nodes": nodes,
        "triangles": np.array(triangles).reshape(-1, 3, 3),
        "uvs": np.array(uvs).reshape(-1, 3, 2),
        "textures": textures,
    }


def _load_image(path: Path) -> np.ndarray | None:
    if not path.is_file():
        return None
    if path.suffix.lower() == ".pic":
        rgba, info = softimage_pic.decode_pic_bytes(path.read_bytes())
        return np.frombuffer(rgba, np.uint8).reshape(int(info["height"]), int(info["width"]), 4)[..., :3] / 255.0
    from PIL import Image

    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), np.float64) / 255.0


def _sample(image: np.ndarray, uv: np.ndarray, flip_v: bool) -> np.ndarray:
    """Nearest-texel lookup with wrap; flip_v=True samples bottom-left UVs."""
    h, w = image.shape[:2]
    u = np.mod(uv[:, 0], 1.0)
    v = np.mod(uv[:, 1], 1.0)
    row = (1.0 - v) if flip_v else v
    return image[np.clip((row * h).astype(int), 0, h - 1), np.clip((u * w).astype(int), 0, w - 1)]


# --------------------------------------------------------------------------- comparison


AXIS_FLIPS = [np.array(signs, float) for signs in itertools.product((1, -1), repeat=3)]


def _closest_points(points: np.ndarray, triangles: np.ndarray, candidates: np.ndarray):
    """Closest point on each candidate triangle; returns (triangle index, barycentric)."""
    best_d = np.full(len(points), np.inf)
    best_t = np.zeros(len(points), int)
    best_w = np.zeros((len(points), 3))
    for k in range(candidates.shape[1]):
        tri = triangles[candidates[:, k]]
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        # project onto the plane, then clamp barycentrics (adequate for scoring)
        v0, v1, v2 = b - a, c - a, points - a
        d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
        d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
        denom = np.where(np.abs(d00 * d11 - d01 * d01) < 1e-18, 1e-18, d00 * d11 - d01 * d01)
        wv = (d11 * d20 - d01 * d21) / denom
        ww = (d00 * d21 - d01 * d20) / denom
        weights = np.clip(np.stack([1 - wv - ww, wv, ww], 1), 0, None)
        weights /= np.maximum(weights.sum(1, keepdims=True), 1e-12)
        q = (weights[:, :, None] * tri).sum(1)
        d = np.linalg.norm(points - q, axis=1)
        better = d < best_d
        best_d[better], best_t[better], best_w[better] = d[better], candidates[better, k], weights[better]
    return best_t, best_w, best_d


def compare_pair(msh_path: str, bundle: str, msh_tool: str, retail_root: str, xsi_override: str | None = None) -> dict:
    from scipy.spatial import cKDTree

    msh = load_msh(Path(msh_path), Path(msh_tool))
    xsi_path = Path(xsi_override) if xsi_override else next(iter(sorted((Path(bundle) / "engine").glob("*.xsi"))))
    ours = rc.load_xsi(xsi_path)
    result = {"msh": Path(msh_path).name, "bundle": Path(bundle).name, "msh_faces": int(len(msh["triangles"]))}
    if not len(msh["triangles"]) or not len(ours.positions):
        return {**result, "status": "no_geometry"}

    our_points = ours.positions.reshape(-1, 3)
    our_tree = cKDTree(our_points)
    size = float(np.linalg.norm(our_points.max(0) - our_points.min(0))) or 1.0

    # scale: the compiled .msh is uniformly scaled; match RMS radius about the centroid
    msh_points = msh["triangles"].reshape(-1, 3)
    msh_radius = float(np.sqrt(((msh_points - msh_points.mean(0)) ** 2).sum(1).mean())) or 1.0
    our_radius = float(np.sqrt(((our_points - our_points.mean(0)) ** 2).sum(1).mean())) or 1.0
    scale = msh_radius / our_radius
    # axis mapping: the sign flips minimizing the symmetric nearest-vertex distance
    best = None
    for flips in AXIS_FLIPS:
        mapped = msh_points * flips / scale
        forward = our_tree.query(mapped)[0].mean()
        backward = cKDTree(mapped).query(our_points)[0].mean()
        score = (forward + backward) / 2
        if best is None or score < best[0]:
            best = (score, flips)
    distance, flips = best
    result.update({"status": "ok", "axis_flips": flips.tolist(), "msh_scale": round(scale, 5), "geometry_mean_distance_rel": round(distance / size, 5)})

    # texture agreement at msh face centres
    centres = (msh["triangles"] * flips / scale).mean(axis=1)
    msh_uv = msh["uvs"].mean(axis=1)
    centroid_tree = cKDTree(ours.positions.mean(axis=1))
    _dist, candidates = centroid_tree.query(centres, k=min(8, len(ours.positions)))
    candidates = np.asarray(candidates).reshape(len(centres), -1)
    tri_index, weights, surface_distance = _closest_points(centres, ours.positions, candidates)
    near = surface_distance < 0.01 * size
    our_uv = (weights[:, :, None] * ours.uvs[tri_index]).sum(1)

    retail = texture_layers._retail_index()
    retail_images: dict[str, np.ndarray | None] = {}
    our_images: dict[str, np.ndarray | None] = {}
    theirs_rgb, ours_rgb = [], []
    unmatched_textures: set[str] = set()
    for face in np.nonzero(near)[0]:
        stem = msh["textures"][face]
        our_texture = ours.texture[tri_index[face]]
        if not stem or not our_texture:
            continue
        if stem not in retail_images:
            members = retail.get(stem) or []
            retail_images[stem] = _load_image(Path(retail_root) / members[0]) if members else None
        if our_texture not in our_images:
            our_images[our_texture] = _load_image(xsi_path.parent / our_texture)
        a, b = retail_images[stem], our_images[our_texture]
        if a is None or b is None:
            unmatched_textures.add(stem)
            continue
        theirs_rgb.append((stem, face))
        ours_rgb.append((our_texture, face))
    if not theirs_rgb:
        return {**result, "texture_samples": 0, "near_surface_fraction": round(float(near.mean()), 4), "unmatched_textures": sorted(unmatched_textures)}

    faces = np.array([f for _s, f in theirs_rgb])
    their_colour = np.concatenate([_sample(retail_images[s], msh_uv[[f]], flip_v=False) for s, f in theirs_rgb])
    # our UVs are Softimage bottom-left
    our_colour = np.concatenate([_sample(our_images[t], our_uv[[f]], flip_v=True) for t, f in ours_rgb])
    # the shipped msh V may be top-left or bottom-left: score both, keep the better
    their_colour_flipped = np.concatenate([_sample(retail_images[s], msh_uv[[f]], flip_v=True) for s, f in theirs_rgb])

    def agreement(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
        error = float(np.abs(x - y).mean())
        xc, yc = x - x.mean(0), y - y.mean(0)
        denominator = float(np.linalg.norm(xc) * np.linalg.norm(yc))
        return error, (float((xc * yc).sum() / denominator) if denominator else 0.0)

    top = agreement(their_colour, our_colour)
    bottom = agreement(their_colour_flipped, our_colour)
    (error, correlation), msh_v = (top, "top_left") if top[1] >= bottom[1] else (bottom, "bottom_left")
    per_texture = {}
    for stem in sorted({s for s, _f in theirs_rgb}):
        mask = np.array([s == stem for s, _f in theirs_rgb])
        chosen = their_colour if msh_v == "top_left" else their_colour_flipped
        e, c = agreement(chosen[mask], our_colour[mask])
        per_texture[stem] = {"samples": int(mask.sum()), "mean_abs_error": round(e, 4), "correlation": round(c, 4)}
    return {
        **result,
        "near_surface_fraction": round(float(near.mean()), 4),
        "texture_samples": int(len(faces)),
        "msh_uv_origin": msh_v,
        "colour_mean_abs_error": round(error, 4),
        "colour_correlation": round(correlation, 4),
        "per_texture": per_texture,
        "unmatched_textures": sorted(unmatched_textures),
    }


def pair_bundles(retail: Path, reconstructed: Path, msh_tool: Path, min_jaccard: float) -> list[tuple[str, str, float]]:
    bundles = {}
    for bundle in reconstructed.iterdir():
        report = bundle / "engine" / "xsi_export.json"
        if report.is_file():
            data = json.loads(report.read_text(encoding="utf-8"))
            bundles[str(bundle)] = {f["frame"].lower() for f in data["frames_detail"]} | {
                f["source_name"].lower() for f in data["frames_detail"]
            }
    pairs = []
    for msh_path in sorted(retail.rglob("*.msh")):
        try:
            nodes = {n.lower() for n in load_msh(msh_path, msh_tool)["nodes"]}
        except Exception:  # noqa: BLE001 - unreadable meshes are simply not paired
            continue
        if len(nodes) < 2:
            continue
        best = max(((len(nodes & names) / len(nodes | names), bundle) for bundle, names in bundles.items()), default=(0, None))
        if best[0] >= min_jaccard:
            pairs.append((str(msh_path), best[1], round(best[0], 3)))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retail", type=Path, required=True, help="folder with the extracted retail .msh files (data.pak)")
    parser.add_argument("--reconstructed", type=Path, default=Path("artifacts/reconstructed"))
    parser.add_argument("--msh-tool", type=Path, default=DEFAULT_MSH_TOOL)
    parser.add_argument("--retail-pictures", type=Path, default=texture_layers.RETAIL_PICTURES_ROOT)
    parser.add_argument("--min-jaccard", type=float, default=0.5)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    pairs = pair_bundles(args.retail, args.reconstructed, args.msh_tool, args.min_jaccard)
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(compare_pair, m, b, str(args.msh_tool), str(args.retail_pictures)) for m, b, _j in pairs]
        results = []
        for (m, b, jaccard), future in zip(pairs, futures):
            try:
                results.append({"node_jaccard": jaccard, **future.result()})
            except Exception as exc:  # noqa: BLE001
                results.append({"msh": Path(m).name, "bundle": Path(b).name, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
    for r in sorted(results, key=lambda r: -(r.get("colour_correlation") or -9)):
        print(
            f"{r['msh']:18} {r['bundle'][:46]:46} geo={r.get('geometry_mean_distance_rel')} "
            f"corr={r.get('colour_correlation')} err={r.get('colour_mean_abs_error')} n={r.get('texture_samples')} {r.get('status')}"
        )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

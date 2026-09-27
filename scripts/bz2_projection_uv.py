#!/usr/bin/env python3
"""Generate practical UVs for legacy Softimage projection types used by BZ2 assets.

This module is intentionally renderer-independent. It converts object-local
positions into fitted projection UVs for the five model-local projection types
observed on DSC relation-code-400 edges, then applies the source-correlated
SI_Texture2D U/V repeat counts, U/V scale+offset and source-pixel crop rectangle.

The operator correspondence is a working reconstruction table, not a claim that
all historical Softimage enum names have been recovered authoritatively:

    1 planar XY
    2 planar XZ
    3 planar YZ
    4 spherical
    5 cylindrical

The full supplied primary corpus contains 283 relation-code-400 edges, including
54 non-identity +90 matrix SRTs. Archive/source validation proves that the stored
rotation is a Softimage projection-support pose: for rotation-only model-local
code-400 polygon bindings, object coordinates are transformed by the serialized
siXYZ object-to-projection rotation before projection. Non-unit support scale/translation and
material-level code-401 matrix application remain separate evidence boundaries.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

WORKING_PROJECTION_TYPES = {
    1: "planar_xy",
    2: "planar_xz",
    3: "planar_yz",
    4: "spherical",
    5: "cylindrical",
}

EPSILON = 1.0e-9
MATRIX_IDENTITY_TOLERANCE = 1.0e-5


def _rotation_matrix_xyz(rotation):
    rx, ry, rz = (float(v) for v in rotation)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # Softimage default siXYZ: apply X, then Y, then Z => Rz @ Ry @ Rx.
    return (
        (cz*cy, cz*sy*sx - sz*cx, cz*sy*cx + sz*sx),
        (sz*cy, sz*sy*sx + cz*cx, sz*sy*cx - cz*sx),
        (-sy, cy*sx, cy*cx),
    )

def _transpose3(m):
    return tuple(tuple(m[j][i] for j in range(3)) for i in range(3))

def _mul3(m, p):
    x, y, z = (float(v) for v in p[:3])
    return tuple(m[i][0]*x + m[i][1]*y + m[i][2]*z for i in range(3))

def projection_rotation_supported(projection: dict, tolerance: float = MATRIX_IDENTITY_TOLERANCE) -> bool:
    """Allow corpus-observed rotation-only projection definitions.

    Both DSC 400 and 401 reference the same TXMP projection-definition layout.
    For 401 this helper is used only when geometry must generate a missing/all-zero
    CurrentUV source; authored nonzero 401 UVs remain preserved separately.
    """
    if int(projection.get("relation_code") or 0) not in {400, 401}:
        return False
    scale = projection.get("si_texture2d_matrix_scale_xyz") or [1.0, 1.0, 1.0]
    translation = projection.get("si_texture2d_matrix_translation_xyz") or [0.0, 0.0, 0.0]
    return (
        all(abs(float(v)-1.0) <= tolerance for v in scale)
        and all(abs(float(v)) <= tolerance for v in translation)
    )

def code400_rotation_supported(projection: dict, tolerance: float = MATRIX_IDENTITY_TOLERANCE) -> bool:
    """Compatibility predicate for the model-local relation-400 path."""
    return int(projection.get("relation_code") or 0) == 400 and projection_rotation_supported(projection, tolerance)

def projection_space_point(point, projection: dict):
    rotation = projection.get("si_texture2d_matrix_rotation_xyz_radians") or [0.0, 0.0, 0.0]
    if all(abs(float(v)) <= MATRIX_IDENTITY_TOLERANCE for v in rotation):
        return tuple(float(v) for v in point[:3])
    if not projection_rotation_supported(projection):
        raise ValueError("non-identity SI_Texture2D matrix is not proven for this projection-generation path")
    # The serialized rotation maps object coordinates into projection-local UVW.
    # face39 validates this direction geometrically: R*n becomes almost pure +Y
    # for a planar-XZ projection; transposing R produces the wrong support axis.
    return _mul3(_rotation_matrix_xyz(rotation), point)

def projection_space_bounds(bounds, projection: dict):
    minimum, maximum = bounds
    corners = [
        (x, y, z)
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    ]
    return bounds_from_points(projection_space_point(point, projection) for point in corners)


def prepare_projection_points(points, projection: dict):
    """Prepare a whole fitted support once, returning support-local points/bounds.

    PATCH: rotating an object's pre-rotation AABB and then bounding its corners
    overestimates the fitted support for irregular meshes. Corpus validation on
    all 21 non-identity class-4 code-400 edges found base-UV deltas up to 0.319.
    Transform the actual mesh vertices first, then derive the projection bounds.
    """
    values = [tuple(float(component) for component in point[:3]) for point in points]
    if not values:
        raise ValueError("cannot prepare projection support from zero points")
    if matrix_srt_is_identity(projection):
        return values, bounds_from_points(values)
    if not projection_rotation_supported(projection):
        raise ValueError("non-identity SI_Texture2D matrix is not proven for this projection-generation path")
    transformed = [projection_space_point(point, projection) for point in values]
    return transformed, bounds_from_points(transformed)

def project_prepared_polygon(points, bounds, projection: dict):
    """Project points already expressed in projection-support local coordinates."""
    code = int(projection.get("projection_or_mapping_code_candidate") or 0)
    if code not in WORKING_PROJECTION_TYPES:
        raise ValueError(f"unsupported working projection code {code}")
    uvs = [base_projection_uv(point, bounds, code) for point in points]
    if code in {4, 5}:
        uvs = unwrap_angular_seam(uvs)
    repeats = projection.get("si_texture2d_repeat_uv")
    scale = projection.get("si_texture2d_uv_scale")
    offset = projection.get("si_texture2d_uv_offset")
    crop = projection.get("crop_rect_pixels_raw")
    image_size = (
        [projection.get("width"), projection.get("height")]
        if projection.get("width") and projection.get("height")
        else None
    )
    return [
        apply_crop(
            apply_uv_scale_offset(apply_uv_repeats(uv, repeats), scale, offset),
            crop,
            image_size,
        )
        for uv in uvs
    ]


def apply_current_uv_effects(uvw, projection: dict):
    """Apply corpus-proven live SI_Texture2D effects to an authored CurrentUV.

    Softimage Texture.GetTransformValues starts from Material.CurrentUV and applies
    projection-definition transformation plus texture/image effects. BZ2 HRC stores
    only U/V, so the preserved polygon source is promoted to (U,V,0). The supplied
    code-401 corpus has rotation-only matrix state: unit matrix scale and zero matrix
    translation. Source UV data is never overwritten; callers create derived UVs.
    """
    u, v = float(uvw[0]), float(uvw[1])
    w = float(uvw[2]) if len(uvw) >= 3 else 0.0
    # The texture-matrix rotation is NOT applied to a stored CurrentUV: the
    # stored UVs already include it. Anchor: the shipped retail .msh models
    # (bz2_msh_compare.py). With the rotation applied, the pi-Y mirror left
    # the worm, Scion satchel, grenade launcher and APC wreck textured
    # mirror-reversed (colour correlation -0.05 / -0.09 / 0.33 / 0.27);
    # without it they reach 0.90 / 0.83 / 0.99 / 0.94. 22 of 74 textured
    # shipped models improve and none get worse; median 0.58 -> 0.71. The
    # BZ2 engines likewise ignore SI_Texture2D matrices. Generated
    # projections still apply the matrix (projection_space_point).
    if not projection_rotation_supported(projection) and not matrix_srt_is_identity(projection):
        raise ValueError("non-identity CurrentUV matrix scale/translation is not corpus-proven")
    result = apply_uv_repeats((u, v), projection.get("si_texture2d_repeat_uv"))
    result = apply_uv_scale_offset(
        result,
        projection.get("si_texture2d_uv_scale"),
        projection.get("si_texture2d_uv_offset"),
    )
    image_size = (
        [projection.get("width"), projection.get("height")]
        if projection.get("width") and projection.get("height")
        else None
    )
    result = apply_crop(result, projection.get("crop_rect_pixels_raw"), image_size)
    return result[0], result[1], w


LIVE_PLANAR_CODES = {1, 2, 3}


def prefers_live_projection(layer: dict) -> bool:
    """True when a layer's live planar projection overrides the HRC's stored UVs.

    Stored class-4 UVs are a snapshot. On NewTank's merged hull
    (bmerge5_default_2) and fins they no longer match the planar projections
    Softimage rendered: generating codes 1-3 live took the per-part
    render-compare error from 0.75 to 0.53 (hull) and 0.99 to 0.55 (fin)
    against TANK.1, and left all 20 other aligned reference scenes unchanged.
    Spherical/cylindrical layers keep their stored UVs: regenerating them broke
    the Pluto walker (body error 0.38 -> 0.92). The stored UVs stay in the
    glTF as the preserved source UV set.
    """
    code = int(layer.get("projection_or_mapping_code_candidate") or 0)
    return code in LIVE_PLANAR_CODES and (matrix_srt_is_identity(layer) or projection_rotation_supported(layer))


def projection_type_name(code: int | None) -> str | None:
    return WORKING_PROJECTION_TYPES.get(int(code)) if code is not None else None


def bounds_from_points(points: Iterable[Sequence[float]]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    values = [tuple(float(component) for component in point[:3]) for point in points]
    if not values:
        raise ValueError("cannot compute projection bounds from zero points")
    return (
        tuple(min(point[axis] for point in values) for axis in range(3)),
        tuple(max(point[axis] for point in values) for axis in range(3)),
    )


def _unit(value: float, minimum: float, maximum: float) -> float:
    span = maximum - minimum
    return (value - minimum) / span if abs(span) > EPSILON else 0.5


def normalized_xyz(point: Sequence[float], bounds) -> tuple[float, float, float]:
    minimum, maximum = bounds
    return tuple(
        _unit(float(point[axis]), float(minimum[axis]), float(maximum[axis]))
        for axis in range(3)
    )


def base_projection_uv(point: Sequence[float], bounds, projection_code: int) -> tuple[float, float]:
    """Project one object-local point using a support fitted to object bounds.

    Softimage's default projection fills its support. The angular projections
    use +Y as the pole/axis, matching the documented spherical/cylindrical
    convention. V increases from the support's bottom toward +Y.
    """
    code = int(projection_code)
    if code not in WORKING_PROJECTION_TYPES:
        raise ValueError(f"unsupported working projection code {code}")

    x, y, z = normalized_xyz(point, bounds)
    if code == 1:
        return x, y
    if code == 2:
        return x, z
    if code == 3:
        # Looking down X: the picture's horizontal runs along Z, its vertical
        # along Y. Anchor: adconcept/RENDER_PICTURES/pluto.1 -- the corridor
        # walls' cementwall bands (pipes above, grille at the floor) run along
        # the corridor; (y, z) turned them 90 degrees. Render-compare edge
        # alignment 0.43 -> 0.67 (bz2_render_compare.py).
        return z, y

    # Fit angular supports to the object's local bounding box. Converting each
    # axis to [-1,1] keeps non-uniform object dimensions from changing the seam
    # or pole locations merely because the source mesh is elongated.
    cx, cy, cz = 2.0 * x - 1.0, 2.0 * y - 1.0, 2.0 * z - 1.0
    u = 0.5 + math.atan2(cx, cz) / (2.0 * math.pi)
    if code == 5:
        return u, y

    radius = math.sqrt(cx * cx + cy * cy + cz * cz)
    if radius <= EPSILON:
        return 0.5, 0.5
    v = 0.5 + math.asin(max(-1.0, min(1.0, cy / radius))) / math.pi
    return u, v


def unwrap_angular_seam(uvs: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
    """Keep one polygon from interpolating the long way across the U seam."""
    output = [(float(uv[0]), float(uv[1])) for uv in uvs]
    if not output:
        return output
    us = [uv[0] for uv in output]
    if max(us) - min(us) <= 0.5:
        return output
    return [(u + 1.0 if u < 0.5 else u, v) for u, v in output]


def apply_uv_repeats(uv: Sequence[float], repeats=None) -> tuple[float, float]:
    """Apply recovered legacy URepeat/VRepeat factors.

    Softimage documents a repeat factor of 2 as shrinking a texture so that it
    fits twice in the normalized interval. The archival TXMP values immediately
    preceding the confirmed +6 scale/offset block correlate exactly with authored
    tiling cases (20x20 bump, 4x1 wall, 1x4 floor/ceiling, 6x6 arch, etc.).
    """
    repeats = repeats if isinstance(repeats, (list, tuple)) and len(repeats) >= 2 else (1.0, 1.0)
    return float(uv[0]) * float(repeats[0]), float(uv[1]) * float(repeats[1])


def _safe_scale(value) -> float:
    value = float(value)
    return value if abs(value) > EPSILON else 1.0


def apply_uv_scale_offset(uv: Sequence[float], scale=None, offset=None) -> tuple[float, float]:
    """Apply SI_Texture2D UScale/VScale and UOffset/VOffset (TXMP +6).

    Softimage scales and moves the *texture*, so texture lookup coordinates are
    ``(uv - offset) / scale`` (the counterpart of URepeat, which shrinks it),
    with V measured from the top of the picture.
    Anchor: the final NewTank hull layers (tank2-t2d2/t2d7/t2d8/t2d90) map to
    picture windows such as u 0.306-0.694 (centred) and u 0.627-1.000 / v
    0.475-1.000 (ending exactly at the picture edge) under this direction,
    reproducing the emblem deck of NewTank/RENDER_PICTURES/TANK.1.pic, while
    ``uv * scale + offset`` tiles the whole picture 2.7x across the deck.
    """
    scale = scale if isinstance(scale, (list, tuple)) and len(scale) >= 2 else (1.0, 1.0)
    offset = offset if isinstance(offset, (list, tuple)) and len(offset) >= 2 else (0.0, 0.0)
    # The window is measured in picture space from the top row (PIC scanline
    # order), so V is flipped around the placement: identity windows are
    # unchanged, and the NewTank nose plate (v window 0.741-1.0) selects the
    # emblem strip at the bottom of tank.pic rather than the engine pods.
    return (
        (float(uv[0]) - float(offset[0])) / _safe_scale(scale[0]),
        1.0 - ((1.0 - float(uv[1])) - float(offset[1])) / _safe_scale(scale[1]),
    )


def effective_crop(crop: dict | None, width: int, height: int) -> tuple[float, float, float, float] | None:
    """The pixel window (x0, x1, y0, y1) Softimage samples, or None for the whole picture.

    A crop is stored in the pixels of the picture as it was when the crop was
    authored. When the picture file was later replaced by a resized or
    extended version, a crop starting at 0,0 that no longer matches the
    picture is a stale full-frame crop and selects the whole current picture.
    Anchors (render-compare, 2026-09-26): walker_final/walker.1 edge alignment
    0.30 -> 0.49 (tankturret1 590x167 crop on the 1000x283 picture), and the
    NewTank nose deck error 0.65 -> 0.52 (tank.pic 1325-row crop on 1513 rows).
    Clamping the stale crop, or counting its rows from the top, both
    score worse. Crops that do not start at 0,0 are real windows and are
    clamped to the picture.
    """
    if not crop or width <= 1 or height <= 1:
        return None
    x0, x1 = float(crop.get("x0", 0)), float(crop.get("x1", width - 1))
    y0, y1 = float(crop.get("y0", 0)), float(crop.get("y1", height - 1))
    if x0 == 0 and y0 == 0 and (int(x1) + 1 != width or int(y1) + 1 != height):
        return None
    x0, x1 = min(max(x0, 0.0), width - 1.0), min(max(x1, 0.0), width - 1.0)
    y0, y1 = min(max(y0, 0.0), height - 1.0), min(max(y1, 0.0), height - 1.0)
    return x0, x1, y0, y1


def apply_crop(uv: Sequence[float], crop: dict | None, image_size: Sequence[int] | None) -> tuple[float, float]:
    """Map normalized UV into an inclusive source-pixel crop rectangle.

    Full-image rectangles such as 0..W-1 / 0..H-1 remain an identity mapping.
    Crop rows count from the picture's bottom (Softimage's texture pivot);
    counting them from the top scored worse against the archived renders.
    """
    if not crop or not image_size or len(image_size) < 2:
        return float(uv[0]), float(uv[1])
    width, height = int(image_size[0]), int(image_size[1])
    window = effective_crop(crop, width, height)
    if window is None:
        return float(uv[0]), float(uv[1])
    x0, x1, y0, y1 = window
    return (
        (x0 + float(uv[0]) * (x1 - x0)) / float(width - 1),
        (y0 + float(uv[1]) * (y1 - y0)) / float(height - 1),
    )


def matrix_srt_is_identity(
    projection: dict,
    tolerance: float = MATRIX_IDENTITY_TOLERANCE,
) -> bool:
    """Treat sub-1e-5 SRT residue as numerical decomposition noise.

    A relation-aware code-401 corpus pass found 133 records with byte-nonzero
    rotation components, but four are only ~1e-7..1e-6 radians. At 1e-5 the
    meaningful frontier is 129 authored rotations; no corresponding non-unit
    matrix scale or translation is present. This tolerance prevents harmless
    decomposition residue from needlessly deferring otherwise identity layers.
    """
    rotation = projection.get("si_texture2d_matrix_rotation_xyz_radians")
    scale = projection.get("si_texture2d_matrix_scale_xyz")
    translation = projection.get("si_texture2d_matrix_translation_xyz")
    if rotation is None and scale is None and translation is None:
        return True
    rotation = rotation or [0.0, 0.0, 0.0]
    scale = scale or [1.0, 1.0, 1.0]
    translation = translation or [0.0, 0.0, 0.0]
    return (
        all(abs(float(value)) <= tolerance for value in rotation)
        and all(abs(float(value) - 1.0) <= tolerance for value in scale)
        and all(abs(float(value)) <= tolerance for value in translation)
    )


def project_polygon(points: Sequence[Sequence[float]], bounds, projection: dict) -> list[tuple[float, float]]:
    """Compatibility projection API for one polygon.

    Blender's production path uses ``prepare_projection_points`` on the complete
    mesh and ``project_prepared_polygon`` so fitted bounds are exact. This helper
    retains the older bounds argument for standalone callers.
    """
    code = int(projection.get("projection_or_mapping_code_candidate") or 0)
    if code not in WORKING_PROJECTION_TYPES:
        raise ValueError(f"unsupported working projection code {code}")
    if not matrix_srt_is_identity(projection):
        if not code400_rotation_supported(projection):
            raise ValueError("non-identity SI_Texture2D matrix SRT is not promoted for this binding path")
        bounds = projection_space_bounds(bounds, projection)
        points = [projection_space_point(point, projection) for point in points]
    return project_prepared_polygon(points, bounds, projection)


def self_test() -> None:
    bounds = ((-1.0, -2.0, -3.0), (1.0, 2.0, 3.0))
    assert base_projection_uv((-1.0, -2.0, -3.0), bounds, 1) == (0.0, 0.0)
    assert base_projection_uv((1.0, 2.0, 3.0), bounds, 2) == (1.0, 1.0)
    assert base_projection_uv((0.0, 0.0, 0.0), bounds, 3) == (0.5, 0.5)
    repeated = apply_uv_repeats((0.25, 0.5), (4, 2))
    assert repeated == (1.0, 1.0)
    transformed = apply_uv_scale_offset((0.25, 0.5), (2.0, -1.0), (0.1, 0.75))
    assert abs(transformed[0] - 0.6) < 1.0e-9
    assert abs(transformed[1] - 0.25) < 1.0e-9
    cropped = apply_crop((1.0, 1.0), {"x0": 0, "x1": 482, "y0": 0, "y1": 362}, (483, 363))
    assert all(abs(value - 1.0) < 1.0e-9 for value in cropped)
    noisy_identity = {
        "si_texture2d_matrix_rotation_xyz_radians": [1.0e-6, -3.0e-7, 0.0],
        "si_texture2d_matrix_scale_xyz": [1.0, 1.0, 1.0],
        "si_texture2d_matrix_translation_xyz": [0.0, 0.0, 0.0],
    }
    assert matrix_srt_is_identity(noisy_identity)
    projection = {
        "projection_or_mapping_code_candidate": 2,
        "si_texture2d_repeat_uv": [2, 3],
        "si_texture2d_uv_scale": [1.0, 1.0],
        "si_texture2d_uv_offset": [0.0, 0.0],
        "crop_rect_pixels_raw": {"x0": 0, "x1": 482, "y0": 0, "y1": 362},
        "width": 483,
        "height": 363,
        "si_texture2d_matrix_rotation_xyz_radians": [0.0, 0.0, 0.0],
        "si_texture2d_matrix_scale_xyz": [1.0, 1.0, 1.0],
        "si_texture2d_matrix_translation_xyz": [0.0, 0.0, 0.0],
    }
    assert project_polygon([(-1.0, 0.0, -3.0), (1.0, 0.0, 3.0)], bounds, projection) == [(0.0, 0.0), (2.0, 3.0)]


if __name__ == "__main__":
    self_test()
    print("bz2_projection_uv self-test: ok")

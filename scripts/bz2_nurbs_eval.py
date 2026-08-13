#!/usr/bin/env python3
"""Rational NURBS evaluation and validation-quality OBJ tessellation for BZ2 SI3D records."""
from __future__ import annotations

import re
from pathlib import Path


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "nurbs"


def _find_span(count: int, degree: int, u: float, knots: list[float]) -> int:
    n = count - 1
    if u >= knots[n + 1]:
        return n
    if u <= knots[degree]:
        for span in range(degree, n + 1):
            if knots[span + 1] > u:
                return span
        return n
    low, high = degree, n + 1
    mid = (low + high) // 2
    while u < knots[mid] or u >= knots[mid + 1]:
        if u < knots[mid]:
            high = mid
        else:
            low = mid
        mid = (low + high) // 2
    return mid


def _basis(span: int, u: float, degree: int, knots: list[float]) -> list[float]:
    values = [0.0] * (degree + 1)
    left = [0.0] * (degree + 1)
    right = [0.0] * (degree + 1)
    values[0] = 1.0
    for j in range(1, degree + 1):
        left[j] = u - knots[span + 1 - j]
        right[j] = knots[span + j] - u
        saved = 0.0
        for r in range(j):
            denom = right[r + 1] + left[j - r]
            temp = 0.0 if abs(denom) < 1e-15 else values[r] / denom
            values[r] = saved + right[r + 1] * temp
            saved = left[j - r] * temp
        values[j] = saved
    return values


def _samples(knots: list[float], degree: int, count: int, steps: int, closed: bool) -> list[float]:
    start, end = knots[degree], knots[count]
    if steps < 2:
        return [start]
    denom = steps if closed else steps - 1
    return [start + (end - start) * i / denom for i in range(steps)]


def _curve_data(record: dict):
    points = [p["xyzw"] for p in record["control_points"]]
    degree = int(record["degree_inferred"])
    closed = bool(record.get("closed"))
    conv = record.get("knot_conversion") or {}
    knots = record.get("knots_standard") or record.get("knots_standard_open")
    if not knots:
        raise ValueError("missing standardized curve knot vector")
    wrap = int(conv.get("control_wrap_count", degree if closed else 0))
    if closed and wrap:
        points = [*points, *[list(p) for p in points[:wrap]]]
    return points, knots, degree, closed


def _surface_data(record: dict):
    points = [p["xyzw"] for p in record["control_points"]]
    cu, cv = int(record["control_count_u"]), int(record["control_count_v"])
    du, dv = int(record["degree_u_inferred"]), int(record["degree_v_inferred"])
    closed_u, closed_v = bool(record.get("closed_u")), bool(record.get("closed_v"))
    conv_u, conv_v = record.get("knot_conversion_u") or {}, record.get("knot_conversion_v") or {}
    ku = record.get("knots_u_standard") or record.get("knots_u_standard_open")
    kv = record.get("knots_v_standard") or record.get("knots_v_standard_open")
    if not ku or not kv:
        raise ValueError("missing standardized surface knot vector")
    rows = [points[v * cu:(v + 1) * cu] for v in range(cv)]
    wu = int(conv_u.get("control_wrap_count", du if closed_u else 0))
    wv = int(conv_v.get("control_wrap_count", dv if closed_v else 0))
    if closed_u and wu:
        rows = [[*row, *[list(p) for p in row[:wu]]] for row in rows]
        cu += wu
    if closed_v and wv:
        rows = [*rows, *[[list(p) for p in row] for row in rows[:wv]]]
        cv += wv
    return [p for row in rows for p in row], cu, cv, ku, kv, du, dv, closed_u, closed_v


def evaluate_curve(record: dict, u: float) -> tuple[float, float, float]:
    points, knots, degree, _ = _curve_data(record)
    span = _find_span(len(points), degree, u, knots)
    basis = _basis(span, u, degree, knots)
    num = [0.0, 0.0, 0.0]
    den = 0.0
    for j, value in enumerate(basis):
        p = points[span - degree + j]
        wb = value * p[3]
        for axis in range(3):
            num[axis] += wb * p[axis]
        den += wb
    if abs(den) < 1e-15:
        raise ValueError("zero rational curve denominator")
    return tuple(value / den for value in num)


def evaluate_surface(record: dict, u: float, v: float) -> tuple[float, float, float]:
    points, cu, cv, ku, kv, du, dv, _, _ = _surface_data(record)
    su, sv = _find_span(cu, du, u, ku), _find_span(cv, dv, v, kv)
    bu, bv = _basis(su, u, du, ku), _basis(sv, v, dv, kv)
    num = [0.0, 0.0, 0.0]
    den = 0.0
    for lv, vb in enumerate(bv):
        iv = sv - dv + lv
        for lu, ub in enumerate(bu):
            iu = su - du + lu
            p = points[iv * cu + iu]
            wb = ub * vb * p[3]
            for axis in range(3):
                num[axis] += wb * p[axis]
            den += wb
    if abs(den) < 1e-15:
        raise ValueError("zero rational surface denominator")
    return tuple(value / den for value in num)


def _sample_trim(trim: dict, steps: int = 256) -> list[tuple[float, float]]:
    points, knots, degree, closed = _curve_data(trim)
    values = _samples(knots, degree, len(points), steps, closed)
    return [(p[0], p[1]) for p in (evaluate_curve(trim, value) for value in values)]


def _inside(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            denom = yj - yi
            xcross = xi + (y - yi) * (xj - xi) / denom if abs(denom) > 1e-15 else xi
            if x < xcross:
                inside = not inside
        j = i
    return inside


def _trim_loops(record: dict):
    boundaries, holes = [], []
    for trim in (record.get("trim_section") or {}).get("trims", []):
        if not (trim.get("uv_space_candidate") and trim.get("reconstruction_ready") and trim.get("closed")):
            continue
        (boundaries if trim.get("is_boundary_raw") else holes).append(_sample_trim(trim))
    return boundaries, holes


def _keep_uv(uv, boundaries, holes) -> bool:
    return (not boundaries or any(_inside(uv, loop) for loop in boundaries)) and not any(_inside(uv, loop) for loop in holes)


def _mid(a: float, b: float, start: float, end: float, closed: bool) -> float:
    if not closed or b >= a:
        return (a + b) * 0.5
    value = (a + b + (end - start)) * 0.5
    return value - (end - start) if value >= end else value


def write_curve_obj(path: Path, record: dict, steps: int) -> dict:
    points, knots, degree, closed = _curve_data(record)
    params = _samples(knots, degree, len(points), steps, closed)
    verts = [evaluate_curve(record, u) for u in params]
    lines = [f"o {_sanitize(path.stem)}", *[f"v {x:.9f} {y:.9f} {z:.9f}" for x, y, z in verts]]
    ids = list(range(1, len(verts) + 1))
    if closed and ids:
        ids.append(1)
    lines.append("l " + " ".join(map(str, ids)))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"vertices": len(verts), "segments": len(verts) if closed else max(0, len(verts) - 1), "closed": closed}


def write_surface_obj(path: Path, record: dict, steps_u: int, steps_v: int) -> dict:
    _, cu, cv, ku, kv, du, dv, closed_u, closed_v = _surface_data(record)
    us = _samples(ku, du, cu, steps_u, closed_u)
    vs = _samples(kv, dv, cv, steps_v, closed_v)
    verts = [evaluate_surface(record, u, v) for v in vs for u in us]
    lines = [f"o {_sanitize(path.stem)}", *[f"v {x:.9f} {y:.9f} {z:.9f}" for x, y, z in verts]]
    boundaries, holes = _trim_loops(record)
    apply_trim = bool(boundaries or holes)
    ud, vd = (ku[du], ku[cu]), (kv[dv], kv[cv])
    uc, vc = (steps_u if closed_u else steps_u - 1), (steps_v if closed_v else steps_v - 1)
    emitted = clipped = 0
    for v in range(vc):
        vn = (v + 1) % steps_v
        vm = _mid(vs[v], vs[vn], *vd, closed_v)
        for u in range(uc):
            un = (u + 1) % steps_u
            um = _mid(us[u], us[un], *ud, closed_u)
            if apply_trim and not _keep_uv((um, vm), boundaries, holes):
                clipped += 1
                continue
            a, b = v * steps_u + u + 1, v * steps_u + un + 1
            c, d = vn * steps_u + un + 1, vn * steps_u + u + 1
            lines.append(f"f {a} {b} {c} {d}")
            emitted += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "vertices": len(verts), "quads": emitted, "quads_clipped_by_trim": clipped,
        "steps_u": steps_u, "steps_v": steps_v, "closed_u": closed_u, "closed_v": closed_v,
        "trim_status": "applied_uv_centroid_clip" if apply_trim else "none",
        "trim_boundary_loops": len(boundaries), "trim_hole_loops": len(holes),
    }

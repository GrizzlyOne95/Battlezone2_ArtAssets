#!/usr/bin/env python3
"""Apply the corpus-backed class-1 type-3 B-spline ROOT evaluator."""
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"patch anchor missing in {path}: {old[:180]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "scripts/bz2_hrc_root_special_geometry.py"
replace_once(
    path,
    '''Current proven subtype: outer class-1 primitive kind 2, serialized as a regular\nu-count by v-count XYZ lattice. This is intentionally ROOT-only: nested class-1\nrecords are often Softimage construction/history objects and must not be emitted\nas duplicate render meshes merely because they share a class id.\n''',
    '''Proven ROOT handling currently covers class-1 surface type 2 as the historical\ncontrol-cage approximation plus class-1 surface type 3 as a uniform cubic B-spline\ntensor surface. Type-3 evaluation is archive-backed by the 5x4 open/open movie\nsoldier patch, source Step fields, 183-record class-1 envelope census, and complete\ndownstream scene reconstruction. This is intentionally ROOT-only: nested class-1\nrecords are often Softimage construction/history objects and must not be emitted\nas duplicate render meshes merely because they share a class id.\n''',
)

p = Path(path)
text = p.read_text(encoding="utf-8")
start = text.index("def _decode_class1_grid")
end = text.index("\ndef _append_chunk", start)
new_function = r'''def _cubic_bspline_basis(t: float) -> tuple[float, float, float, float]:
    """Uniform cubic B-spline basis for one four-control-point span."""
    one = 1.0 - t
    return (
        (one * one * one) / 6.0,
        (3.0 * t * t * t - 6.0 * t * t + 4.0) / 6.0,
        (-3.0 * t * t * t + 3.0 * t * t + 3.0 * t + 1.0) / 6.0,
        (t * t * t) / 6.0,
    )


def _decode_class1_grid(data: bytes, outer: dict) -> dict | None:
    if outer.get("class_id") != 1:
        return None
    offset = int(outer["payload_offset"])
    if offset + 6 > len(data):
        return None
    primitive_kind, u_count, v_count = struct.unpack_from(">HHH", data, offset)
    if primitive_kind not in {2, 3}:
        return None
    minimum_count = 4 if primitive_kind == 3 else 2
    if not (minimum_count <= u_count <= 4096 and minimum_count <= v_count <= 4096):
        return None
    control_count = u_count * v_count
    control_end = offset + 6 + control_count * 12
    if control_count > 2_000_000 or control_end > len(data):
        return None
    values = struct.unpack_from(f">{control_count * 3}f", data, offset + 6)
    controls = [tuple(values[index : index + 3]) for index in range(0, len(values), 3)]
    if not all(math.isfinite(value) and abs(value) < 1.0e9 for point in controls for value in point):
        return None

    if primitive_kind == 2:
        # Preserve the existing type-2 behavior until Cardinal interpolation and
        # closed-boundary semantics are independently validated. The source fields
        # are retained by the class-1 census tooling; this remains an explicit cage
        # approximation rather than being mislabeled as the exact patch evaluator.
        vertices = controls
        sample_u_count, sample_v_count = u_count, v_count
        evaluator = "control_cage"
        u_step = v_step = None
    else:
        # The archive contains three type-3 records, all 5x4, open/open, tension
        # 0.5 and Step 3. Autodesk's surface-type ordering and Softimage's own
        # B-spline behavior align type 3 with B-spline. The target movie-soldier
        # ROOT completes every downstream reconstruction stage with this evaluator.
        if control_end + 20 > len(data):
            return None
        u_closed, v_closed = struct.unpack_from(">HH", data, control_end)
        _u_tension, _v_tension = struct.unpack_from(">ff", data, control_end + 4)
        u_step, v_step = struct.unpack_from(">HH", data, control_end + 12)
        if u_closed or v_closed or u_step < 1 or v_step < 1:
            # No type-3 closed-direction corpus case exists yet. Refuse to guess
            # periodic endpoint semantics until a source example establishes it.
            return None
        u_spans = u_count - 3
        v_spans = v_count - 3
        if u_spans < 1 or v_spans < 1:
            return None
        sample_u_count = u_spans * u_step + 1
        sample_v_count = v_spans * v_step + 1
        vertices = []
        for sample_u in range(sample_u_count):
            if sample_u == sample_u_count - 1:
                u_span, tu = u_spans - 1, 1.0
            else:
                position = sample_u / u_step
                u_span = int(position)
                tu = position - u_span
            basis_u = _cubic_bspline_basis(tu)
            for sample_v in range(sample_v_count):
                if sample_v == sample_v_count - 1:
                    v_span, tv = v_spans - 1, 1.0
                else:
                    position = sample_v / v_step
                    v_span = int(position)
                    tv = position - v_span
                basis_v = _cubic_bspline_basis(tv)
                xyz = [0.0, 0.0, 0.0]
                for local_u in range(4):
                    for local_v in range(4):
                        weight = basis_u[local_u] * basis_v[local_v]
                        point = controls[(u_span + local_u) * v_count + (v_span + local_v)]
                        for axis in range(3):
                            xyz[axis] += weight * point[axis]
                vertices.append(tuple(xyz))
        evaluator = "uniform_cubic_bspline"

    indices: list[int] = []
    normal_accum = [[0.0, 0.0, 0.0] for _ in vertices]

    def cross(a: tuple[float, float, float], b: tuple[float, float, float]):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    # Row-major sampled lattice. This winding matches the prior type-2 output.
    for u in range(sample_u_count - 1):
        for v in range(sample_v_count - 1):
            a = u * sample_v_count + v
            b = (u + 1) * sample_v_count + v
            c = (u + 1) * sample_v_count + v + 1
            d = u * sample_v_count + v + 1
            for triangle in ((a, d, c), (a, c, b)):
                indices.extend(triangle)
                p0, p1, p2 = [vertices[index] for index in triangle]
                edge1 = tuple(p1[axis] - p0[axis] for axis in range(3))
                edge2 = tuple(p2[axis] - p0[axis] for axis in range(3))
                face_normal = cross(edge1, edge2)
                for index in triangle:
                    for axis in range(3):
                        normal_accum[index][axis] += face_normal[axis]

    normals = []
    for vector in normal_accum:
        length = math.sqrt(sum(value * value for value in vector))
        normals.append(
            tuple(value / length for value in vector) if length > 1.0e-15 else (0.0, 1.0, 0.0)
        )
    return {
        "primitive_kind": primitive_kind,
        "surface_type_code": primitive_kind,
        "u_count": u_count,
        "v_count": v_count,
        "sample_u_count": sample_u_count,
        "sample_v_count": sample_v_count,
        "u_step": u_step,
        "v_step": v_step,
        "evaluator": evaluator,
        "vertices": vertices,
        "normals": normals,
        "indices": indices,
    }
'''
p.write_text(text[:start] + new_function + text[end:], encoding="utf-8")

replace_once(
    path,
    '''                    "primitive_kind": grid["primitive_kind"],\n                    "u_count": grid["u_count"],\n                    "v_count": grid["v_count"],\n                    "texture_coordinates": "projection_required_no_baked_uv",\n''',
    '''                    "primitive_kind": grid["primitive_kind"],\n                    "surface_type_code": grid["surface_type_code"],\n                    "evaluator": grid["evaluator"],\n                    "u_count": grid["u_count"],\n                    "v_count": grid["v_count"],\n                    "sample_u_count": grid["sample_u_count"],\n                    "sample_v_count": grid["sample_v_count"],\n                    "u_step": grid["u_step"],\n                    "v_step": grid["v_step"],\n                    "texture_coordinates": "projection_required_no_baked_uv",\n''',
)
replace_once(
    path,
    '''                "u_count": grid["u_count"],\n                "v_count": grid["v_count"],\n                "vertex_count": len(vertices),\n''',
    '''                "surface_type_code": grid["surface_type_code"],\n                "evaluator": grid["evaluator"],\n                "u_count": grid["u_count"],\n                "v_count": grid["v_count"],\n                "sample_u_count": grid["sample_u_count"],\n                "sample_v_count": grid["sample_v_count"],\n                "u_step": grid["u_step"],\n                "v_step": grid["v_step"],\n                "vertex_count": len(vertices),\n''',
)
replace_once(
    path,
    '''        "notes": [\n            "only outer/ROOT class-1 primitive-kind-2 lattices are emitted",\n            "nested class-1 construction/history nodes remain non-rendering hierarchy objects",\n            "no TEXCOORD_0 is generated because the reference grids use model-local Softimage texture projection state",\n        ],\n''',
    '''        "notes": [\n            "outer/ROOT class-1 surface type 2 retains the historical control-cage approximation pending exact Cardinal/closure validation",\n            "outer/ROOT class-1 surface type 3 is evaluated as an open uniform cubic B-spline using the serialized U/V Step values",\n            "nested class-1 construction/history nodes remain non-rendering hierarchy objects",\n            "no TEXCOORD_0 is fabricated; class-1 source texture coordinates remain projection-dependent unless independently recovered",\n        ],\n''',
)

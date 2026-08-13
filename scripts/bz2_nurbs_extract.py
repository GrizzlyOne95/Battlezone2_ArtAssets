#!/usr/bin/env python3
"""Corpus-wide exporter for decoded Battlezone 2 Softimage|3D NURBS HRC records.

Scans direct HRC files and HRC members inside embedded ZIP archives, decodes proven
SI3D curve/surface records with ``bz2_nurbs_probe.py``, and writes validation-quality
OBJ derivatives plus a machine-readable index for later DSC scene integration.

The recovered rational control points, weights, knot vectors, closure flags, parameter
ranges, and trim curves remain the preservation source of truth. OBJ trim boundaries
currently use conservative UV face-centroid clipping and are explicitly derivative.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath


def _load_sibling(name: str):
    path = Path(__file__).with_name(name)
    module_name = path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_sibling("bz2_nurbs_probe.py")
evaluator = _load_sibling("bz2_nurbs_eval.py")
hrc_tree = _load_sibling("bz2_hrc_tree_probe.py")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize_component(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return value or "unnamed"


def unique_output_name(name: str, offset: int, used: set[str]) -> str:
    base = sanitize_component(name)
    candidate = f"{base}.obj"
    if candidate.lower() not in used:
        used.add(candidate.lower())
        return candidate
    candidate = f"{base}__0x{offset:X}.obj"
    suffix = 1
    while candidate.lower() in used:
        candidate = f"{base}__0x{offset:X}_{suffix}.obj"
        suffix += 1
    used.add(candidate.lower())
    return candidate


def logical_output_dir(logical_source: str, output_root: Path) -> Path:
    rel = PurePosixPath(logical_source)
    return output_root.joinpath(*rel.parent.parts) / sanitize_component(rel.name)


def decode_hrc_bytes(data: bytes) -> tuple[list[tuple[object, dict]], list[dict]]:
    anchors, rejected = probe.discover_parametric_anchors(data)
    decoded: list[tuple[object, dict]] = []
    failures: list[dict] = []
    for anchor in anchors:
        record = probe.decode_parametric_record(data, anchor)
        if record is None:
            failures.append({"name": anchor.value, "offset": anchor.offset, "reason": "decode_failed"})
            continue
        decoded.append((anchor, record))
    for anchor in rejected:
        failures.append({"name": anchor.value, "offset": anchor.offset, "reason": "candidate_rejected"})
    return decoded, failures


def export_hrc_bytes(
    logical_source: str,
    data: bytes,
    output_root: Path,
    curve_steps: int,
    surface_steps_u: int,
    surface_steps_v: int,
    *,
    container_source: str | None = None,
) -> dict:
    decoded, failures = decode_hrc_bytes(data)
    out_dir = logical_output_dir(logical_source, output_root)
    used: set[str] = set()
    records: list[dict] = []

    for anchor, record in decoded:
        item = {
            "name": anchor.value,
            "anchor_offset": anchor.offset,
            "kind": record["kind"],
            "record_start": record.get("record_start"),
            "decoded_through": record.get("decoded_through_trims", record.get("decoded_through")),
            "reconstruction_ready": bool(record.get("reconstruction_ready")),
            "closed": bool(record.get("closed")) if record["kind"] == "nurbs_curve" else None,
            "closed_u": bool(record.get("closed_u")) if record["kind"] == "nurbs_surface" else None,
            "closed_v": bool(record.get("closed_v")) if record["kind"] == "nurbs_surface" else None,
            "trim_count": int(record.get("trim_count") or 0) if record["kind"] == "nurbs_surface" else 0,
        }
        if not record.get("reconstruction_ready"):
            item["status"] = "unsupported"
            if record["kind"] == "nurbs_curve":
                item["knot_strategy"] = (record.get("knot_conversion") or {}).get("strategy")
            else:
                item["knot_strategy_u"] = (record.get("knot_conversion_u") or {}).get("strategy")
                item["knot_strategy_v"] = (record.get("knot_conversion_v") or {}).get("strategy")
            records.append(item)
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / unique_output_name(anchor.value, anchor.offset, used)
        try:
            if record["kind"] == "nurbs_curve":
                stats = evaluator.write_curve_obj(output_path, record, curve_steps)
            else:
                stats = evaluator.write_surface_obj(output_path, record, surface_steps_u, surface_steps_v)
        except Exception as exc:
            item["status"] = "export_failed"
            item["error"] = f"{type(exc).__name__}: {exc}"
            records.append(item)
            continue

        item["status"] = "exported"
        item["obj"] = output_path.relative_to(output_root.parent).as_posix()
        item["stats"] = stats
        records.append(item)

    result = {
        "source": logical_source,
        "source_size": len(data),
        "source_sha256": sha256_bytes(data),
        "record_count": len(decoded),
        "failure_count": len(failures),
        "records": records,
        "candidate_failures": failures,
    }
    if container_source:
        result["container_source"] = container_source
    return result


def iter_sources(source_root: Path, include_zip_archives: bool):
    direct = [path for path in (sorted(source_root.rglob("*.hrc")) + sorted(source_root.rglob("*.HRC"))) if path.is_file()]
    direct_names = {path.relative_to(source_root).as_posix().lower() for path in direct}
    for path in direct:
        yield path.relative_to(source_root).as_posix(), path.read_bytes(), None

    if not include_zip_archives:
        return
    archives = [path for path in (sorted(source_root.rglob("*.zip")) + sorted(source_root.rglob("*.ZIP"))) if path.is_file()]
    for archive_path in archives:
        archive_rel = PurePosixPath(archive_path.relative_to(source_root).as_posix())
        expanded_root = archive_rel.with_suffix("")
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                for info in sorted(zf.infolist(), key=lambda item: item.filename.lower()):
                    if info.is_dir() or not info.filename.lower().endswith(".hrc"):
                        continue
                    member = PurePosixPath(info.filename.replace("\\", "/"))
                    logical = (expanded_root / member).as_posix()
                    if logical.lower() in direct_names:
                        continue
                    yield logical, zf.read(info), archive_rel.as_posix()
        except zipfile.BadZipFile:
            print(f"warning: bad ZIP skipped: {archive_path}", file=sys.stderr)


def _identity_matrix() -> list[list[float]]:
    return [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _matrix_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def _srt_matrix(srt: dict | None) -> list[list[float]]:
    if not srt:
        return _identity_matrix()
    sx, sy, sz = srt["scale"]
    rx, ry, rz = srt["rotation_xyz"]
    tx, ty, tz = srt["translation_xyz"]
    cx, sxn = math.cos(rx), math.sin(rx)
    cy, syn = math.cos(ry), math.sin(ry)
    cz, szn = math.cos(rz), math.sin(rz)
    rot_x = [[1,0,0,0],[0,cx,sxn,0],[0,-sxn,cx,0],[0,0,0,1]]
    rot_y = [[cy,0,-syn,0],[0,1,0,0],[syn,0,cy,0],[0,0,0,1]]
    rot_z = [[cz,szn,0,0],[-szn,cz,0,0],[0,0,1,0],[0,0,0,1]]
    scale = [[sx,0,0,0],[0,sy,0,0],[0,0,sz,0],[0,0,0,1]]
    translate = [[1,0,0,0],[0,1,0,0],[0,0,1,0],[tx,ty,tz,1]]
    return _matrix_mul(scale, _matrix_mul(rot_x, _matrix_mul(rot_y, _matrix_mul(rot_z, translate))))


def _transform_point(point: tuple[float, float, float], matrix: list[list[float]]) -> tuple[float, float, float]:
    x, y, z = point
    return (
        x * matrix[0][0] + y * matrix[1][0] + z * matrix[2][0] + matrix[3][0],
        x * matrix[0][1] + y * matrix[1][1] + z * matrix[2][1] + matrix[3][1],
        x * matrix[0][2] + y * matrix[1][2] + z * matrix[2][2] + matrix[3][2],
    )


def _hierarchy_worlds(tree_report: dict) -> dict[str, list[list[float]]]:
    outer = tree_report.get("outer_model") or {}
    worlds: dict[str, list[list[float]]] = {}
    if outer.get("name"):
        worlds[outer["name"]] = _srt_matrix(outer.get("local_srt"))
    for item in tree_report.get("tree", []):
        parent = item.get("parent_name")
        if parent not in worlds or not item.get("local_srt"):
            continue
        worlds[item["name"]] = _matrix_mul(_srt_matrix(item["local_srt"]), worlds[parent])
    return worlds


def _decode_tree_parametric(data: bytes, item: dict) -> dict | None:
    string_offset = item.get("string_offset")
    if string_offset is None:
        return None
    anchor = probe.StringAnchor(offset=int(string_offset), value=str(item["name"]), parametric=True)
    return probe.decode_parametric_record(data, anchor)


def export_hierarchy_obj(
    source_path: Path,
    output_path: Path,
    curve_steps: int,
    surface_steps_u: int,
    surface_steps_v: int,
) -> dict:
    """Emit one combined OBJ using decoded HRC hierarchy and source-local SRT."""
    data = source_path.read_bytes()
    tree_report = hrc_tree.probe(source_path)
    worlds = _hierarchy_worlds(tree_report)
    outer = tree_report.get("outer_model") or {}
    items: list[dict] = []
    if outer.get("class_id") in {9, 10}:
        items.append({**outer, "parent_name": None, "depth": 0})
    items.extend(item for item in tree_report.get("tree", []) if item.get("class_id") in {9, 10})

    lines = [f"# Source: {source_path.as_posix()}", "# Hierarchy-aware tessellated NURBS derivative"]
    vertex_offset = 0
    exports: list[dict] = []
    failures: list[dict] = []
    for item in items:
        name = item["name"]
        world = worlds.get(name)
        if world is None:
            failures.append({"name": name, "reason": "world_transform_unavailable"})
            continue
        record = _decode_tree_parametric(data, item)
        if record is None:
            failures.append({"name": name, "reason": "decode_failed"})
            continue
        if not record.get("reconstruction_ready"):
            failures.append({"name": name, "reason": "record_not_reconstruction_ready"})
            continue

        lines.extend(["", f"o {sanitize_component(name)}"])
        entry = {
            "name": name,
            "kind": record["kind"],
            "parent_name": item.get("parent_name"),
            "depth": item.get("depth"),
            "record_start": record.get("record_start"),
            "local_srt": item.get("local_srt"),
            "world_matrix_row_major": world,
        }
        if record["kind"] == "nurbs_curve":
            points, knots, degree, closed = evaluator._curve_data(record)
            params = evaluator._samples(knots, degree, len(points), curve_steps, closed)
            vertices = [_transform_point(evaluator.evaluate_curve(record, value), world) for value in params]
            lines.extend(f"v {x:.9f} {y:.9f} {z:.9f}" for x, y, z in vertices)
            indices = [vertex_offset + index + 1 for index in range(len(vertices))]
            if closed and indices:
                indices.append(indices[0])
            if indices:
                lines.append("l " + " ".join(str(index) for index in indices))
            vertex_offset += len(vertices)
            entry.update({"vertices": len(vertices), "closed": closed})
        else:
            _, cu, cv, ku, kv, du, dv, closed_u, closed_v = evaluator._surface_data(record)
            us = evaluator._samples(ku, du, cu, surface_steps_u, closed_u)
            vs = evaluator._samples(kv, dv, cv, surface_steps_v, closed_v)
            vertices = [_transform_point(evaluator.evaluate_surface(record, u, v), world) for v in vs for u in us]
            lines.extend(f"v {x:.9f} {y:.9f} {z:.9f}" for x, y, z in vertices)
            boundaries, holes = evaluator._trim_loops(record)
            trim = bool(boundaries or holes)
            ud, vd = (ku[du], ku[cu]), (kv[dv], kv[cv])
            uc = surface_steps_u if closed_u else surface_steps_u - 1
            vc = surface_steps_v if closed_v else surface_steps_v - 1
            quads = clipped = 0
            for v_index in range(vc):
                vn = (v_index + 1) % surface_steps_v
                vm = evaluator._mid(vs[v_index], vs[vn], *vd, closed_v)
                for u_index in range(uc):
                    un = (u_index + 1) % surface_steps_u
                    um = evaluator._mid(us[u_index], us[un], *ud, closed_u)
                    if trim and not evaluator._keep_uv((um, vm), boundaries, holes):
                        clipped += 1
                        continue
                    a = vertex_offset + v_index * surface_steps_u + u_index + 1
                    b = vertex_offset + v_index * surface_steps_u + un + 1
                    c = vertex_offset + vn * surface_steps_u + un + 1
                    d = vertex_offset + vn * surface_steps_u + u_index + 1
                    lines.append(f"f {a} {b} {c} {d}")
                    quads += 1
            vertex_offset += len(vertices)
            entry.update({
                "vertices": len(vertices), "quads": quads, "quads_clipped_by_trim": clipped,
                "closed_u": closed_u, "closed_v": closed_v,
                "trim_boundary_loops": len(boundaries), "trim_hole_loops": len(holes),
            })
        exports.append(entry)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "schema": "bz2-hierarchy-aware-nurbs-export-v1",
        "obj": str(output_path),
        "chosen_hierarchy_baseline": tree_report.get("chosen_baseline"),
        "hierarchy_baseline_candidates": tree_report.get("baseline_candidates"),
        "source_parametric_count": len(items),
        "exported_count": len(exports),
        "failure_count": len(failures),
        "exports": exports,
        "failures": failures,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path, help="Root containing modelsdirectory/HRC files and embedded ZIPs")
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/extracts/hrc_nurbs"))
    parser.add_argument("--report", type=Path, default=Path("artifacts/reports/hrc_nurbs.json"))
    parser.add_argument("--curve-steps", type=int, default=64)
    parser.add_argument("--surface-steps-u", type=int, default=32)
    parser.add_argument("--surface-steps-v", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", help="Optional case-insensitive regex applied to logical HRC paths")
    parser.add_argument("--no-zip-archives", action="store_true", help="Do not inspect HRC members inside ZIP archives")
    parser.add_argument("--hierarchy-aware", action="store_true", help="Also emit one combined OBJ per HRC with decoded internal SRT/hierarchy applied")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    report_path = args.report.resolve()
    wanted = re.compile(args.only, re.IGNORECASE) if args.only else None
    sources = iter_sources(source_root, not args.no_zip_archives)

    counters = Counter()
    entries: list[dict] = []
    scanned = 0
    for logical, data, container_source in sources:
        if wanted and not wanted.search(logical):
            continue
        if args.limit and scanned >= args.limit:
            break
        scanned += 1
        if container_source:
            counters["archive_hrc_members_scanned"] += 1
        else:
            counters["direct_hrc_files_scanned"] += 1
        entry = export_hrc_bytes(
            logical, data, output_root, args.curve_steps, args.surface_steps_u, args.surface_steps_v,
            container_source=container_source,
        )
        if not entry["record_count"] and not entry["failure_count"]:
            continue
        if args.hierarchy_aware and entry["record_count"]:
            hierarchy_dir = logical_output_dir(logical, output_root)
            hierarchy_obj = hierarchy_dir / f"{sanitize_component(PurePosixPath(logical).stem)}__hierarchy.obj"
            if container_source:
                with tempfile.NamedTemporaryFile(suffix=".hrc") as handle:
                    handle.write(data)
                    handle.flush()
                    hierarchy = export_hierarchy_obj(
                        Path(handle.name), hierarchy_obj, args.curve_steps, args.surface_steps_u, args.surface_steps_v
                    )
                hierarchy["logical_source"] = logical
                hierarchy["container_source"] = container_source
                entry["hierarchy_export"] = hierarchy
            else:
                source_path = source_root / Path(*PurePosixPath(logical).parts)
                entry["hierarchy_export"] = export_hierarchy_obj(
                    source_path, hierarchy_obj, args.curve_steps, args.surface_steps_u, args.surface_steps_v
                )
        entries.append(entry)
        counters["hrc_files_with_parametric_data"] += 1
        counters["records"] += entry["record_count"]
        counters["candidate_failures"] += entry["failure_count"]
        for record in entry["records"]:
            counters[record["kind"]] += 1
            counters[f"status:{record['status']}"] += 1
            if record.get("trim_count"):
                counters["trimmed_surfaces"] += 1
                counters["trim_loops"] += record["trim_count"]
        if scanned % 500 == 0:
            print(f"scanned {scanned} logical HRC sources", file=sys.stderr)

    counters["logical_hrc_sources_scanned"] = scanned
    payload = {
        "schema": "bz2-hrc-nurbs-export-v2",
        "source_root": str(source_root),
        "output_root": str(output_root),
        "settings": {
            "curve_steps": args.curve_steps,
            "surface_steps_u": args.surface_steps_u,
            "surface_steps_v": args.surface_steps_v,
            "include_zip_archives": not args.no_zip_archives,
            "trim_tessellation": "uv_face_centroid_clip_validation_quality",
            "hierarchy_aware": args.hierarchy_aware,
        },
        "summary": dict(sorted(counters.items())),
        "files": entries,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

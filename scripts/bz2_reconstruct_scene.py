#!/usr/bin/env python3
"""Run the validated static BZ2 Softimage reconstruction stack for one DSC scene.

Example:

    python scripts/bz2_reconstruct_scene.py \
        modelsdirectory/Some/SCENES/example.1-0.dsc \
        modelsdirectory/Archival.zip \
        Some \
        artifacts/reconstructed/example

The output directory contains a final ``scene.gltf`` plus source PNGs and the
sidecars needed by ``blender_finish_reconstruction.py``. Every stage retains its
own report so a later decoder refinement can be regression-checked independently.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import bz2_dsc_material_gltf as dscmat
import bz2_dsc_multiroot_gltf as multiroot
import bz2_hrc_root_special_geometry as special_geometry
import bz2_dsc_multiroot_material_gltf as multiroot_materials
import bz2_texture_layers_gltf as texture_layers
import bz2_mtr_gltf_refine as mtr_refine
import bz2_dsc_scene_gltf as scene_gltf
import bz2_model_texture_projection as model_projection
import bz2_uv_provenance_gltf as uv_provenance
import bz2_fx_director_scene as fx_director
import bz2_sts_render_state as sts_render_state


class ReconstructionError(RuntimeError):
    pass


def _summary_error(name: str, summary: dict, fields: tuple[str, ...]) -> None:
    problems = {field: summary.get(field) for field in fields if summary.get(field)}
    if problems:
        raise ReconstructionError(f"{name} failed validation: {problems}")


def _source_picture_warnings(layers: dict, projections: dict) -> list[dict]:
    """Promote absent source images to explicit archival warnings."""
    warnings = []
    for kind, summary in (("missing_material_picture_sources", layers), ("missing_model_projection_picture_sources", projections)):
        count = int(summary.get("unresolved_picture_count") or 0)
        if count:
            warnings.append({"kind": kind, "count": count, "details": summary.get("unresolved_pictures", [])})
    return warnings


def _resolve_setup_soft(
    scene_dsc: Path,
    asset_source: Path,
    scene_prefix: str,
) -> tuple[str | None, dict | None]:
    store = dscmat.open_store(asset_source)
    chapters, _relations = dscmat.parse_dsc(scene_dsc)
    setup_names = chapters.get("SETUP_SOFT", [])
    if not setup_names:
        return None, None

    # The exact reference scenes contain one SETUP_SOFT element. Preserve all
    # ambiguity rather than silently choosing if a future scene contains more.
    if len(setup_names) != 1:
        return None, {
            "status": "ambiguous_setup_soft",
            "setup_soft_names": setup_names,
        }
    name = setup_names[0]
    member = (
        store.find_basename(name + ".sts", f"{scene_prefix}/SETUP_SOFT")
        or store.find_basename(name + ".sts")
    )
    if not member:
        return None, {
            "status": "setup_soft_source_missing",
            "setup_soft_name": name,
        }

    data = store.read(member)
    with tempfile.NamedTemporaryFile(suffix=".sts") as handle:
        handle.write(data)
        handle.flush()
        payload = sts_render_state.parse_sts(Path(handle.name))
    payload["source_sts"] = member
    payload["setup_soft_name"] = name
    payload["status"] = "ok"
    return member, payload


def _copy_stage_reports(scene_gltf_path: Path, report_dir: Path) -> list[str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    known_suffixes = (
        ".multiroot.json",
        ".special_geometry.json",
        ".materials.json",
        ".texture_layers.json",
        ".mtr.json",
        ".scene.json",
        ".model_textures.json",
        ".uv_provenance.json",
        ".fx.json",
    )
    for suffix in known_suffixes:
        source = scene_gltf_path.with_suffix(suffix)
        if not source.is_file():
            continue
        destination = report_dir / source.name
        shutil.copy2(source, destination)
        copied.append(str(destination))
    return copied


def reconstruct(
    scene_dsc: Path,
    asset_source: Path,
    scene_prefix: str,
    output_dir: Path,
    *,
    curve_steps: int = 64,
    surface_steps_u: int = 32,
    surface_steps_v: int = 32,
) -> dict:
    scene_dsc = scene_dsc.resolve()
    asset_source = asset_source.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    final_gltf = output_dir / "scene.gltf"

    stages = []

    multi = multiroot.assemble_scene(
        scene_dsc,
        asset_source,
        scene_prefix,
        final_gltf,
        include_parametric=True,
        curve_steps=max(2, curve_steps),
        surface_steps_u=max(2, surface_steps_u),
        surface_steps_v=max(2, surface_steps_v),
    )
    _summary_error(
        "multi-root assembly",
        multi,
        (
            "missing_root_count",
            "root_export_failure_count",
            "unmapped_model_count",
            "ambiguous_node_mapping_count",
            "code110_parent_mismatch_count",
            "code110_parent_unmapped_count",
        ),
    )
    stages.append({"stage": "multi_root", "summary": multi})

    special = special_geometry.append_root_geometry(
        final_gltf,
        asset_source,
        scene_prefix,
        final_gltf,
    )
    _summary_error(
        "special ROOT geometry",
        special,
        ("unsupported_class1_root_count",),
    )
    stages.append({"stage": "special_root_geometry", "summary": special})

    materials = multiroot_materials.bind_scene_materials(
        final_gltf,
        scene_dsc,
        asset_source,
        scene_prefix,
        final_gltf,
    )
    _summary_error(
        "complete-scene material binding",
        materials,
        ("class4_decode_failure_count", "slot_error_count"),
    )
    stages.append({"stage": "materials", "summary": materials})

    layers = texture_layers.restore_layers(
        final_gltf,
        scene_dsc,
        asset_source,
        scene_prefix,
        final_gltf,
    )
    # Missing picture bytes are corpus-completeness warnings; TXMP state remains preserved.
    _summary_error(
        "ordered texture layers",
        layers,
        ("missing_gltf_material_count",),
    )
    stages.append({"stage": "texture_layers", "summary": layers})

    mtr = mtr_refine.refine_gltf(
        final_gltf,
        asset_source,
        final_gltf,
    )
    _summary_error("MTR refinement", mtr, ("failure_count",))
    stages.append({"stage": "mtr", "summary": mtr})

    scene = scene_gltf.augment_scene(
        scene_dsc,
        asset_source,
        scene_prefix,
        final_gltf,
        final_gltf,
    )
    # Camera/light recovery is useful but not every DSC necessarily authors one.
    # Missing decoded source objects are reported in the sidecar rather than being
    # assumed fatal here; structural scene-model failures were already fatal above.
    stages.append({"stage": "camera_lights", "summary": scene})

    projections = model_projection.augment_model_projections(
        final_gltf,
        scene_dsc,
        asset_source,
        scene_prefix,
        final_gltf,
    )
    # Missing code-400 picture bytes are likewise preserved as source warnings.
    stages.append({"stage": "model_projections", "summary": projections})

    # Make the distinction between genuine per-corner source UVs, all-zero UVs
    # that require Softimage projection state, and normalized NURBS parameter UVs
    # explicit in the portable asset before Blender generates additive maps.
    uv = uv_provenance.annotate(final_gltf, final_gltf)
    _summary_error("UV provenance", uv, ("decode_failure_count",))
    stages.append({"stage": "uv_provenance", "summary": uv})

    fx = fx_director.attach_fx_directors(
        final_gltf,
        scene_dsc,
        asset_source,
        scene_prefix,
        final_gltf,
    )
    _summary_error("FxDirector", fx, ("unresolved_count",))
    stages.append({"stage": "fx_director", "summary": fx})

    _sts_member, render_state = _resolve_setup_soft(
        scene_dsc,
        asset_source,
        scene_prefix,
    )
    render_state_path = output_dir / "scene.render_state.json"
    if render_state is not None:
        render_state_path.write_text(json.dumps(render_state, indent=2), encoding="utf-8")

    report_dir = output_dir / "reports"
    copied_reports = _copy_stage_reports(final_gltf, report_dir)

    blender_script = Path(__file__).with_name("blender_finish_reconstruction.py")
    scene_sidecar = final_gltf.with_suffix(".scene.json")
    texture_sidecar = final_gltf.with_suffix(".texture_layers.json")
    model_texture_sidecar = final_gltf.with_suffix(".model_textures.json")
    output_blend = output_dir / "scene.blend"
    blender_command = (
        f'blender --background --python "{blender_script}" -- '
        f'"{final_gltf}" "{scene_sidecar}" "{output_blend}" '
        f'"{texture_sidecar}" "{model_texture_sidecar}" "{render_state_path}"'
    )
    (output_dir / "blender_command.txt").write_text(blender_command + "\n", encoding="utf-8")

    final_doc = json.loads(final_gltf.read_text(encoding="utf-8"))
    source_warnings = _source_picture_warnings(layers, projections)
    manifest = {
        "schema": "bz2-reconstructed-scene-bundle-v2",
        "scene_dsc": str(scene_dsc),
        "asset_source": str(asset_source),
        "scene_prefix": scene_prefix,
        "output_dir": str(output_dir),
        "scene_gltf": str(final_gltf),
        "scene_bin": str(output_dir / final_doc["buffers"][0]["uri"]),
        "render_state": str(render_state_path) if render_state_path.is_file() else None,
        "texture_layers": str(texture_sidecar),
        "model_texture_projections": str(model_texture_sidecar),
        "uv_provenance": str(final_gltf.with_suffix(".uv_provenance.json")),
        "blender_output": str(output_blend),
        "blender_command": blender_command,
        "final_node_count": len(final_doc.get("nodes", [])),
        "final_mesh_count": len(final_doc.get("meshes", [])),
        "final_primitive_count": sum(
            len(mesh.get("primitives", []))
            for mesh in final_doc.get("meshes", [])
        ),
        "final_material_count": len(final_doc.get("materials", [])),
        "final_image_count": len(final_doc.get("images", [])),
        "source_explicit_polygon_uv_primitive_count": uv.get("source_explicit_polygon_uv_primitive_count"),
        "source_zero_uv_primitive_count": uv.get("source_zero_uv_primitive_count"),
        "zero_uv_with_model_projection_count": uv.get("zero_uv_with_model_projection_count"),
        "source_warning_count": sum(int(item["count"]) for item in source_warnings),
        "source_warnings": source_warnings,
        "copied_stage_reports": copied_reports,
        "stage_summaries": [
            {
                "stage": item["stage"],
                "schema": item["summary"].get("schema"),
            }
            for item in stages
        ],
        "notes": [
            "scene.gltf is the portable reconstruction product; source-format sidecars remain alongside it for Blender and future decoder refinements",
            "source HRC polygon UVs are preserved; all-zero UVs that depend on projection state are explicitly annotated rather than treated as valid authored unwraps",
            "the Blender command now generates additive projection UV maps and restores confirmed texture scale/offset/crop while leaving source UVs intact",
            "missing source picture files are preserved as explicit source warnings rather than guessed, substituted, or treated as decoder failures",
            "renderer-specific Mental Ray and FxDirector semantics remain metadata unless an explicit reconstruction stage has been proven",
        ],
    }
    manifest_path = output_dir / "reconstruction.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene_dsc", type=Path)
    parser.add_argument("asset_source", type=Path)
    parser.add_argument("scene_prefix")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--curve-steps", type=int, default=64)
    parser.add_argument("--surface-steps-u", type=int, default=32)
    parser.add_argument("--surface-steps-v", type=int, default=32)
    args = parser.parse_args()
    try:
        manifest = reconstruct(
            args.scene_dsc,
            args.asset_source,
            args.scene_prefix,
            args.output_dir,
            curve_steps=args.curve_steps,
            surface_steps_u=args.surface_steps_u,
            surface_steps_v=args.surface_steps_v,
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 1
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

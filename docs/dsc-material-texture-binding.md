# DSC material / texture binding

This note records the first source-faithful material binding path for reconstructed BZ2 Softimage assets.

## Proven binding chain

For class-4 polygon geometry, the complete source chain is now:

```text
HRC polygon metadata
  upper u16 = material slot
        |
        v
ordered DSC MODELS -> MATERIALS edges (relation code 300)
        |
        v
binary .mtr material
        |
        v
DSC MATERIALS -> TEXTURES2D edge (relation code 401)
        |
        v
binary .txt texture object
        |
        v
original source picture (.pic in the validated targets)
        |
        v
PNG + glTF material primitive
```

The material slot is **not** a global material index. It selects an entry from the ordered code-300 material list associated with the corresponding DSC model node.

## Softimage material inheritance

The high-resolution archived walker proves that a child polygon mesh may have no direct model-to-material relation while still using the parent's material set. `cube8` and `cube10` require nearest-ancestor material inheritance. Their source polygon slot values then resolve normally against the inherited ordered material list.

This behavior is preserved by `scripts/bz2_dsc_material_gltf.py`.

## High-resolution ISDF tank

Source scene:

`NewTank/NewTank/SCENES/hi_res-ISDF_tank.1-0.dsc`

Source HRC:

`NewTank/NewTank/MODELS/tank2-NewIVTankBody.1-0.hrc`

Validated output:

- 17 HRC hierarchy nodes
- 15 class-4 source meshes
- 22 glTF primitives after per-material splitting
- 27 source materials represented
- 4 original source texture images bound
- 0 polygon material-slot errors
- 0 unresolved class-4 local transforms

Bound source textures include `tank.pic`, `tankturret1.pic`, `TANKTURRETTOP.1.pic`, and `turret.pic`.

An independent glTF loader resolves all 22 geometry instances with finite assembled bounds and finds texture bindings on nine geometry primitives.

## High-resolution ISDF walker

Source scene:

`walker_final/SCENES/ISDF-walker_final_carey.1-0.dsc`

Source HRC:

`walker_final/MODELS/walker_final_carey-null1.1-0.hrc`

Validated output:

- 104 HRC hierarchy nodes
- 71 class-4 source meshes
- 101 glTF primitives after per-material splitting
- 50 source materials represented
- 8 original source texture images bound
- 0 polygon material-slot errors
- 0 unresolved class-4 local transforms

Bound images include the original walker sheets for the middle/upper/lower leg, hazard stripes, blue glow, chrome, pipes and cavern texture.

An independent glTF loader resolves all 101 geometry instances with finite assembled bounds and finds texture bindings on 87 geometry primitives.

## Appearance boundary

The current `.mtr` decoder has a stable big-endian float window and maps the proven/inferred diffuse, specular, shininess and alpha values into a conservative glTF PBR approximation. This is sufficient for source-material identification and useful Blender previewing, but it is **not yet claimed to reproduce Softimage's renderer exactly**.

The original PIC renders should therefore remain the visual authority while these remaining semantics are refined:

- reflection/environment mapping behavior;
- emissive/self-illumination semantics;
- exact Softimage transparency mode;
- Phong/specular conversion;
- texture projection/wrap options beyond the preserved polygon UVs;
- lighting and render-environment response.

## Blender target

The materialized glTF is intended to be directly importable into Blender while preserving:

- HRC node hierarchy and names;
- local transforms;
- original polygon UVs;
- per-polygon material assignment;
- original texture images converted losslessly from PIC where applicable.

The next reconstruction layer should create Blender-native material nodes and recover the DSC camera/light scene so Blender renders can be compared directly against the archived Softimage PIC reference frames.

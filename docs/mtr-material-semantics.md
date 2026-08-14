# Softimage MTR material semantics

The provisional material parser used by the first textured glTF milestone started the scalar window four bytes too late. This did not corrupt the recovered diffuse/specular/shininess values because those fields shifted into the positions the provisional code expected, but it **did** hide ambient RGB and caused the source refractive-index field to be mistaken for alpha.

## Correct scalar block

After the `MTRL<name>\0` record there is an eight-byte prefix followed by **13 big-endian float32 values**. Equivalently, the scalar block begins at `material_name_nul + 9`.

The corpus-consistent layout is:

```text
0  ambient.r
1  ambient.g
2  ambient.b
3  diffuse.r
4  diffuse.g
5  diffuse.b
6  specular.r
7  specular.g
8  specular.b
9  shininess
10 transparency
11 reflectivity
12 refractive index
```

This layout was decoded successfully across **14,773** direct/archive MTR records.

The population strongly supports the semantic interpretation:

- transparency: 14,021 records at zero; authored nonzero values cluster around `0.1`, `0.2`, `0.3`, `0.5`, `0.7`, and `1.0`;
- reflectivity: sparse (232 nonzero records) and concentrated in named metals/glass/reflection materials;
- refractive index: 14,762 records at `1.0`, nine glass-family records at `1.1`, and two legacy records at `0.0`.

## Named glass proof

The exact-version walker scene 20 contains:

`walker_final-GLASS-glass.1-2.3-0`

with the corrected block:

```text
ambient       0, 0, 0
diffuse       0, 0, 0
specular      50, 50, 50
shininess     300
transparency  0.1
reflectivity  1.0
IOR           1.1
```

Earlier/later walker glass revisions vary transparency/reflectivity while retaining the same high specular and IOR 1.1. This is much more coherent than the old interpretation where `1.1` was treated as an invalid alpha value.

## glTF mapping policy

`scripts/bz2_mtr_gltf_refine.py` is intentionally conservative.

- **Diffuse RGB** -> glTF base color.
- **Shininess** -> roughness using `sqrt(2/(n+2))`, preserving the earlier Phong-to-PBR approximation.
- **Specular RGB** -> `KHR_materials_specular`, normalized/clamped to the extension's legal range while retaining the raw Softimage RGB in extras.
- **Transparency** -> provisional `KHR_materials_transmission` factor.
- **Non-default valid IOR** -> `KHR_materials_ior`.
- **Ambient RGB** -> source metadata only; glTF PBR has no direct Softimage ambient-color equivalent.
- **Reflectivity** -> source metadata only. It is explicitly **not** treated as glTF metallic because Softimage reflectivity is an authored reflection/environment response, not a metalness workflow.

Texture alpha and existing glTF `alphaMode` from the PIC stage are left untouched. Source transparency is not shoved into base-color alpha because that loses the distinction between image alpha and material transmission.

## Exact target validation

### High-resolution ISDF tank

- 27 source materials refined / 0 failures;
- 0 source-transmission materials;
- 1 material with nonzero source reflectivity (`tank2-mat8_1.1-0`, about `0.5091`);
- 22 materialized geometry primitives still load with finite bounds after refinement.

### ISDF walker scene 20

The exact-version scene now runs through geometry, materials, original textures, camera/lights and corrected MTR semantics:

- 51 materials refined / 0 failures;
- 1 transmission material;
- 15 source-reflective materials;
- 1 non-default IOR material;
- 108 per-material geometry primitives;
- 1 recovered camera / 6 recovered lights;
- finite scene bounds after all standard glTF material extensions are added.

## Remaining appearance work

This does not yet solve every Softimage shading behavior. In particular:

- source reflectivity/environment mapping still needs a Blender-side approximation calibrated against PIC renders;
- self-illumination/emissive behavior is not proven to live in this 13-float block and should not be guessed from names such as `blueglow`;
- material-to-TEXTURES3D relation code 501 and volume-shader relationships need separate study;
- exact Softimage transparency/refraction rendering will differ from modern physically based transmission.

The recovered raw values remain attached to glTF material extras so Blender reconstruction can evolve without re-decoding the source archive.

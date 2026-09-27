# Retail game data as a picture source and ground truth

The shipped Battlezone II 1.0 install (`_Battlezone_II_1.0.7z`) has two uses for this project:

1. it contains the in-game textures for pictures the art archive lacks;
2. it contains the compiled `.msh` models built from the same art, which are an independent ground truth for the final game models.

Nothing from the retail game is committed. It is staged into the git-ignored `.bz2-source-cache/`.

## Retail pictures fill missing references

`scripts/bz2_retail_pictures.py` extracts only the picture members of the DOCP v2 paks into `.bz2-source-cache/retail_bz2/<pak>/...`:

| pak | pictures | contents |
|---|---:|---|
| `data.pak` | 678 | in-game resolution (units 256×256) |
| `smtex.pak` | 471 | 128×128 low-detail set |
| `bumps.pak` | 123 | bump maps |

```bash
python scripts/bz2_retail_pictures.py "<...>/BZ Patch Installers/_Battlezone_II_1.0.7z"
```

The picture resolver (`bz2_texture_layers_gltf.resolve_picture_for_crop`) always resolves from the art archive first, using exact reference, then path, then crop-size match. It consults the retail pictures only when the archive has no copy at all:
- it matches by picture stem;
- it prefers `data` over `smtex` over `bumps`, and `.pic` over other formats;
- it records `picture_resolution: retail_game_supplement`.

These are shipped derivatives, resized and possibly retouched, not the artists' originals. Stale full-frame crops over them select the whole picture (`effective_crop`).

**Coverage:** of the 88 picture names the corpus references but the archive lacks, 46 are in the retail paks, and 219 of the 257 affected scenes gain textures. The FURY (Scion) vehicle and building families, CORE buildings, ISDF building interiors, weapons and the dropship are all covered.

The 42 names still missing are:
- Softimage's own library pictures (`tran_shade`, `tran_proj`, from `D:/softimage/SOFT3D_3.8SP2/.../SI_Materials`);
- prototype and cinematic art that never shipped: the `mvtank` concept, face and eye maps, and Pluto desk props.

## Shipped `.msh` models as ground truth

`data.pak` ships 671 compiled `.msh` models. `scripts/bz2_msh_compare.py` compares them with the engine exports. It uses the `io_scene_bz2msh` parser (sibling checkout, `--msh-tool`).

How the comparison works:
- It pairs each multi-node `.msh` with the bundle whose engine XSI frame names overlap most (Jaccard ≥ 0.5): 209 pairs.
- It reads the `.msh` per-node meshes (vertex groups + indices, row-vector node matrices composed down the tree). The block-level face table does not index the same UV list and must not be used.
- It fits scale and axis flips, then reports the symmetric nearest-vertex distance.
- At every `.msh` face centre it samples the shipped texture at the shipped UV and ours at the nearest point on our surface, then compares the colours. This stays valid where our UVs were re-packed into a baked atlas.

```bash
python scripts/bz2_msh_compare.py --retail "<extracted data.pak dir>" --json out/msh_compare.json
```

Conventions confirmed:
- **Axes:** the `.msh` mirrors X relative to Softimage/dotXSI (76 of 88 geometry-matched pairs). BZ2 is left-handed at runtime.
- **Scale:** 1.0 for almost all pairs. The scavenger LOD is an outlier at 0.75.
- **UV origin:** `.msh` UVs are top-left (67 of 74 textured pairs).
- **Exactness:** 60 pairs agree within 1% of model size. The REND worm matches exactly: all 552 triangles and 278 vertices.

### Finding: stored UVs already include the texture-matrix rotation

With the corpus's πY `SI_Texture2D` rotation applied to stored CurrentUVs, several shipped models came out mirror-textured. Not applying it improved 22 of 74 textured pairs and made none worse, lifting the median colour correlation from 0.58 to 0.71:

| model | with rotation | without |
|---|---:|---:|
| REND worm `rcworm01` | −0.05 | 0.90 |
| Scion satchel `sgsatc00` | −0.09 | 0.83 |
| grenade launcher `iggren00` | 0.33 | 0.99 |
| APC wreck `peapc00` | 0.27 | 0.94 |
| walker `ivwalk01` | 0.68 | 0.92 |

`bz2_projection_uv.apply_current_uv_effects` therefore leaves the rotation out. The render harness scores were unchanged. Generated projections still apply the texture matrix.

The `.msh` animation-pose variants (`ivwalk_idle`, `_death`, …) sit lower (≈0.40) because their bind pose differs from the scene pose, not because of texturing.

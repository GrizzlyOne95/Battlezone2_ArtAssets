# ROOT-only class-1 primitive-grid geometry

Full DSC multi-root assembly exposes specialized scene roots that were previously absent from the selected-HRC export. The most immediately renderable subtype is Softimage HRC class 1, primitive kind 2.

## Binary layout

For the validated outer records, the class-1 payload begins:

```text
u16  primitive_kind   # 2
u16  u_count
u16  v_count
f32be vertices[u_count * v_count][3]
```

The vertex array is a row-major rectangular lattice. Adjacent lattice cells can therefore be triangulated without inventing additional control points.

Across the supplied direct + archive corpus there are 29 outer class-1 HRCs:

- 27 use primitive kind 2 rectangular lattices;
- one is a kind-3 primitive;
- one is a separate kind-0 variant.

Observed kind-2 dimensions are 13x13, 11x8, 9x8, 8x8, 7x8, and 5x8.

`bz2_hrc_root_special_geometry.py` deliberately supports only outer/ROOT kind-2 records. It does **not** emit nested class-1 records from complex HRC hierarchies, because those can be Softimage construction/history objects rather than additional render meshes.

## Exact tank and walker grids

Both reference scenes use the same 13x13 grid dimensions:

- 169 source vertices;
- 144 lattice cells;
- 288 generated triangles;
- local bounds approximately `(-71.55957, 0, -71.55957)` to `(71.55957, 0, 71.55957)`;
- planar local XZ surface with +Y generated normals.

The high-resolution tank places `tank2-grid1.1-0` at DSC Y = `-1.602825`. The original `TANK.1.pic` visibly contains a broad reflective ground surface beneath the vehicle, which is consistent with the recovered grid extent and placement.

Adding the grid geometry raises the complete static-scene geometry counts from:

- walker: 77 -> 78 geometry objects;
- tank: 17 -> 18 geometry objects.

Both resulting glTF scenes load successfully through an independent scene reader.

## Texture projection boundary

No `TEXCOORD_0` is fabricated for these grids. The tank grid already carries model-local DSC code-400 `rusty` texture/projection state, and the wider class-1/DSC evidence shows that these Softimage assets can rely on projection mapping rather than baked corner UVs.

The grid mesh is therefore emitted with POSITION + NORMAL only. The model-local TXMP projection layer remains responsible for reconstructing the authored floor texture once the Softimage projection support/mapping function is fully decoded.

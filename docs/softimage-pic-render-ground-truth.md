# Softimage PIC render ground truth

The supplied Battlezone II art archive contains far more than source textures. It also preserves thousands of original Softimage/mental ray render frames that can serve as visual ground truth for reconstructed assets and scenes.

## Corpus

Across direct files and embedded ZIP members:

- **6,494** files are named `.pic` / `.PIC`;
- **6,490** are standard decodable Softimage PIC images;
- **5,756** are in a render location (direct `RENDER_PICTURES`, an archived `RENDER_PICTURES`, or a ZIP physically stored under `RENDER_PICTURES`);
- **5,754** of those render-located files are standard PIC images;
- two `.pic` files are zero-byte placeholders;
- two 128-byte files are Softimage `ray...` render-reference records rather than pixel images.

The overwhelming image-storage pattern is 8-bit mixed RLE with one RGB packet and, usually, a separate alpha packet. Five images use uncompressed 8-bit RGBA.

## Dependency-free decoder

`scripts/softimage_pic.py` decodes the observed PIC storage layouts and writes RGBA PNG with only the Python standard library. This removes OpenImageIO as a hard dependency for preserving the original BZ2 textures/renders.

The implementation follows the standard Softimage PIC header and packet semantics used by established open-source decoders:

- magic `53 80 F6 34`;
- `PICT` identifier at byte 88;
- big-endian width/height;
- chained 8-bit channel packets;
- packet type 0: uncompressed;
- packet type 1: pure RLE;
- packet type 2: mixed RLE;
- channel masks `0x80/0x40/0x20/0x10` = R/G/B/A.

The decoder also identifies the two non-image `ray...` reference records explicitly instead of reporting misleading image corruption.

## Original visual-reference assets

### High-resolution ISDF tank

`Archival.zip/NewTank/NewTank/RENDER_PICTURES/TANK.1.pic` is a 1200×2100 RGBA render of the original high-resolution tank.

It is especially useful because it shows the intended appearance that geometry-only recovery cannot provide:

- orange/black body scheme;
- reflective dark panels;
- red and green emissive details;
- exact decal/marking placement;
- turret and barrel material separation;
- transparency/dark-glass treatment;
- source lighting and reflection response.

Its related source PIC textures include `tank.pic`, `TANKTURRETTOP.1.pic`, `tankturret1.pic`, `turret.pic`, and `tank_top2a.pic`. Converted PNGs visibly contain the same orange framing, dark panel layout, lights, markings, and mechanical detail seen in the render.

This is therefore a high-value reference target for DSC material-slot reconstruction and UV/material verification.

### High-resolution ISDF walker

`Archival.zip/OLD_PIX/walker_view1.1.pic` is a 2048×3584 RGBA reference render of the archived high-resolution walker.

It shows:

- grey metallic body surfaces;
- orange accent/hazard markings;
- blue emissive foot details;
- articulated joint placement;
- reflective/specular response;
- original lens-flare/light effects;
- original scene/reflection context.

The five `OLD_PIX/walker_view*.1.pic` renders provide multiple angles, making them suitable for silhouette, part-placement, material, and decal regression checks against the automated 71-mesh walker assembly.

### Concept tank

`Archival.zip/adconcept/RENDER_PICTURES/avtank.0.pic` is a 640×480 rendered tank reference showing a different archived concept/revision with painted ISDF markings. It is useful for revision identification and for avoiding accidental cross-version material assignment.

## How renders should be used

The render images are **visual derivatives**, not replacements for source data.

Use original HRC/DSC/MTR/TXT/PIC/SHD/LIG/CAM data as the structural source of truth, then use rendered PICs to verify the result visually:

1. geometry silhouette and part placement;
2. model hierarchy/articulation;
3. polygon material-slot assignment;
4. selected source texture for each material;
5. UV orientation/scale and decal placement;
6. transparency/alpha behavior;
7. diffuse/specular/emissive appearance where recoverable;
8. lights and camera framing for reconstructed scenes.

The render is allowed to differ from a modern PBR interpretation in shading response. The goal is to establish what the original Softimage scene looked like and preserve enough source semantics to reproduce or reinterpret it deliberately.

## Material-reconstruction implication

The archived tank scene confirms that DSC relation code `300` links models to materials and relation code `401` links materials to texture objects. Class-4 polygon metadata carries the internal material-slot index in its upper 16 bits. The slot index can therefore be resolved against the ordered model→material relations rather than applying one material to the entire HRC mesh.

The original render PICs now provide a direct visual check for whether that slot binding is correct.

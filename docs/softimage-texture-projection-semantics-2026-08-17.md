# Softimage texture-projection semantics — 2026-08-17

This note records official Softimage behavior relevant to interpreting the recovered TXMP `SI_Texture2D` transform fields. It is deliberately separate from binary-layout claims: the source documentation constrains *what the transform means*, while archive evidence determines where/how that state is serialized.

## Official behavior

Autodesk's **Texture Projection Definition Property Editor** documentation describes a texture projection UVW transformation with independent Scale U/V/W, Rotation U/V/W, and Translation U/V/W. It says these operations transform the texture projection **on its support** and use the picture's bottom-left corner as the pivot.

Source: `https://download.autodesk.com/global/docs/softimage2014/en_us/userguide/files/property9001.htm`

Autodesk's **Manipulating Projections and Supports** documentation distinguishes a texture support from the projection placed on that support. It states that by default the projection fills the support, and that the projection itself can then be scaled, rotated and translated on the support.

Critically, the same documentation says that changing the projection transformation does **not** immediately rewrite the editable UV coordinates. The **Freeze** operation bakes the projection's current UVW transformation into the UV coordinates and then resets the projection transform to identity/default values.

Source: `https://download.autodesk.com/global/docs/softimage2013/en_us/userguide/files/tex_applying_ManipulatingProjectionsandSupports.htm`

Autodesk's texturing overview also describes the texture operator stack: the initial texture coordinates are generated from the projection/support, after which texture-coordinate operations can remain live in the stack.

Source: `https://download.autodesk.com/global/docs/Softimage2014/en_us/userguide/files/tex_applying_AboutTexturinginSoftimage.htm`

## Consequence for the BZ2 reconstruction

This explains why a class-4 HRC can contain nontrivial `SI_MeshTextureCoords` / source `TEXCOORD_0` while its associated code-400 TXMP still carries a non-identity +90 matrix. Those states are not contradictory: the coordinates can represent the underlying projection output while a live projection UVW transformation remains separately authored.

Therefore the recovered +90 matrix must **not** be treated as a generic image-space `KHR_texture_transform` applied after final 2D UV generation. It belongs to the Softimage projection-coordinate pipeline and must be composed at the correct UVW/projection stage.

This still does not by itself prove direct-versus-inverse application, Euler construction order, or code-400/code-401 composition. Those remain corpus-falsification targets.

## Current production policy

Until transform direction/order is independently validated:

- preserve all +90 matrix state;
- do not discard source HRC UV coordinates;
- do not force non-identity +90 matrices through 2D `KHR_texture_transform`;
- do not invent a direct/inverse convention;
- use class-4 source UVs and mirrored/asymmetric corpus examples as independent validation oracles.

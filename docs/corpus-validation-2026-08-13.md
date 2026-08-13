# Corpus validation — 2026-08-13

Validation against the supplied `bz2_art` archive established these regression constraints for the HRC decoder:

- all 1,987 decoded NURBS records retain valid local SRT placement;
- for every HRC containing nested model records, the hierarchy baseline is `first_child_zero_run + 2`;
- this baseline rule passes 3,426/3,426 direct HRC files and 84/84 HRC members from `Archival.zip`;
- DSC hierarchy comparison, restricted to `MODELS -> MODELS` relation code 110, matches 33,324 of 33,327 comparable parent edges;
- all 5,188 outer class-4 mesh roots have recoverable local SRT;
- all 29,120 nested class-4 records have recoverable local SRT: 29,058 through the counted-polygon/edge layout and 62 through tightly scoped legacy movie/grouped fallbacks;
- the existing material/texture-anchor heuristic must not be the primary class-4 decoder because it disagrees with the structural placement on thousands of overlapping records;
- with the structural class-4 path plus its 62 legacy fallbacks, all 1,204 parametric records in the multi-record NURBS validation set are placeable through complete transform chains.

The normal counted-polygon class-4 layout supports zero-vertex transform-only nodes, polygons larger than 32 corners, and an all-NaN normal triplet as a missing-normal sentinel. A recurring 24-byte metadata block may occur between the class-4 edge trailer and local SRT and must be skipped as a complete signature rather than interpreted as transform floats.

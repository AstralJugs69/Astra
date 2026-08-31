# Golden artifact directory

The V1/V2 BRF, manifests, source map, and page-impact files are generated only
after Gate 0 binds the pinned Liblouis 3.38.0 table bundle. This repository does
not invent those bytes while Liblouis is unavailable. The golden test still
requires byte-identical repeat renders and compares checked-in artifacts when
the Gate 0 evidence is present.

## Live-demo volume

`demo-volume/` is the judge-facing, wholly synthetic V1/V2 source pair. With
the pinned demo profile it renders to **46 Braille pages** in both revisions.
The material correction is in a single middle source block, changes page **24**
only, and deterministically resynchronizes the unchanged suffix after page
**24**. The dedicated golden test preserves that property.

The Markdown source is generated from
[`demo_volume_source.py`](../fixtures/demo_volume_source.py) so it cannot be
mistaken for copied educational material. To materialize an explicit, local
V1 or V2 file for human upload/paste into the prepared Drive source, use
[`export_demo_volume.py`](../scripts/export_demo_volume.py). It refuses to
overwrite a file and makes no Drive request.

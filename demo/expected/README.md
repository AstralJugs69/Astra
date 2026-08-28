# Golden artifact directory

The V1/V2 BRF, manifests, source map, and page-impact files are generated only
after Gate 0 binds the pinned Liblouis 3.38.0 table bundle. This repository does
not invent those bytes while Liblouis is unavailable. The golden test still
requires byte-identical repeat renders and compares checked-in artifacts when
the Gate 0 evidence is present.

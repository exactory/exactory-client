# Research acquisition fixtures

These are authored examples, not downloaded literature or production responses.
They model provider field shapes while using invented titles, authors, abstracts,
references and identifiers. `tests/research_fixtures.py` generates authored Atom
pages and supplies deterministic clocks and HTTP streams. No test contacts a
registry or performs real provider pacing sleeps.

`authored-grid.jpg` and `authored-grid-progressive.jpg` encode an authored 8 by 8
RGB grid. Red is 0 for columns 0 to 3 and 255 for columns 4 to 7. Green is 0 for
rows 0 to 3 and 255 for rows 4 to 7. Blue is 128 everywhere. They were generated
from binary PPM with libjpeg-turbo 3.1.3 `cjpeg -quality 90`, with `-progressive`
for the second file. Both decoded successfully with that version's `djpeg`.
Tests read these committed bytes and require no image encoder or decoder.

The GIF encodes the same PPM with macOS `sips-316 -s format gif`; it decoded to
PNG with the same tool. The two WebP files use libwebp 1.5.0 `cwebp -q 90` and
`cwebp -lossless` and both decoded successfully with `dwebp` 1.5.0. These tools
were used only to prepare and independently check the authored fixtures.

| Fixture | SHA-256 |
| --- | --- |
| authored-grid.jpg | a492a45cf152052c0b741325c4a7ab00f246ce6c35e454d4670f5699eb33d1a4 |
| authored-grid-progressive.jpg | 78d50fc3c116d6a64afc765ec5e14ef701b6dc7f7f0fe0627a4c7dff77c48baf |
| authored-grid.gif | 5de3b9f67de92cd1b157424f434cf24a34f154c9f801a4d0ef1ea5c61a02950e |
| authored-grid.webp | 6159020e337afa5e8533a7d4ebf2c7429730ff12b404dee8d454199d9477444c |
| authored-grid-lossless.webp | 7282951ee7fcde9c3c644ee8da332a163fbf93f41c946cff0017cd9124018554 |

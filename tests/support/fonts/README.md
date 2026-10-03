# Deterministic layout fonts

`BoxEmProbe.ttf` is an original synthetic font made only from square outlines,
with one-em advance and ink bounds for ASCII and the ellipsis. The font and its
generator are CC0-1.0; no glyphs or data were copied from another font.

It reproduces the wide missing-font metrics seen in Qt's Windows offscreen
backend. Tests select it explicitly for an additional compact layout case,
alongside normal platform font and enlarged/stretched text cases. It is not an
application font and is never selected by the product.

`WideEmProbe.ttf` is a second original CC0-1.0 fixture made from rectangles.
Its 1,100 units per em and 1,400-unit advances/ink widths produce exactly 28px
glyph widths at 22px height, matching Windows's rounded 125% widths. Fixed
metrics avoid platform differences in `QFont.setStretch`; normal font/stretch
cases remain separate. Tests register either fixture only when explicitly
requested and remove it afterward, so normal cases retain their platform fallback.

The checked-in file loads through `QFontDatabase`; tests need no extra package.
For regeneration only, run `generate_box_em_probe.py` or
`generate_wide_em_probe.py` with an existing FontTools installation. Both
generators fix header timestamps for reproducible bytes.

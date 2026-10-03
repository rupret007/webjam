# Full-em layout fixture

`BoxEmProbe.ttf` is an original synthetic font made only from square outlines,
with one-em advance and ink bounds for ASCII and the ellipsis. The font and its
generator are CC0-1.0; no glyphs or data were copied from another font.

It reproduces the wide missing-font metrics seen in Qt's Windows offscreen
backend. Tests select it explicitly for an additional compact layout case,
alongside normal platform font and enlarged/stretched text cases. It is not an
application font and is never selected by the product.

The checked-in file loads through `QFontDatabase`; tests need no extra package.
For regeneration only, run `generate_box_em_probe.py` with an existing FontTools
installation. The generator fixes the header timestamps for reproducible bytes.

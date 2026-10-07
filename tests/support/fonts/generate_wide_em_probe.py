"""Generate an original test-only font matching Qt Windows offscreen wide-fallback metrics.

No glyphs are copied from another font. Every mapped character is one original
rectangle. At 22px height its 28px advance models rounded Windows 125% widths.
This generator and its generated font may be used under CC0-1.0.
Run with an already installed fontTools (no runtime dependency is required).
"""
from pathlib import Path
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

font = FontBuilder(1100, isTTF=True)
characters = (*range(32, 127), 0x2026)
glyph_names = ['.notdef', *(f'g{character}' for character in characters)]
font.setupGlyphOrder(glyph_names)
font.setupCharacterMap({character: f'g{character}' for character in characters})
glyphs = {}
for name in glyph_names:
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((1400, 0))
    pen.lineTo((1400, 1100))
    pen.lineTo((0, 1100))
    pen.closePath()
    glyphs[name] = pen.glyph()
font.setupGlyf(glyphs)
font.setupHorizontalMetrics({name: (1400, 0) for name in glyph_names})
font.setupHorizontalHeader(ascent=1100, descent=0, lineGap=165)
font.setupNameTable({
    'familyName': 'WebJam Wide Em Probe', 'styleName': 'Regular',
    'uniqueFontIdentifier': 'WebJam Wide Em Probe Regular',
    'fullName': 'WebJam Wide Em Probe', 'psName': 'WebJamWideEmProbe-Regular',
})
font.setupOS2(sTypoAscender=1100, sTypoDescender=0, sTypoLineGap=165,
              usWinAscent=1100, usWinDescent=0)
font.setupPost()
font.font['head'].created = 3873858609
font.font['head'].modified = 3873858609
font.save(Path(__file__).with_name('WideEmProbe.ttf'))

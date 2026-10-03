"""Generate an original test-only font matching Qt QFontEngineBox metrics.

No glyphs are copied from another font. Every mapped character is one original
square; one em advance and ink bounds reproduce Qt's missing-font box engine.
This generator and its generated font may be used under CC0-1.0.
Run with an already installed fontTools (no runtime dependency is required).
"""
from pathlib import Path
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

font = FontBuilder(1000, isTTF=True)
characters = (*range(32, 127), 0x2026)
glyph_names = ['.notdef', *(f'g{character}' for character in characters)]
font.setupGlyphOrder(glyph_names)
font.setupCharacterMap({character: f'g{character}' for character in characters})
glyphs = {}
for name in glyph_names:
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((1000, 0))
    pen.lineTo((1000, 1000))
    pen.lineTo((0, 1000))
    pen.closePath()
    glyphs[name] = pen.glyph()
font.setupGlyf(glyphs)
font.setupHorizontalMetrics({name: (1000, 0) for name in glyph_names})
font.setupHorizontalHeader(ascent=1000, descent=0, lineGap=150)
font.setupNameTable({
    'familyName': 'WebJam Box Em Probe', 'styleName': 'Regular',
    'uniqueFontIdentifier': 'WebJam Box Em Probe Regular',
    'fullName': 'WebJam Box Em Probe', 'psName': 'WebJamBoxEmProbe-Regular',
})
font.setupOS2(sTypoAscender=1000, sTypoDescender=0, sTypoLineGap=150,
              usWinAscent=1000, usWinDescent=0)
font.setupPost()
font.font['head'].created = 3873858609
font.font['head'].modified = 3873858609
font.save(Path(__file__).with_name('BoxEmProbe.ttf'))

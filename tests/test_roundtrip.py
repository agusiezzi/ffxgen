"""Regenerate both AE-saved reference presets from their XML and require a
byte-identical match (XMP tail excluded). Run: python3 tests/test_roundtrip.py"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import ffxgen

CASES = [
    ("examples/sampler.xml",     "reference/sampler_ae2026.ffx",     None),
    ("examples/2d_text_box.xml", "reference/2d_text_box_ae2026.ffx", "Pseudo/iezno TextBox"),
]
ok = True
for xml, ref, mn in CASES:
    xml, ref = os.path.join(ROOT, xml), os.path.join(ROOT, ref)
    ffxgen.adopt_constants(ref)
    ok &= ffxgen.verify(ffxgen.build(ffxgen.parse_effect(xml, mn)), ref)
sys.exit(0 if ok else 1)

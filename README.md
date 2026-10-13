# ffxgen — free pseudo effect creator for After Effects

Builds a pseudo-effect animation preset (`.ffx`) straight from a
`PresetEffects.xml`-style `<Effect>` definition. No After Effects involved,
no `PresetEffects.xml` editing, no Pseudo Effect Maker.

```
python3 ffxgen.py examples/demo.xml dist/demo.ffx
python3 ffxgen.py examples/demo.xml dist/demo.ffx --matchname "Pseudo/me Demo"
python3 ffxgen.py examples/sampler.xml --verify reference/sampler_ae2026.ffx
python3 ffxgen.py --dump dist/demo.ffx
```

Pure Python 3, no dependencies.

## How it was built

AE embeds the full panel definition inside an `.ffx` (a RIFX container).
Two presets saved from AE 2026 live in `reference/`:

- `sampler_ae2026.ffx` — one control per type and per attribute variant
  (`examples/sampler.xml`), so every byte difference maps to one feature.
- `2d_text_box_ae2026.ffx` — a real panel (`examples/2d_text_box.xml`).

`ffxgen` regenerates both **byte for byte** from their XML. That is the
acceptance test; run `python3 tests/test_roundtrip.py` (or the VS Code test
task). The only part not reproduced is the XMP metadata packet AE appends
*after* the RIFX payload, which AE does not need on read.

## Supported

All control types in Adobe's DTD: `Slider`, `Angle`, `Checkbox`, `Popup`,
`Color`, `Point`, `Point3D`, `Layer`, `Group` (nested, empty, `INVISIBLE`).

Attributes: `default*`, `valid_min/max`, `slider_min/max`, `precision`,
`DISPLAY_PERCENT`, `DISPLAY_PIXEL`, `INVISIBLE`, `CANNOT_TIME_VARY`,
`popup_string`. Names may use Adobe's `$$$/AE/Preset/Key=Display Name` form.

Not stored in an `.ffx` by AE either, so ignored: `hold`, `default_self`,
`external_id`.

Limits (fixed-width binary fields): control names ≤ 31 bytes, matchnames
≤ 39 bytes. Use a `Pseudo/` matchname prefix to avoid the missing-effect
warning on other machines.

## Format notes (from the sampler)

- `parT` list: per control, a 40-byte matchname (`<effect matchname>-NNNN`,
  numbered in order; `-0000` is the effect itself) + a 148-byte `pard`.
  Type at byte 12 (0 layer, 3 angle, 4 checkbox, 5 color, 6 point, 7 popup,
  10 slider, 13 group start, 14 group end, 18 point3D), flags at byte 4
  (`0x200` invisible, `0x8` group end), name at 16..47, `CANNOT_TIME_VARY`
  as `2` at byte 48. Popups put their items in a following `pdnm` string.
- `tdgp` list: per control a `tdbs` block with `tdsb` (3 = hidden), display
  name, a per-type 124-byte `tdb4` template, the value(s) in `cdat`, and for
  sliders the range in `tdum`/`tduM`.
- Point defaults are stored three ways (16.16 fixed fraction, fixed ×100,
  double); Point3D is stored ×65536; Color is ARGB; slider range doubles are
  rounded through float32 first.
- Three comp-specific stamps are constants (`BESO`, `TIMEBASE`, `LAYER_ID`);
  `--verify` adopts them from the reference file.

## Using the result

```javascript
layer.applyPreset(new File("/path/to/demo.ffx"));
var fx = layer.property("ADBE Effect Parade").property("FFX Gen Demo");
fx.property("Look").property("Amount").setValue(75);   // scripting: walk the group
// expressions flatten groups:  effect("FFX Gen Demo")("Amount")
```

`ae-scripts/Apply_FFX_Test.jsx` applies a chosen `.ffx` to the selected layer
and lists every control it finds — use it to confirm a generated panel on a
machine whose `PresetEffects.xml` has never seen the matchname.
`ae-scripts/Add_PseudoEffect_By_Matchname.jsx` is the XML-registration route,
kept for producing new reference files.

## Layout

```
ffxgen.py            the tool
examples/            XML panel definitions
reference/           AE-saved .ffx files used as ground truth
tests/               round-trip test
ae-scripts/          ExtendScript helpers for testing inside AE
dist/                generated output
```

## Status

Round-trip against AE-saved files: passing. Applying a generated `.ffx` with
a never-registered matchname inside AE: to be confirmed (try `dist/demo.ffx`).
Older-AE compatibility of the header is untested.

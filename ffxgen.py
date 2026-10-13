#!/usr/bin/env python3
"""
ffxgen.py - build an After Effects pseudo-effect animation preset (.ffx)
directly from a PresetEffects.xml-style <Effect> definition. No After
Effects, no PresetEffects.xml edit, no Pseudo Effect Maker.

    python3 ffxgen.py panel.xml out.ffx
    python3 ffxgen.py panel.xml out.ffx --matchname "Pseudo/me MyPanel"
    python3 ffxgen.py panel.xml --verify reference.ffx     # byte diff vs an AE-saved ffx
    python3 ffxgen.py --dump some.ffx                      # decode an ffx (debug)

Supported controls (the full PresetEffects.xml DTD): Slider, Angle, Checkbox,
Popup, Color, Point, Point3D, Layer, Group (nested, empty, INVISIBLE).
Attributes honoured: default*, valid_min/max, slider_min/max, precision,
DISPLAY_PERCENT, DISPLAY_PIXEL, INVISIBLE, CANNOT_TIME_VARY, popup_string.
Attributes AE does not store in an ffx (so neither do we): hold, default_self,
external_id.

Format knowledge comes from two AE 2026 reference files (a 2D Text Box panel
and a sampler with one control per type/attribute). The --verify mode
regenerates a panel from its XML and diffs it against the AE-saved file; both
references round-trip byte for byte, excluding the trailing XMP packet, which
AE appends outside the RIFX payload and does not need on read.
"""
import re
import struct
import sys
import xml.etree.ElementTree as ET

# --------------------------------------------------------------------------
# Constants lifted from the reference files
# --------------------------------------------------------------------------

HEAD = bytes.fromhex("00000003000000610000000700000000")

# Source-comp metadata (fps, size...). AE ignores it on apply; this is the
# sampler's block (25 fps).
BESO = bytes.fromhex(
    "00000001000000010000000000006400001900000000000007801388078013883ff0"
    "0000000000003ff000000000000000000000ffffffff")

# Timebase bytes AE writes into every tdb4 at [12:16]; 0x6400 = 25 fps source.
TIMEBASE = bytes.fromhex("00006400")

# Layer-id stamp AE writes for Layer controls (and the effect root). It is the
# id of the layer the preset was saved from; AE resolves it on apply.
LAYER_ID = 0x42


TYPE_LAYER, TYPE_ANGLE, TYPE_CHECK, TYPE_COLOR, TYPE_POINT, TYPE_POPUP = 0, 3, 4, 5, 6, 7
TYPE_BUILTIN, TYPE_SLIDER, TYPE_GSTART, TYPE_GEND, TYPE_POINT3D = 9, 10, 13, 14, 18

# tdb4 templates per type, with the timebase at [12:16] patched in at build time.
_TDB4 = {
    TYPE_LAYER:   "db9900010001000000010000XXXXXXXX3f1a36e2eb1c432d3ff00000000000003ff00000000000003ff00000000000003ff00000000000000000000404000000000000000000000080000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    TYPE_ANGLE:   "db99000100010000ffffffffXXXXXXXX3f1a36e2eb1c432d3ff00000000000003ff00000000000003ff00000000000003ff00000000000000000000406000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    TYPE_CHECK:   "db9900010001000000010004XXXXXXXX3f1a36e2eb1c432d3ff00000000000003ff00000000000003ff00000000000003ff00000000000000000000404000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    TYPE_COLOR:   "db990004000700010002ffffXXXXXXXX3f1a36e2eb1c432d3ff00000000000003ff00000000000003ff00000000000003ff00000000000000000000101000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    TYPE_POINT:   "db990002000f0003ffffffffXXXXXXXX3d9b7cdfd9d7bdbc3fd89374bc6a7efa3ff00000000000003ff00000000000003ff00000000000000000000406000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    TYPE_POPUP:   "db9900010001000000010004XXXXXXXX3f1a36e2eb1c432d3ff00000000000003ff00000000000003ff00000000000003ff00000000000000000000404000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    TYPE_SLIDER:  "db99000100010000ffffffffXXXXXXXX3f1a36e2eb1c432d3ff00000000000003ff00000000000003ff00000000000003ff00000000000000000000809000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    TYPE_GSTART:  "db9900010001000000010000XXXXXXXX3f1a36e2eb1c432d3ff00000000000003ff00000000000003ff00000000000003ff00000000000000000000809000000000000000000000080000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    TYPE_POINT3D: "db990003000f0003ffffffffXXXXXXXX3d9b7cdfd9d7bdbc3fd89374bc6a7efa3ff00000000000003ff00000000000003ff00000000000000000000809000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
}
_TDB4[TYPE_GEND] = _TDB4[TYPE_GSTART]


def tdb4(typ):
    return bytes.fromhex(_TDB4[typ].replace("XXXXXXXX", TIMEBASE.hex()))


# --------------------------------------------------------------------------
# RIFX helpers
# --------------------------------------------------------------------------

def chunk(tag, body):
    """Standard RIFF chunk: 4-byte tag, big-endian size, body, pad to even."""
    assert len(tag) == 4
    return tag + struct.pack(">I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def LIST(kind, *parts):
    return chunk(b"LIST", kind + b"".join(parts))


def utf8str(s):
    """AE string payload: 'Utf8' + u32 length + bytes, padded to even length
    with the pad INCLUDED in the chunk size (unlike plain RIFF padding)."""
    raw = s.encode("utf-8")
    body = b"Utf8" + struct.pack(">I", len(raw)) + raw
    if len(body) & 1:
        body += b"\0"
    return body


def tdmn(name):
    raw = name.encode("utf-8")
    if len(raw) > 39:
        raise ValueError(f"matchname too long (max 39 bytes): {name!r}")
    return chunk(b"tdmn", raw.ljust(40, b"\0"))


def fixed16(v):
    return struct.pack(">i", int(round(v * 65536)))


def f32(v):
    """Round through float32 (AE stores slider_min/max as float, then widens)."""
    return struct.unpack(">f", struct.pack(">f", v))[0]


# --------------------------------------------------------------------------
# XML parsing
# --------------------------------------------------------------------------

def clean_name(s):
    # "$$$/AE/Preset/Foo=Foo Bar" -> "Foo Bar"
    return s.split("=", 1)[1] if s.startswith("$$$/") else s


def truthy(v):
    return str(v).strip().lower() == "true"


def parse_effect(path, matchname=None):
    text = open(path, encoding="utf-8").read()
    # Tolerate a bare <Effect> (no root) and a leading DTD.
    text = re.sub(r"<!DOCTYPE.*?\]>", "", text, flags=re.S)
    root = ET.fromstring(text if text.lstrip().startswith("<?xml") or "<Effects" in text
                         else "<Effects>" + text + "</Effects>")
    eff = root if root.tag == "Effect" else root.find(".//Effect")
    if eff is None:
        raise ValueError("no <Effect> element found")
    controls = []

    def walk(parent):
        for el in parent:
            a = el.attrib
            c = {"tag": el.tag, "name": clean_name(a.get("name", "")),
                 "invisible": truthy(a.get("INVISIBLE", "false")),
                 "notimevary": truthy(a.get("CANNOT_TIME_VARY", "false"))}
            if el.tag == "Group":
                controls.append(dict(c, type=TYPE_GSTART))
                walk(el)
                controls.append({"tag": "GroupEnd", "name": "", "type": TYPE_GEND,
                                 "invisible": False, "notimevary": False})
                continue
            if el.tag == "Slider":
                vmin, vmax = float(a["valid_min"]), float(a["valid_max"])
                c.update(type=TYPE_SLIDER, default=float(a.get("default", 0)),
                         valid_min=vmin, valid_max=vmax,
                         slider_min=float(a.get("slider_min", vmin)),
                         slider_max=float(a.get("slider_max", vmax)),
                         precision=int(a.get("precision", 1)),
                         display=2 if truthy(a.get("DISPLAY_PIXEL", "false"))
                         else 1 if truthy(a.get("DISPLAY_PERCENT", "false")) else 0)
            elif el.tag == "Angle":
                c.update(type=TYPE_ANGLE, default=float(a.get("default", 0)))
            elif el.tag == "Checkbox":
                c.update(type=TYPE_CHECK, default=truthy(a.get("default", "false")))
            elif el.tag in ("Popup", "Popup_UTF8"):
                items = a["popup_string"].split("|")
                c.update(type=TYPE_POPUP, default=int(a.get("default", 1)),
                         items=a["popup_string"], count=len(items))
            elif el.tag == "Color":
                c.update(type=TYPE_COLOR, rgb=(int(a.get("default_red", 0)),
                                               int(a.get("default_green", 0)),
                                               int(a.get("default_blue", 0))))
            elif el.tag == "Point":
                c.update(type=TYPE_POINT, xy=(float(a.get("default_x", 0.5)),
                                              float(a.get("default_y", 0.5))))
            elif el.tag == "Point3D":
                c.update(type=TYPE_POINT3D, xyz=(float(a.get("default_x", 0.5)),
                                                 float(a.get("default_y", 0.5)),
                                                 float(a.get("default_z", 0.5))))
            elif el.tag == "Layer":
                c.update(type=TYPE_LAYER)
            else:
                raise ValueError(f"unsupported element <{el.tag}>")
            controls.append(c)

    walk(eff)
    return {"matchname": matchname or eff.attrib["matchname"],
            "name": clean_name(eff.attrib["name"]), "controls": controls}


# --------------------------------------------------------------------------
# Definition section (parT): one 148-byte 'pard' per control
# --------------------------------------------------------------------------

def pard(c):
    typ = c["type"]
    flags = 0x200 if c.get("invisible") else 0
    if typ == TYPE_GEND:
        flags |= 0x8
    name = c.get("name", "").encode("utf-8")
    if len(name) > 31:
        raise ValueError(f"control name too long (max 31 bytes): {c['name']!r}")
    b = bytearray(148)
    struct.pack_into(">I", b, 4, flags)
    struct.pack_into(">I", b, 12, typ)
    b[16:16 + len(name)] = name
    w = 48  # start of the per-type payload
    if typ in (TYPE_LAYER, TYPE_GSTART, TYPE_GEND) or c.get("notimevary"):
        struct.pack_into(">I", b, w, 2)
    if typ == TYPE_LAYER:
        struct.pack_into(">I", b, w + 80, 0xFFFFFFFF)
    elif typ == TYPE_ANGLE:
        d = fixed16(c["default"])
        b[w + 8:w + 12] = d
        b[w + 12:w + 16] = d
        b[w + 16:w + 24] = bytes.fromhex("ffff8ad000007530")  # valid +-30000
    elif typ == TYPE_COLOR:
        r, g, bb = c["rgb"]
        argb = bytes([255, r, g, bb])
        b[w + 8:w + 12] = argb
        b[w + 12:w + 16] = argb
    elif typ == TYPE_POINT:
        x, y = c["xy"]
        b[w + 8:w + 12] = fixed16(x)
        b[w + 12:w + 16] = fixed16(y)
        b[w + 20:w + 24] = fixed16(x * 100)
        b[w + 24:w + 28] = fixed16(y * 100)
    elif typ == TYPE_POINT3D:
        x, y, z = c["xyz"]
        for i, v in enumerate((x, y, z)):
            struct.pack_into(">d", b, w + 8 + 8 * i, v * 65536)
            struct.pack_into(">d", b, w + 32 + 8 * i, v * 100 * 65536)
    elif typ == TYPE_CHECK:
        if c["default"]:
            struct.pack_into(">I", b, w + 8, 1)
            b[w + 12] = 1
    elif typ == TYPE_POPUP:
        struct.pack_into(">I", b, w + 8, c["default"])
        struct.pack_into(">HH", b, w + 12, c["count"], c["default"])
    elif typ == TYPE_SLIDER:
        struct.pack_into(">d", b, w + 8, c["default"])
        struct.pack_into(">fffff", b, w + 56, c["valid_min"], c["valid_max"],
                         c["slider_min"], c["slider_max"], c["default"])
        struct.pack_into(">HH", b, w + 76, c["precision"], c["display"])
    return chunk(b"pard", bytes(b))


def root_pard():
    b = bytearray(148)
    struct.pack_into(">I", b, 48, 2)
    struct.pack_into(">I", b, 128, 0xFFFFFFFF)
    return chunk(b"pard", bytes(b))


def builtin_pard():
    b = bytearray(148)
    struct.pack_into(">I", b, 12, TYPE_BUILTIN)
    return chunk(b"pard", bytes(b))


# --------------------------------------------------------------------------
# Value section (tdgp): one 'tdbs' LIST per control
# --------------------------------------------------------------------------

def cdat(typ, values):
    n = {TYPE_COLOR: 96, TYPE_POINT: 48, TYPE_POINT3D: 72}.get(typ, 40)
    body = b"".join(struct.pack(">d", v) for v in values)
    return chunk(b"cdat", body.ljust(n, b"\0"))


def tdbs(c):
    typ = c["type"]
    parts = [chunk(b"tdsb", struct.pack(">I", 3 if c.get("invisible") else 1)),
             chunk(b"tdsn", utf8str(c.get("name", ""))),
             chunk(b"tdb4", tdb4(typ))]
    if typ == TYPE_SLIDER:
        parts += [cdat(typ, [c["default"]]),
                  chunk(b"tdum", struct.pack(">d", f32(c["slider_min"]))),
                  chunk(b"tduM", struct.pack(">d", f32(c["slider_max"])))]
    elif typ == TYPE_ANGLE:
        parts.append(cdat(typ, [c["default"]]))
    elif typ == TYPE_CHECK:
        parts.append(cdat(typ, [1.0 if c["default"] else 0.0]))
    elif typ == TYPE_POPUP:
        parts.append(cdat(typ, [float(c["default"])]))
    elif typ == TYPE_COLOR:
        parts.append(cdat(typ, [255.0] + [float(v) for v in c["rgb"]]))
    elif typ == TYPE_POINT:
        parts.append(cdat(typ, list(c["xy"])))
    elif typ == TYPE_POINT3D:
        parts.append(cdat(typ, [v * 65536 for v in c["xyz"]]))
    elif typ == TYPE_LAYER:
        parts += [cdat(typ, []), chunk(b"tdpi", struct.pack(">I", LAYER_ID)),
                  chunk(b"tdps", b"\0\0\0\0")]
    else:  # groups
        parts.append(cdat(typ, []))
    return LIST(b"tdbs", *parts)


def root_tdbs():
    return LIST(b"tdbs",
                chunk(b"tdsb", struct.pack(">I", 3)),
                chunk(b"tdsn", utf8str("")),
                chunk(b"tdb4", tdb4(TYPE_LAYER)),
                cdat(TYPE_LAYER, []),
                chunk(b"tdpi", struct.pack(">I", LAYER_ID)),
                chunk(b"tdps", b"\0\0\0\0"))


# --------------------------------------------------------------------------
# Assemble
# --------------------------------------------------------------------------

def build(eff):
    mn, name, controls = eff["matchname"], eff["name"], eff["controls"]
    pmn = [f"{mn}-{i:04d}" for i in range(len(controls) + 1)]  # 0000 = effect root

    parT = [chunk(b"parn", struct.pack(">I", len(controls) + 2)),
            tdmn(pmn[0]), root_pard()]
    for i, c in enumerate(controls, 1):
        parT += [tdmn(pmn[i]), pard(c)]
        if c["type"] == TYPE_POPUP:
            parT.append(chunk(b"pdnm", utf8str(c["items"])))
        elif c["type"] == TYPE_CHECK:
            parT.append(chunk(b"pdnm", utf8str("")))
    parT += [tdmn("ADBE Effect Built In Params"), builtin_pard()]

    tdgp = [chunk(b"tdsb", struct.pack(">I", 1)),
            chunk(b"tdsn", utf8str(name)),
            tdmn(pmn[0]), root_tdbs()]
    for i, c in enumerate(controls, 1):
        tdgp += [tdmn(pmn[i]), tdbs(c)]
    tdgp += [tdmn("ADBE Effect Built In Params"), _builtin_block(),
             tdmn("ADBE Group End")]

    sspc = LIST(b"sspc",
                chunk(b"fnam", utf8str(name)),
                LIST(b"parT", *parT),
                LIST(b"tdgp", *tdgp),
                chunk(b"pgui", bytes(16)),
                chunk(b"elab", b"\xff"))

    besc = LIST(b"besc",
                chunk(b"beso", BESO),
                LIST(b"tdsp",
                     chunk(b"tdot", b"\xff\xff\xff\xff"),
                     chunk(b"tdpl", struct.pack(">I", 2)),
                     LIST(b"tdsi", chunk(b"tdix", b"\xff\xff\xff\xff"), tdmn("ADBE Effect Parade")),
                     LIST(b"tdsi", chunk(b"tdix", bytes(4)), tdmn(mn))),
                chunk(b"tdsn", utf8str(name)),
                LIST(b"tdsp",
                     chunk(b"tdot", b"\xff\xff\xff\xff"),
                     chunk(b"tdpl", struct.pack(">I", 1)),
                     LIST(b"tdsi", chunk(b"tdix", b"\xff\xff\xff\xff"), tdmn("ADBE End of path sentinel"))),
                sspc)

    payload = b"FaFX" + chunk(b"head", HEAD) + besc
    return b"RIFX" + struct.pack(">I", len(payload)) + payload


def _builtin_block():
    """'ADBE Effect Built In Params' tdgp LIST (Compositing Options), built from
    the same primitives so it stays byte-identical to the reference."""
    mask_opacity = LIST(b"tdbs",
                        chunk(b"tdsb", struct.pack(">I", 1)),
                        chunk(b"tdsn", utf8str("-_0_/-")),
                        chunk(b"tdb4", tdb4(TYPE_SLIDER)),
                        cdat(TYPE_SLIDER, [100.0]),
                        chunk(b"tdum", struct.pack(">d", 0.0)),
                        chunk(b"tduM", struct.pack(">d", 100.0)))
    gpu_tdb4 = bytearray(tdb4(TYPE_CHECK))
    gpu_tdb4[9] = 2  # 00000002 00000004 variant
    force_gpu = LIST(b"tdbs",
                     chunk(b"tdsb", struct.pack(">I", 1)),
                     chunk(b"tdsn", utf8str("-_0_/-")),
                     chunk(b"tdb4", bytes(gpu_tdb4)),
                     cdat(TYPE_CHECK, [1.0]))
    return LIST(b"tdgp",
                chunk(b"tdsb", struct.pack(">I", 1)),
                chunk(b"tdsn", utf8str("Compositing Options")),
                tdmn("ADBE Effect Mask Parade"),
                LIST(b"tdgp",
                     chunk(b"tdsb", struct.pack(">I", 1)),
                     chunk(b"tdsn", utf8str("-_0_/-")),
                     tdmn("ADBE Group End")),
                tdmn("ADBE Effect Mask Opacity"), mask_opacity,
                tdmn("ADBE Force CPU GPU"), force_gpu,
                tdmn("ADBE Group End"))


# --------------------------------------------------------------------------
# Verify / dump
# --------------------------------------------------------------------------

def rifx_payload(data):
    """Bytes inside the RIFX envelope; drops the XMP packet AE appends after it."""
    size = struct.unpack(">I", data[4:8])[0]
    return data[:8 + size]


def adopt_constants(reference_path):
    """Take the comp-specific stamps (beso, timebase, layer id) from an AE-saved
    file so a regenerated panel can be compared byte for byte."""
    global BESO, TIMEBASE, LAYER_ID
    ref = open(reference_path, "rb").read()
    i = ref.find(b"beso")
    BESO = ref[i + 8:i + 8 + 56]
    i = ref.find(b"tdb4")
    TIMEBASE = ref[i + 8 + 12:i + 8 + 16]
    i = ref.find(b"tdpi")
    LAYER_ID = struct.unpack(">I", ref[i + 8:i + 12])[0]


def verify(generated, reference_path):
    ref = rifx_payload(open(reference_path, "rb").read())
    gen = rifx_payload(generated)
    if ref == gen:
        print(f"OK: byte-identical to {reference_path} ({len(gen)} bytes, XMP tail excluded)")
        return True
    print(f"DIFF: generated {len(gen)} bytes vs reference {len(ref)} bytes")
    n = 0
    for i, (a, b) in enumerate(zip(gen, ref)):
        if a != b:
            print(f"  offset {i:#08x}: gen {a:02x} ref {b:02x}  context gen={gen[i-8:i+8].hex()} ref={ref[i-8:i+8].hex()}")
            n += 1
            if n >= 20:
                print("  ...")
                break
    return False


def dump(path):
    d = open(path, "rb").read()

    def walk(off, end, depth):
        while off + 8 <= end:
            tag = d[off:off + 4]
            size = struct.unpack(">I", d[off + 4:off + 8])[0]
            body = d[off + 8:off + 8 + size]
            if tag == b"LIST":
                print("  " * depth + "LIST " + body[:4].decode("latin1") + f" ({size})")
                walk(off + 12, off + 8 + size, depth + 1)
            else:
                s = repr(body.rstrip(b"\0")) if tag in (b"tdmn", b"tdsn", b"pdnm", b"fnam") else body.hex()
                print("  " * depth + tag.decode("latin1") + f" ({size}) " + s)
            off += 8 + size + (size & 1)
    print(d[:12])
    walk(12, len(rifx_payload(d)), 0)


def main(argv):
    if len(argv) >= 2 and argv[0] == "--dump":
        dump(argv[1])
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 1
    xml_path = argv[0]
    matchname = None
    if "--matchname" in argv:
        matchname = argv[argv.index("--matchname") + 1]
    if "--verify" in argv:
        ref_path = argv[argv.index("--verify") + 1]
        adopt_constants(ref_path)
        eff = parse_effect(xml_path, matchname)
        return 0 if verify(build(eff), ref_path) else 2
    eff = parse_effect(xml_path, matchname)
    data = build(eff)
    out = argv[1]
    open(out, "wb").write(data)
    kinds = {}
    for c in eff["controls"]:
        kinds[c["tag"]] = kinds.get(c["tag"], 0) + 1
    print(f"wrote {out}: '{eff['name']}' ({eff['matchname']}), {len(data)} bytes, "
          + ", ".join(f"{v} {k}" for k, v in kinds.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

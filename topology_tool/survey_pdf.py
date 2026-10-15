# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""Spot heights out of a site plan / survey plan PDF — no Qt.

A vector PDF from CAD carries the heights as texts («+564,89», «565.167»,
«KD: 565.95») next to a point symbol (a quartered circle, a dot, a cross).
This module finds those texts (with their true position and angle),
finds the small point symbols among the drawing's paths and pairs every
height with its symbol. Each result is a point record like the ones of a
survey file (see ``survey_points`` in the plugin) — in PAPER coordinates;
the dialog turns them into metres with the plan scale.

Texts come from pypdf's layout-mode text walker (it keeps the position of
every text show operation, also where the simple text visitor loses it),
paths from ``pdf_vectors.extract``.
"""
from __future__ import annotations

import math
import re

from . import pdf_vectors as pv

PT_TO_MM = pv.PT_TO_MM
MM = 1.0 / PT_TO_MM                  # page points per millimetre

# codes whose points are NOT on the ground (start unticked)
NOT_GROUND_CODES = ("KS", "SO", "SOHLE", "OK", "OKM", "OKW", "UK", "FFB",
                    "OKFF", "OKRF", "OKRD", "FOK", "RFB", "Floor level",
                    "FH", "TH", "DA", "SH")
SPOT = "Spot height"
FLOOR = "Floor level"

_NUM = r"\d{1,4}(?:[.,]\d{1,3})"
_HEIGHT = re.compile(
    r"^(?:(?P<pre>[A-Za-zÄÖÜäöü]{1,6})\s*\.?\s*:?\s*)?"
    r"(?P<sign>[+±\-−])?\s*(?P<num>" + _NUM + r")\s*"
    r"(?:m\s*(?:ü\.?\s*)?(?:NN|NHN)?|NN|NHN)?$")
_FLOOR = re.compile(r"=\s*(?P<sign>[+±\-−])?\s*(?P<num>" + _NUM + r")\s*$")
_SKIP = re.compile(r"%|Δ|∆|\bdh\b|^R\s*\d|^[Øø⌀]|\bx\b", re.IGNORECASE)


def _num(s):
    return float(s.replace(",", "."))


# ---------------------------------------------------------------------------
# texts with position and angle
# ---------------------------------------------------------------------------

def page_texts(reader, page_index):
    """``[{"x", "y", "angle", "size", "text"}]`` in page points (origin
    lower left of the visible page, like ``pdf_vectors``). One entry per
    BT…ET text object — fragments of one number written piece by piece
    come out joined."""
    pv._pypdf()
    from pypdf.generic import ContentStream
    from pypdf._text_extraction._layout_mode import _fixed_width_page as fw
    from pypdf._text_extraction._layout_mode._text_state_manager import \
        TextStateManager

    page = reader.pages[page_index]
    base, _w, _h = pv._page_base(page)
    try:
        fonts = page._layout_mode_fonts()
        content = page.get_contents()
        if content is None:
            return []
        ops = iter(ContentStream(content, page.pdf, "bytes").operations)
    except Exception:                                      # noqa: BLE001
        return []
    # the name differs between pypdf releases
    walk = getattr(fw, "recurse_to_target_op", None) or \
        getattr(fw, "recurs_to_target_op")
    out = []
    state = TextStateManager()
    import logging
    log = logging.getLogger("pypdf")
    old = log.level
    log.setLevel(logging.ERROR)
    try:
        for operands, op in ops:
            if op in (b"BT", b"q"):
                try:
                    bts, tjs = walk(
                        ops, state, b"ET" if op == b"BT" else b"Q", fonts,
                        False)
                except Exception:                          # noqa: BLE001
                    continue
                _collect(bts, tjs, base, out)
            elif op == b"Tf":
                try:
                    state.set_font(fw.resolve_font(fonts, operands[0]),
                                   operands[1])
                except Exception:                          # noqa: BLE001
                    pass
            else:
                try:
                    state.set_state_param(op, operands)
                except Exception:                          # noqa: BLE001
                    pass
    finally:
        log.setLevel(old)
    return out


# advance widths (em) of a typical sans-serif CAD font
_W = {".": 0.278, ",": 0.278, ":": 0.278, " ": 0.278, "+": 0.584,
      "-": 0.333, "±": 0.584, "=": 0.584, "%": 0.889, "/": 0.278,
      "(": 0.333, ")": 0.333, "I": 0.278, "i": 0.222, "l": 0.222,
      "1": 0.556, "f": 0.278, "t": 0.278, "r": 0.333, "m": 0.833,
      "M": 0.833, "W": 0.944, "w": 0.722}


def text_width(text, size):
    """Estimated length of a text line in page points."""
    return sum(_W.get(ch, 0.556 if ch.isdigit() else 0.6) for ch in text) * size


def _collect(bts, tjs, base, out):
    """One text per run of show operations with the same direction that
    follow on from each other (a number in pieces → one text). Every piece
    knows where it starts and — from the font's widths — where it ends."""
    cur = None
    for tj in tjs:
        t = getattr(tj, "txt", None) or getattr(tj, "text", "") or ""
        try:
            tm = tuple(float(v) for v in tj.transform)
            dm = tuple(float(v) for v in tj.displaced_transform())
        except Exception:                                  # noqa: BLE001
            continue
        m = pv._mul(tm, base)
        x, y = m[4], m[5]
        xe, ye = pv._apply(base, dm[4], dm[5])
        a, b = m[0], m[1]
        k = math.hypot(a, b) or 1.0
        ang = math.degrees(math.atan2(b, a))
        size = abs(float(tj.font_size or 0)) * k
        if not t.strip():
            if cur is not None and t:
                cur["text"] += t
                cur["ex"], cur["ey"] = xe, ye
            continue
        if cur is not None and abs(((ang - cur["angle"] + 180) % 360) - 180) < 2:
            sz = max(cur["size"], size)
            gap = math.hypot(x - cur["ex"], y - cur["ey"])
            same = math.hypot(x - cur["x"], y - cur["y"])
            if gap < 0.45 * sz or same < 0.05 * sz:
                cur["text"] += t
                cur["ex"], cur["ey"] = xe, ye
                continue
        cur = {"x": x, "y": y, "angle": ang, "size": size, "text": t,
               "ex": xe, "ey": ye}
        out.append(cur)
    for c in out:
        c["text"] = " ".join(c["text"].split())


# ---------------------------------------------------------------------------
# point symbols
# ---------------------------------------------------------------------------

def find_symbols(vec, min_mm=0.9, max_mm=5.5):
    """Small round-ish shapes (circles, dots, quartered circles, crosses
    drawn as curves) → symbol centres ``[(x, y, size_pt)]``. Pieces of one
    symbol (ring + dot, circle + filled quarters) become one."""
    lo, hi = min_mm * MM, max_mm * MM
    parts = []
    for p in vec.paths:
        xs = [x for run, _c in p.runs for x, _y in run]
        if not xs:
            continue
        ys = [y for run, _c in p.runs for _x, y in run]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        big = max(w, h)
        if not (lo <= big <= hi):
            continue
        if min(w, h) < 0.45 * big:
            continue
        nv = sum(len(r) for r, _c in p.runs)
        curved = any(c for _r, c in p.runs)
        if not curved and nv < 8:
            continue
        parts.append([(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                      min(xs), min(ys), max(xs), max(ys)])
    # cluster pieces whose centres lie within 1.6 mm (grid hashing)
    cell = 1.6 * MM
    grid = {}
    for i, q in enumerate(parts):
        grid.setdefault((int(q[0] // cell), int(q[1] // cell)), []).append(i)
    parent = list(range(len(parts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, q in enumerate(parts):
        gx, gy = int(q[0] // cell), int(q[1] // cell)
        for ix in (gx - 1, gx, gx + 1):
            for iy in (gy - 1, gy, gy + 1):
                for j in grid.get((ix, iy), ()):
                    if j > i and math.dist(q[:2], parts[j][:2]) < cell:
                        parent[find(j)] = find(i)
    groups = {}
    for i in range(len(parts)):
        groups.setdefault(find(i), []).append(parts[i])
    out = []
    for g in groups.values():
        x0 = min(q[2] for q in g)
        y0 = min(q[3] for q in g)
        x1 = max(q[4] for q in g)
        y1 = max(q[5] for q in g)
        out.append(((x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0)))
    return out


# ---------------------------------------------------------------------------
# heights
# ---------------------------------------------------------------------------

def parse_label(text):
    """A height text → ``(value, code)`` or None. Floor levels written as
    «-3,68 = +567,62» give the absolute value with code «Floor level»."""
    t = text.strip().replace("−", "-")
    if _SKIP.search(t):
        return None
    m = _FLOOR.search(t)
    if m and "=" in t:
        v = _num(m.group("num"))
        if m.group("sign") == "-":
            v = -v
        return v, FLOOR
    m = _HEIGHT.match(t)
    if not m:
        return None
    v = _num(m.group("num"))
    if m.group("sign") == "-":
        v = -v
    pre = (m.group("pre") or "").upper()
    if pre in ("M", "MASSSTAB", "M1"):
        return None
    signed = bool(m.group("sign")) or bool(pre)
    return v, (pre or SPOT), signed


def _seg_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def spot_heights(texts, symbols, link_mm=12.0):
    """Height texts paired with symbols → records (paper coordinates).
    Each record: ``{"label", "value", "code", "px", "py", "lx", "ly",
    "symbol": bool}``."""
    cands = []
    for t in texts:
        r = parse_label(t["text"])
        if r is None:
            continue
        if len(r) == 2:
            v, code = r
            signed = True
        else:
            v, code, signed = r
        cands.append((t, v, code, signed))
    if not cands:
        return [], {"absolute": False}
    # absolute heights (above sea level) or relative ones (±0,00 system)?
    big = sorted(v for _t, v, _c, _s in cands if abs(v) >= 20)
    absolute = len(big) >= max(2, len(cands) // 3)
    if absolute:
        med = big[len(big) // 2]
        keep = [c for c in cands if abs(c[1] - med) <= 80]
    else:
        keep = [c for c in cands if c[3]]          # relative: only ± / prefix
    recs = []
    for t, v, code, _s in keep:
        a = math.radians(t["angle"])
        w = text_width(t["text"], t["size"])
        ex, ey = t.get("ex"), t.get("ey")
        if ex is None or math.hypot(ex - t["x"], ey - t["y"]) < 0.3 * t["size"]:
            ex, ey = t["x"] + math.cos(a) * w, t["y"] + math.sin(a) * w
        recs.append({"label": t["text"], "value": v, "code": code,
                     "lx": t["x"], "ly": t["y"], "ex": ex, "ey": ey,
                     "size": t["size"]})
    # pair labels and symbols, nearest first
    lim = link_mm * MM
    pairs = []
    for i, r in enumerate(recs):
        for j, s in enumerate(symbols):
            if abs(s[0] - r["lx"]) > lim + 60 or abs(s[1] - r["ly"]) > lim + 60:
                continue
            d = _seg_dist(s[0], s[1], r["lx"], r["ly"], r["ex"], r["ey"])
            if d <= lim:
                pairs.append((d, i, j))
    pairs.sort()
    used_sym = {}
    for d, i, j in pairs:
        r = recs[i]
        if "px" in r:
            continue
        if j in used_sym:
            o = recs[used_sym[j]]
            # one symbol, two different heights: only for different kinds
            # (a manhole carries «KD» cover and «KS» invert)
            if abs(o["value"] - r["value"]) > 0.005 and o["code"] == r["code"]:
                continue
        r["px"], r["py"] = symbols[j][0], symbols[j][1]
        r["symbol"] = True
        used_sym.setdefault(j, i)
    for r in recs:
        if "px" not in r:
            r["px"], r["py"] = r["lx"], r["ly"]
            r["symbol"] = False
    return recs, {"absolute": absolute}


def read_pdf(path, page_index=0):
    """Everything the dialog needs for one sheet: ``{"recs", "scale",
    "scale_text", "pages", "vec", "texts", "info"}``."""
    reader = pv._reader(path)
    n = len(reader.pages)
    if not 0 <= page_index < n:
        raise pv.PdfImportError(f"The PDF has no page {page_index + 1}.")
    texts = page_texts(reader, page_index)
    vec = pv.extract(path, page_index)
    symbols = find_symbols(vec)
    recs, info = spot_heights(texts, symbols)
    name, scale, ptexts = pv.page_texts(path, page_index, reader)
    info["symbols"] = len(symbols)
    info["name"] = name
    return {"recs": recs, "scale": scale, "pages": n, "vec": vec,
            "texts": ptexts, "info": info}

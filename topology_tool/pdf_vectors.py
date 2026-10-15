# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""Vector geometry out of one PDF page — no rendering, no Qt.

Walks the page's content stream (and Form XObjects inside it) with the
graphics state (CTM, colours, line width) and returns every painted path
as polyline *runs* in page points, origin at the lower-left of the visible
page (CropBox), page rotation applied. Bezier segments are flattened; a
run of consecutive curve segments is flagged ``curved`` so the importer
can give it one curve id (an arc selects as one contour, like a drawn one).

Text (BT…ET), images and shadings are counted, never converted. Clipping
paths that are not painted (``W n``) are dropped.

PDF parsing itself is pypdf's (BSD-3, vendored in ``_vendor``).
"""
from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass, field

_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")


def _pypdf():
    """Import pypdf — the vendored copy first: the text positions need its
    layout walker as it is in this release (older pypdf releases split
    texts differently)."""
    if "pypdf" not in sys.modules and _VENDOR not in sys.path:
        sys.path.insert(0, _VENDOR)
    import pypdf  # noqa: F401
    return pypdf


PT_TO_MM = 25.4 / 72.0


@dataclass
class PdfPath:
    runs: list            # [(points [(x, y), ...], curved: bool), ...]
    stroke: bool
    fill: bool
    stroke_rgb: tuple
    fill_rgb: tuple
    width: float          # stroke width in page points (after CTM)
    evenodd: bool = False  # fill rule f* / B* / b*  (else non-zero)


@dataclass
class PageVectors:
    width: float          # visible page size in points
    height: float
    paths: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)   # skipped things


# ---- small matrix helpers (PDF: [a b c d e f], row vectors) ---------------
def _mul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D,
            c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _scale_of(m):
    a, b, c, d, _e, _f = m
    return math.sqrt(abs(a * d - b * c)) or 1.0


def _bezier(p0, p1, p2, p3, tol):
    """Flatten a cubic (already in page space); returns points after p0.
    Wang's bound: n = sqrt(3·M / (4·tol)) segments keep the polyline within
    ``tol`` of the curve (M = largest second difference) — a 1 mm glyph
    takes one or two segments, a big arc as many as it needs."""
    m = max(math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1]),
            math.hypot(p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1]))
    n = int(min(64, max(1, math.ceil(math.sqrt(0.75 * m / max(tol, 1e-6))))))
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u**3 * p0[0] + 3 * u * u * t * p1[0]
                    + 3 * u * t * t * p2[0] + t**3 * p3[0],
                    u**3 * p0[1] + 3 * u * u * t * p1[1]
                    + 3 * u * t * t * p2[1] + t**3 * p3[1]))
    return out


def _gray(v):
    return (v, v, v)


def _cmyk(c, m, y, k):
    return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))


def _color(ops):
    try:
        v = [float(o) for o in ops if not hasattr(o, "startswith")
             or not str(o).startswith("/")]
    except (TypeError, ValueError):
        return None
    if len(v) == 1:
        return _gray(v[0])
    if len(v) == 3:
        return tuple(v)
    if len(v) == 4:
        return _cmyk(*v)
    return None


class _State:
    __slots__ = ("ctm", "srgb", "frgb", "lw")

    def __init__(self, ctm):
        self.ctm = ctm
        self.srgb = (0.0, 0.0, 0.0)
        self.frgb = (0.0, 0.0, 0.0)
        self.lw = 1.0

    def copy(self):
        s = _State(self.ctm)
        s.srgb, s.frgb, s.lw = self.srgb, self.frgb, self.lw
        return s


class PdfImportError(Exception):
    """A PDF that cannot be read (encrypted, damaged, no such page)."""


def open_reader(path):
    return _reader(path)


def _reader(path):
    pypdf = _pypdf()
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            # Many CAD plots are "protected" with an empty user password
            # (printing allowed, editing not) — those open fine.
            if not reader.decrypt(""):
                raise PdfImportError("The PDF is password-protected.")
        return reader
    except PdfImportError:
        raise
    except Exception as exc:                               # noqa: BLE001
        raise PdfImportError(f"Cannot read the PDF: {exc}") from exc


def page_info(path) -> list:
    """``[(width_pt, height_pt), ...]`` per page, as displayed (rotation)."""
    out = []
    for page in _reader(path).pages:
        box = page.cropbox
        w, h = float(box.width), float(box.height)
        if int(page.get("/Rotate", 0) or 0) % 180 == 90:
            w, h = h, w
        out.append((w, h))
    return out


_SCALE_LINE = re.compile(r"^(.*?)[\s\t]*\bM?\s*1\s*:\s*(\d{1,5})\s*$")
_SCALE_ANY = re.compile(r"\b1\s*:\s*(\d{1,5})\b")


@dataclass
class PageText:
    x: float              # baseline start, page points (like PageVectors)
    y: float
    size: float           # text height in page points
    angle: float          # degrees, counter-clockwise
    text: str
    scale: bool = False   # reads like a plan scale «1:100»


def _title_from(text):
    name = scale = None
    for line in text.splitlines():
        line = line.strip()
        mt = _SCALE_LINE.match(line)
        if mt and len(mt.group(1).strip()) >= 3:
            name = " ".join(mt.group(1).split())
            scale = int(mt.group(2))
            break
    if scale is None:
        found = [int(v) for v in _SCALE_ANY.findall(text) if int(v) > 1]
        if found:
            scale = max(set(found), key=found.count)
    return name, scale


def page_texts(path, page_index: int, reader=None) -> tuple:
    """``(name, scale_n, [PageText])`` — the drawing label «Erdgeschoss
    1:100» CAD layouts print, and every text with its position for the
    page view. Slow-ish (pypdf text extraction): run it off the main
    thread for a whole set."""
    reader = reader or _reader(path)
    try:
        page = reader.pages[page_index]
        base, _w, _h = _page_base(page)
    except Exception:                                      # noqa: BLE001
        return None, None, []
    items = []

    def visit(text, cm, tm, _font, size):
        t = (text or "").strip()
        if not t:
            return
        try:
            m = _mul(_mul(tuple(float(v) for v in tm),
                          tuple(float(v) for v in cm)), base)
        except Exception:                                  # noqa: BLE001
            return
        a, b = m[0], m[1]
        k = math.hypot(a, b) or 1.0
        items.append(PageText(m[4], m[5], abs(float(size or 0)) * k,
                              math.degrees(math.atan2(b, a)), t,
                              bool(_SCALE_ANY.search(t))))

    try:
        text = page.extract_text(visitor_text=visit) or ""
    except Exception:                                      # noqa: BLE001
        return None, None, items
    name, scale = _title_from(text)
    return name, scale, items


def page_title(path, page_index: int, reader=None) -> tuple:
    """``(name, scale_n)`` — see :func:`page_texts`."""
    name, scale, _items = page_texts(path, page_index, reader)
    return name, scale


def page_count(path) -> int:
    return len(page_info(path))


def _page_base(page):
    """``(matrix, width, height)``: the crop origin to (0,0), then the page
    rotation (clockwise as displayed), so the result looks like the page
    on screen."""
    box = page.cropbox
    x0, y0 = float(box.left), float(box.bottom)
    w, h = float(box.width), float(box.height)
    rot = int(page.get("/Rotate", 0) or 0) % 360
    base = (1, 0, 0, 1, -x0, -y0)
    if rot == 90:
        base = _mul(base, (0, -1, 1, 0, 0, w))
        w, h = h, w
    elif rot == 180:
        base = _mul(base, (-1, 0, 0, -1, w, h))
    elif rot == 270:
        base = _mul(base, (0, 1, -1, 0, h, 0))
        w, h = h, w
    return base, w, h


def extract(path, page_index: int = 0, tol: float = 0.1) -> PageVectors:
    """All painted paths of page ``page_index`` (0-based). ``tol`` is the
    curve flattening tolerance in page points."""
    reader = _reader(path)
    from pypdf.generic import ContentStream

    if not 0 <= page_index < len(reader.pages):
        raise PdfImportError(f"The PDF has no page {page_index + 1}.")
    page = reader.pages[page_index]
    base, w, h = _page_base(page)

    out = PageVectors(width=w, height=h)
    counts = out.counts

    def bump(k, n=1):
        counts[k] = counts.get(k, 0) + n

    def walk(content, resources, ctm, depth):
        if depth > 12:
            return
        try:
            ops = ContentStream(content, reader).operations
        except Exception:                                  # noqa: BLE001
            bump("unreadable streams")
            return
        gs = _State(ctm)
        stack = []
        sub = []           # subpaths: list of [runs]; run = [pts, curved]
        cur = None         # current point (user space)
        start = None
        in_text = False

        def P(x, y):
            return _apply(gs.ctm, float(x), float(y))

        def new_run(curved, first):
            sub[-1].append([[first], curved])

        def seg_to(pt, curved):
            if not sub:
                sub.append([])
            runs = sub[-1]
            if not runs or runs[-1][1] != curved:
                prev = runs[-1][0][-1] if runs else P(*cur)
                new_run(curved, prev)
            return sub[-1][-1][0]

        def paint(stroke, fill, evenodd=False):
            if sub:
                runs = [(pts, c) for r in sub for pts, c in r if len(pts) > 1]
                if runs:
                    out.paths.append(PdfPath(
                        runs, stroke, fill, gs.srgb, gs.frgb,
                        gs.lw * _scale_of(gs.ctm), evenodd))
            sub.clear()

        for operands, op in ops:
            if isinstance(op, bytes):
                op = op.decode("latin-1")
            if in_text:
                if op == "ET":
                    in_text = False
                elif op in ("Tj", "TJ", "'", '"'):
                    bump("text")
                continue
            if op == "q":
                stack.append(gs.copy())
            elif op == "Q":
                if stack:
                    gs = stack.pop()
            elif op == "cm":
                gs.ctm = _mul(tuple(float(v) for v in operands), gs.ctm)
            elif op == "w":
                gs.lw = float(operands[0])
            elif op in ("RG", "G", "K", "SC", "SCN"):
                c = _color(operands)
                if c:
                    gs.srgb = c
            elif op in ("rg", "g", "k", "sc", "scn"):
                c = _color(operands)
                if c:
                    gs.frgb = c
            elif op == "m":
                cur = (float(operands[0]), float(operands[1]))
                start = cur
                sub.append([])
                sub[-1].append([[P(*cur)], False])
            elif op == "l":
                if cur is None:
                    continue
                nxt = (float(operands[0]), float(operands[1]))
                seg_to(nxt, False).append(P(*nxt))
                cur = nxt
            elif op in ("c", "v", "y"):
                if cur is None:
                    continue
                v = [float(o) for o in operands]
                if op == "c":
                    c1, c2, end = (v[0], v[1]), (v[2], v[3]), (v[4], v[5])
                elif op == "v":
                    c1, c2, end = cur, (v[0], v[1]), (v[2], v[3])
                else:
                    c1, c2, end = (v[0], v[1]), (v[2], v[3]), (v[2], v[3])
                pts = seg_to(end, True)
                pts.extend(_bezier(P(*cur), P(*c1), P(*c2), P(*end), tol))
                cur = end
            elif op == "h":
                if cur is not None and start is not None and sub:
                    if math.dist(P(*cur), P(*start)) > 1e-9:
                        seg_to(start, False).append(P(*start))
                    cur = start
            elif op == "re":
                x, y, rw, rh = (float(o) for o in operands)
                corners = [P(x, y), P(x + rw, y), P(x + rw, y + rh),
                           P(x, y + rh), P(x, y)]
                sub.append([[corners, False]])
                cur = start = (x, y)
            elif op in ("S", "s"):
                if op == "s" and cur is not None and start is not None \
                        and sub:
                    seg_to(start, False).append(P(*start))
                paint(True, False)
            elif op in ("f", "F", "f*"):
                paint(False, True, op == "f*")
            elif op in ("B", "B*", "b", "b*"):
                if op in ("b", "b*") and cur is not None \
                        and start is not None and sub:
                    if math.dist(P(*cur), P(*start)) > 1e-9:
                        seg_to(start, False).append(P(*start))
                paint(True, True, op.endswith("*"))
            elif op == "n":
                sub.clear()                       # clipping path only
            elif op == "BT":
                in_text = True
            elif op == "BI":
                bump("images")
            elif op == "sh":
                bump("shadings")
            elif op == "Do":
                name = operands[0]
                try:
                    xo = resources["/XObject"][name].get_object()
                except Exception:                          # noqa: BLE001
                    continue
                subtype = xo.get("/Subtype")
                if subtype == "/Form":
                    m = tuple(float(v) for v in xo.get(
                        "/Matrix", (1, 0, 0, 1, 0, 0)))
                    res = xo.get("/Resources")
                    res = res.get_object() if res is not None else resources
                    walk(xo, res, _mul(m, gs.ctm), depth + 1)
                elif subtype == "/Image":
                    bump("images")

    resources = page.get("/Resources")
    resources = resources.get_object() if resources is not None else {}
    contents = page.get_contents()
    if contents is not None:
        walk(contents, resources, base, 0)
    return out


# ---- storey levels read from the sheets ----------------------------------
_LEVEL_NUM = r"(±|[+\-−])?\s?(\d{1,3})[,.](\d{2})"
_LEVEL_ONLY = re.compile(r"^" + _LEVEL_NUM + r"$")
_LEVEL_REL_ABS = re.compile(r"^" + _LEVEL_NUM + r"\s*=\s*[+\-−±]?\s?\d")
_STORY_LABEL = re.compile(r"^(-?\d{1,2})\s+(\S.{0,30})$")

_ABBR = (("untergeschoss", "ug"), ("kellergeschoss", "kg"),
         ("gartengeschoss", "gg"), ("souterrain", "sg"),
         ("sockelgeschoss", "sg"), ("erdgeschoss", "eg"),
         ("obergeschoss", "og"), ("dachgeschoss", "dg"),
         ("staffelgeschoss", "sg"), ("zwischengeschoss", "zg"),
         ("galerie", "ga"), ("spitzboden", "sb"))


def _level_value(m) -> float:
    sign, whole, frac = m.group(1), m.group(2), m.group(3)
    v = int(whole) + int(frac) / 100.0
    return -v if sign in ("-", "−") else v


def story_key(name: str) -> str:
    """«1. Obergeschoss», «1. OG», «1.OG» → «1og»; «Erdgeschoss» → «eg»."""
    s = (name or "").lower()
    for long, short in _ABBR:
        s = s.replace(long, short)
    return re.sub(r"[^a-z0-9]", "", s)


def _is_storey_name(name) -> bool:
    """«Erdgeschoss», «1. OG», «UG» — a floor plan, not a section."""
    s = (name or "").lower()
    if any(long in s for long, _short in _ABBR) or "geschoss" in s:
        return True
    return bool(re.search(r"\b(ug|kg|gg|eg|og|dg|sg)\b", s))


def story_levels(texts) -> list:
    """Story markers as CAD sections/elevations print them — a level above
    a label «-1 GG», «0 EG», «1 1. OG» — as ``[(label, level_m)]``."""
    levels = [(t, _LEVEL_ONLY.match(t.text.strip())) for t in texts]
    levels = [(t, m) for t, m in levels if m]
    out = []
    for t in texts:
        ml = _STORY_LABEL.match(t.text.strip())
        if not ml or _LEVEL_ONLY.match(t.text.strip()):
            continue
        best = None
        for lv, m in levels:
            dx = abs(lv.x - t.x)
            dy = lv.y - t.y
            if dx < 3 * t.size and 0 < dy < 2.5 * max(t.size, 1.0):
                if best is None or dy < best[0]:
                    best = (dy, _level_value(m))
        if best is not None:
            out.append((ml.group(2).strip(), best[1]))
    return out


def page_level(texts):
    """A plan's own level marker «±0,00 = +571,30» (relative = absolute) —
    the most frequent one — or None."""
    found = {}
    for t in texts:
        m = _LEVEL_REL_ABS.match(t.text.strip())
        if m:
            v = round(_level_value(m), 2)
            found[v] = found.get(v, 0) + 1
    if not found:
        return None
    return max(found, key=lambda v: (found[v], -abs(v)))


def suggest_levels(names, texts_by_page):
    """Elevation suggestions per page index from the whole set.

    1. The story table found on sections/elevations («-1 GG -3,68») —
       matched to the page name (Gartengeschoss ↔ GG, 1. Obergeschoss ↔
       1. OG).
    2. Else the page's own «x = +abs» level marker, when there is exactly
       one kind of it or it matches a known story level.
    Returns ``({page: (level_m, source)}, typical_storey_height_or_None,
    [(label, level)] story table)``."""
    votes = {}
    for texts in texts_by_page.values():
        for label, lv in story_levels(texts):
            k = story_key(label)
            votes.setdefault(k, {}).setdefault(label, {})
            votes[k][label][lv] = votes[k][label].get(lv, 0) + 1
    table = {}
    for k, by_label in votes.items():
        label = max(by_label, key=lambda lb: sum(by_label[lb].values()))
        counts = {}
        for d in by_label.values():
            for lv, c in d.items():
                counts[lv] = counts.get(lv, 0) + c
        table[k] = (label, max(counts, key=counts.get))
    out = {}
    for i, name in names.items():
        k = story_key(name or "")
        if k and k in table:
            out[i] = (table[k][1], f"story {table[k][0]}")
            continue
        if not _is_storey_name(name):
            continue                    # sections, elevations, site plan
        own = page_level(texts_by_page.get(i, []))
        if own is not None:
            out[i] = (own, "level on the plan")
    levels = sorted(v for _l, v in table.values())
    diffs = [round(b - a, 2) for a, b in zip(levels, levels[1:])
             if b - a > 1.5]
    typical = max(set(diffs), key=diffs.count) if diffs else None
    story_list = sorted(((lb, v) for lb, v in table.values()),
                        key=lambda e: e[1])
    return out, typical, story_list


# ---- what is on a sheet: floor plan, elevation, section, site plan --------
_SIDES = (("N", ("nord", "north", "nordansicht")),
          ("S", ("süd", "sued", "sud", "south", "südansicht")),
          ("E", ("ost", "east", "ostansicht")),
          ("W", ("west", "westansicht")))
_SIDE_LETTER = {"n": "N", "s": "S", "o": "E", "e": "E", "w": "W"}


def classify_page(name, texts) -> tuple:
    """``(kind, sides, sure)`` — kind: "plan" | "site" | "elev" |
    "section" | "other"; sides: e.g. ["N"] or ["E", "W"] for elevations;
    sure: False when guessed from the content instead of the name."""
    s = (name or "").lower()
    words = re.findall(r"[a-zäöüß]+", s)
    if any(w.startswith(("ansicht", "elevation", "fassade", "facade"))
           for w in words):
        sides = []
        for side, keys in _SIDES:
            if any(k in s for k in keys):
                sides.append(side)
        if not sides:            # «Ansichten O W»: single letters
            for w in words[1:]:
                if len(w) == 1 and w in _SIDE_LETTER:
                    side = _SIDE_LETTER[w]
                    if side not in sides:
                        sides.append(side)
        return "elev", sides, bool(sides)
    if any(w.startswith(("schnitt", "section", "querschnitt",
                         "längsschnitt")) for w in words):
        return "section", [], True
    if any(w.startswith(("lageplan", "außenanlage", "aussenanlage",
                         "site", "freianlage")) for w in words):
        return "site", [], True
    if _is_storey_name(name):
        return "plan", [], True
    # nothing in the name: look at the content
    if len(story_levels(texts or [])) >= 2:
        return "elev", [], False             # storey markers → a view
    if page_level(texts or []) is not None:
        return "plan", [], False
    return "plan", [], False


def datum_y(texts):
    """Sheet y (points) of the ±0,00 storey line on an elevation/section —
    the story marker whose level reads 0 — or None."""
    ys = []
    for t in texts or []:
        m = _LEVEL_ONLY.match(t.text.strip())
        if m and abs(_level_value(m)) < 1e-9:
            # the level text sits on the storey line
            ys.append(t.y - 0.3 * max(t.size, 1.0))
    if not ys:
        return None
    ys.sort()
    return ys[len(ys) // 2]

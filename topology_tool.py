# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""Topology — terrain from contour lines or from points, for IngeTrazo.

An independent implementation for IngeTrazo; no third-party code is used.

* **Terrain from Contours** — select contour lines (loose edges and/or
  groups, e.g. an imported DWG) that lie at their altitudes. A regular grid
  is laid over them, every grid node gets its height by harmonic
  interpolation between the contours (linear ramps from one line to the
  next, no terraces), and the grid is turned into a triangulated surface
  clipped to the contours' envelope.
* **Terrain from Points** — select guide points (Tape Measure), survey
  points or the vertices of edges/groups. A Delaunay TIN goes exactly
  through every point; long edges on the rim can be trimmed.

Both: optional skirt (vertical sides down to a base) with a closed bottom,
the contours and a flat contour map inside the result, soft edges. The
terrain is ONE group that remembers its source and settings — select it
and run **Edit Terrain…** to change the resolution, skirt, etc. Every run
is one undo step; Preview shows the result live in the model.

Install: copy this file into the plugins folder
(Extensions ▸ Open plugins folder; on Windows %APPDATA%\\ingetrazo\\plugins)
and restart IngeTrazo. Needs IngeTrazo ≥ 0.5 (extension API 2).
"""
from __future__ import annotations

import json
import math
import time

KEY = "topology_tool"
LEGACY_KEYS = ("terrain_tool", "topoform")   # terrains made before the renames
TITLE = "Topology"
VERSION = "1.1"
SETTINGS_KEY = "plugins/topology_tool/params"
LEGACY_SETTINGS_KEY = "plugins/topoform/params"
MAX_CELLS_SIDE = 2000          # finer grids bring no detail, only memory
MIN_CELLS_SIDE = 10            # a coarser grid is no terrain any more
MAX_TRIANGLES = 1_000_000      # hard limit: ~2 KB per triangle in IngeTrazo
WARN_TRIANGLES = 300_000       # above this: slow, shown in orange
EPS = 1e-9


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

def default_params() -> dict:
    return {
        "res_mode": "cells",      # "cells" | "size"
        "cells": 150,             # cells on the long side
        "cell_size": 1.0,         # metres
        "boundary": "hull",       # "hull" | "box" (interpolated) | "cut"
        "peaks": "round",         # "flat" | "round" (summits & pits)
        "simplify": 0.0,          # metres, 0 = off   (contour clean-up)
        "smooth": 0,              # passes, 0 = off
        "min_len": 0.0,           # metres, 0 = off
        "max_edge": 0.0,          # metres, 0 = off  (points)
        "soft": True,
        "quads": False,           # contours: one quad per grid cell
        "smooth_r": 0.0,          # contours: smoothing radius, 0 = raw
        "skirt": True,
        "skirt_depth": 1.0,       # metres below the lowest point
        "bottom": True,
        "contours": False,        # include the contours (contour mode)
        "map": False,             # include a flat contour map at the base
    }


def normalize_params(p) -> dict:
    out = default_params()
    if isinstance(p, dict):
        for k, v in p.items():
            if k in out and (type(v) is type(out[k]) or
                             (isinstance(out[k], float) and
                              isinstance(v, (int, float)))):
                out[k] = type(out[k])(v)
    out["force"] = bool(p.get("force", False)) if isinstance(p, dict) else False
    out["smooth"] = max(0, min(10, int(out["smooth"])))
    out["simplify"] = max(0.0, float(out["simplify"]))
    out["smooth_r"] = max(0.0, float(out["smooth_r"]))
    out["min_len"] = max(0.0, float(out["min_len"]))
    out["cells"] = max(MIN_CELLS_SIDE, min(MAX_CELLS_SIDE, int(out["cells"])))
    out["cell_size"] = max(1e-4, float(out["cell_size"]))
    out["skirt_depth"] = max(0.0, float(out["skirt_depth"]))
    out["max_edge"] = max(0.0, float(out["max_edge"]))
    if out["res_mode"] not in ("cells", "size"):
        out["res_mode"] = "cells"
    if out["peaks"] not in ("flat", "round"):
        out["peaks"] = "round"
    if out["boundary"] not in ("hull", "box", "cut"):
        out["boundary"] = "hull"
    return out


def load_params() -> dict:
    try:
        from PySide6.QtCore import QSettings
        q = QSettings()
        raw = (q.value(SETTINGS_KEY, "")
               or q.value("plugins/terrain_tool/params", "")
               or q.value(LEGACY_SETTINGS_KEY, ""))
        if raw:
            return normalize_params(json.loads(raw))
    except Exception:  # noqa: BLE001
        pass
    return default_params()


def save_params(p: dict) -> None:
    try:
        from PySide6.QtCore import QSettings
        p = {k: v for k, v in p.items() if k != "force"}
        QSettings().setValue(SETTINGS_KEY, json.dumps(p))
    except Exception:  # noqa: BLE001
        pass


class TopoError(Exception):
    """A user-facing refusal."""


# ---------------------------------------------------------------------------
# Source data from the selection
# ---------------------------------------------------------------------------

def _xyz(p) -> tuple:
    return (float(p.x()), float(p.y()), float(p.z()))


def terrain_data(group):
    ext = getattr(group, "ext", None) or {}
    data = ext.get(KEY)
    if data is None:
        for k in LEGACY_KEYS:
            data = ext.get(k)
            if data is not None:
                break
    return data if isinstance(data, dict) and "mode" in data else None


def _world_edges(group):
    from core.group import world_mesh
    try:
        return list(world_mesh(group).edges)
    except Exception:  # noqa: BLE001
        return list(group.mesh.edges)


def gather_segments(scene):
    """Contour segments ``[(x0,y0,z0,x1,y1,z1), ...]`` from the selection:
    loose edges and every edge inside selected groups (a terrain made by
    this plugin is skipped)."""
    from core.group import Group
    from core.mesh import Edge
    segs = []
    seen = set()
    for ent in scene.selection:
        if isinstance(ent, Edge):
            edges = [ent]
        elif isinstance(ent, Group) and terrain_data(ent) is None:
            edges = _world_edges(ent)
        else:
            continue
        for e in edges:
            a, b = _xyz(e.a), _xyz(e.b)
            k = (a, b) if a <= b else (b, a)
            if k in seen or a == b:
                continue
            seen.add(k)
            segs.append(a + b)
    return segs


def gather_points(scene):
    """Points ``[(x, y, z), ...]`` from the selection: guide points, the
    vertices of selected edges, faces and groups. Returns ``(points,
    source_label)``; with nothing usable selected, the document's survey
    points are used."""
    from core.group import Group
    from core.mesh import Edge, Face
    try:
        from core.guide import Guide
    except Exception:  # noqa: BLE001
        Guide = None
    pts = []
    n_guides = 0
    for ent in scene.selection:
        if Guide is not None and isinstance(ent, Guide):
            if not ent.is_line:
                pts.append(_xyz(ent.point))
                n_guides += 1
        elif isinstance(ent, Edge):
            pts.append(_xyz(ent.a))
            pts.append(_xyz(ent.b))
        elif isinstance(ent, Face):
            pts.extend(_xyz(v) for v in ent.vertices)
        elif isinstance(ent, Group) and terrain_data(ent) is None:
            for e in _world_edges(ent):
                pts.append(_xyz(e.a))
                pts.append(_xyz(e.b))
    label = "selection"
    if not pts:
        gps = list(getattr(scene, "geo_points", None) or [])
        if gps:
            pts = [_xyz(g.position) for g in gps]
            label = "survey points of the document"
    return dedupe_points(pts), label


def dedupe_points(pts, tol=1e-4):
    """One point per XY spot (within ``tol``); same spot, different heights
    → their mean."""
    acc: dict = {}
    for x, y, z in pts:
        k = (round(x / tol), round(y / tol))
        if k in acc:
            s = acc[k]
            s[2] += z
            s[3] += 1
        else:
            acc[k] = [x, y, z, 1]
    return [(s[0], s[1], s[2] / s[3]) for s in acc.values()]


def chain_segments(segs, tol=1e-6):
    """Join segments into polylines (lists of (x, y, z)); a closed loop
    repeats its first point at the end."""
    def key(x, y, z):
        return (round(x / tol), round(y / tol), round(z / tol))

    adj: dict = {}
    pos: dict = {}
    edges = []
    for i, s in enumerate(segs):
        ka, kb = key(*s[:3]), key(*s[3:])
        pos[ka] = tuple(s[:3])
        pos[kb] = tuple(s[3:])
        adj.setdefault(ka, []).append(i)
        adj.setdefault(kb, []).append(i)
        edges.append((ka, kb))
    used = [False] * len(segs)

    def walk(start_k, first_e):
        line = [start_k]
        k, e = start_k, first_e
        while e is not None:
            used[e] = True
            a, b = edges[e]
            k = b if a == k else a
            line.append(k)
            if len(adj[k]) != 2:
                break
            nxt = [j for j in adj[k] if not used[j]]
            e = nxt[0] if nxt else None
        return line

    lines = []
    # Open chains first (from ends / junctions), then the closed loops.
    for k, es in adj.items():
        if len(es) != 2:
            for e in es:
                if not used[e]:
                    lines.append(walk(k, e))
    for e in range(len(segs)):
        if not used[e]:
            lines.append(walk(edges[e][0], e))
    return [[pos[k] for k in ln] for ln in lines]


# ---------------------------------------------------------------------------
# Geometry helpers (numpy)
# ---------------------------------------------------------------------------

def convex_hull(xy):
    """Monotone chain; CCW, no repeated end point. ``xy``: (n, 2)."""
    pts = sorted(set(map(tuple, xy)))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _inside_convex(poly, X, Y, eps):
    """Vectorised point-in-convex-polygon (CCW) for arrays X, Y."""
    import numpy as np
    inside = np.ones(X.shape, dtype=bool)
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        inside &= ((bx - ax) * (Y - ay) - (by - ay) * (X - ax)) >= -eps
    return inside


def _clip_convex(subject, clip):
    """Sutherland–Hodgman: polygon ``subject`` clipped by CCW convex
    ``clip``. Lists of (x, y)."""
    out = list(subject)
    n = len(clip)
    for i in range(n):
        if not out:
            break
        ax, ay = clip[i]
        bx, by = clip[(i + 1) % n]

        def side(p):
            return (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)

        inp, out = out, []
        for j in range(len(inp)):
            p, q = inp[j], inp[(j + 1) % len(inp)]
            sp, sq = side(p), side(q)
            if sp >= 0:
                out.append(p)
            if (sp >= 0) != (sq >= 0):
                t = sp / (sp - sq)
                out.append((p[0] + (q[0] - p[0]) * t,
                            p[1] + (q[1] - p[1]) * t))
    return out


# ---------------------------------------------------------------------------
# Contours → height grid (harmonic interpolation, multigrid cascade)
# ---------------------------------------------------------------------------

def _rasterize(S, x0, y0, h, nx, ny):
    """Contour samples onto the nodes of a grid: ``(fixed mask, values)``."""
    import numpy as np
    A, B = S[:, :3], S[:, 3:]
    L = np.hypot(B[:, 0] - A[:, 0], B[:, 1] - A[:, 1])
    n = np.ceil(L / (h * 0.3)).astype(np.int64) + 1
    seg = np.repeat(np.arange(len(S)), n)
    start = np.cumsum(n) - n
    t = (np.arange(int(n.sum())) - start[seg]) / np.maximum(n - 1, 1)[seg]
    P = A[seg] + (B[seg] - A[seg]) * t[:, None]
    ix = np.rint((P[:, 0] - x0) / h).astype(np.int64)
    iy = np.rint((P[:, 1] - y0) / h).astype(np.int64)
    ok = (ix >= 0) & (ix <= nx) & (iy >= 0) & (iy <= ny)
    ix, iy, z = ix[ok], iy[ok], P[ok, 2]
    flat = iy * (nx + 1) + ix
    size = (ny + 1) * (nx + 1)
    s = np.bincount(flat, weights=z, minlength=size)
    c = np.bincount(flat, minlength=size)
    fixed = c > 0
    vals = np.zeros(size)
    vals[fixed] = s[fixed] / c[fixed]
    return fixed.reshape(ny + 1, nx + 1), vals.reshape(ny + 1, nx + 1)


def _relax(Z, fixed, iters, omega, tol):
    """Red–black SOR for Laplace's equation; Neumann on the grid border,
    the contour nodes held."""
    import numpy as np
    ny1, nx1 = Z.shape
    jj, ii = np.meshgrid(np.arange(nx1), np.arange(ny1))
    masks = [((ii + jj) % 2 == par) & ~fixed for par in (0, 1)]
    for _ in range(iters):
        change = 0.0
        for m in masks:
            P = np.pad(Z, 1, mode="edge")
            avg = (P[:-2, 1:-1] + P[2:, 1:-1] + P[1:-1, :-2] + P[1:-1, 2:]) * 0.25
            d = (avg - Z)[m]
            Z[m] += omega * d
            if d.size:
                change = max(change, float(np.abs(d).max()))
        if change < tol:
            break
    return Z


def _upsample(Zc, ny, nx, factor):
    """Bilinear: coarse grid (spacing ``factor``·h) → fine (ny+1, nx+1)."""
    import numpy as np
    cy, cx = Zc.shape
    u = np.minimum(np.arange(nx + 1) / factor, cx - 1)
    v = np.minimum(np.arange(ny + 1) / factor, cy - 1)
    j0 = np.minimum(np.floor(u).astype(int), cx - 2) if cx > 1 else np.zeros(nx + 1, int)
    i0 = np.minimum(np.floor(v).astype(int), cy - 2) if cy > 1 else np.zeros(ny + 1, int)
    fu = (u - j0)[None, :]
    fv = (v - i0)[:, None]
    j1 = np.minimum(j0 + 1, cx - 1)
    i1 = np.minimum(i0 + 1, cy - 1)
    z00 = Zc[np.ix_(i0, j0)]
    z01 = Zc[np.ix_(i0, j1)]
    z10 = Zc[np.ix_(i1, j0)]
    z11 = Zc[np.ix_(i1, j1)]
    return (z00 * (1 - fu) * (1 - fv) + z01 * fu * (1 - fv)
            + z10 * (1 - fu) * fv + z11 * fu * fv)


def solve_height_grid(S, x0, y0, h, nx, ny):
    """Height of every node of the (ny+1)×(nx+1) grid from contour segments
    ``S`` (m, 6)."""
    import numpy as np
    zr = float(S[:, [2, 5]].max() - S[:, [2, 5]].min()) or 1.0
    tol = zr * 1e-5
    levels = 0
    while max(nx, ny) / (2 ** (levels + 1)) >= 24:
        levels += 1
    Z = None
    for lev in range(levels, -1, -1):
        f = 2 ** lev
        cnx, cny = math.ceil(nx / f), math.ceil(ny / f)
        fixed, vals = _rasterize(S, x0, y0, h * f, cnx, cny)
        if Z is None:
            Z = np.full((cny + 1, cnx + 1), float(vals[fixed].mean()))
            iters = 4000
        else:
            Z = _upsample(Z, cny, cnx, 2)
            iters = 400
        Z[fixed] = vals[fixed]
        omega = 2.0 / (1.0 + math.sin(math.pi / max(cnx, cny, 2)))
        Z = _relax(Z, fixed, iters, min(omega, 1.97), tol)
    return Z, fixed


def _components(free):
    """4-connected components of the True cells of ``free`` → label array
    (-1 = not free) and the component count."""
    import numpy as np
    ny1, nx1 = free.shape
    lab = np.full(free.shape, -1, dtype=np.int64)
    fl = free.ravel()
    lb = lab.ravel()
    n = 0
    for start in np.flatnonzero(fl).tolist():
        if lb[start] >= 0:
            continue
        lb[start] = n
        stack = [start]
        while stack:
            k = stack.pop()
            r, c = divmod(k, nx1)
            if c > 0 and fl[k - 1] and lb[k - 1] < 0:
                lb[k - 1] = n
                stack.append(k - 1)
            if c < nx1 - 1 and fl[k + 1] and lb[k + 1] < 0:
                lb[k + 1] = n
                stack.append(k + 1)
            if r > 0 and fl[k - nx1] and lb[k - nx1] < 0:
                lb[k - nx1] = n
                stack.append(k - nx1)
            if r < ny1 - 1 and fl[k + nx1] and lb[k + nx1] < 0:
                lb[k + nx1] = n
                stack.append(k + nx1)
        n += 1
    return lab, n


def _chamfer(region, seed):
    """Distance (in cells, chamfer 1/√2) of every ``region`` cell from the
    ``seed`` cells — two raster passes."""
    import numpy as np
    INF = 1e18
    d = np.where(seed, 0.0, INF)
    ny1, nx1 = d.shape
    r2 = math.sqrt(2.0)
    D = d.tolist()
    R = (region | seed).tolist()
    for i in range(ny1):
        row, up = D[i], D[i - 1] if i else None
        for j in range(nx1):
            if not R[i][j]:
                continue
            v = row[j]
            if j and row[j - 1] + 1 < v:
                v = row[j - 1] + 1
            if up is not None:
                if up[j] + 1 < v:
                    v = up[j] + 1
                if j and up[j - 1] + r2 < v:
                    v = up[j - 1] + r2
                if j < nx1 - 1 and up[j + 1] + r2 < v:
                    v = up[j + 1] + r2
            row[j] = v
    for i in range(ny1 - 1, -1, -1):
        row, dn = D[i], D[i + 1] if i < ny1 - 1 else None
        for j in range(nx1 - 1, -1, -1):
            if not R[i][j]:
                continue
            v = row[j]
            if j < nx1 - 1 and row[j + 1] + 1 < v:
                v = row[j + 1] + 1
            if dn is not None:
                if dn[j] + 1 < v:
                    v = dn[j] + 1
                if j < nx1 - 1 and dn[j + 1] + r2 < v:
                    v = dn[j + 1] + r2
                if j and dn[j - 1] + r2 < v:
                    v = dn[j - 1] + r2
            row[j] = v
    return np.asarray(D)


def round_extrema(Z, fixed, h):
    """Summits and pits: the harmonic solution leaves the area inside the
    highest (lowest) closed contour flat. Each such area is lifted (lowered)
    into a dome that leaves the contour with the slope the terrain has just
    outside it and flattens out at the top:

        z = L ± s · (d − d² / 2D)

    d = distance from the contour, D = the largest d in the area, s = the
    mean slope in a ring just outside the contour. Areas open to the grid
    border are left as they are (no way to tell up from down there)."""
    import numpy as np
    ny1, nx1 = Z.shape
    lab, n = _components(~fixed)
    if n == 0:
        return Z
    gy, gx = np.gradient(Z, h)
    slope = np.hypot(gx, gy)
    Z = Z.copy()
    for k in range(n):
        reg = lab == k
        ys, xs = np.nonzero(reg)
        if (ys.min() == 0 or xs.min() == 0 or ys.max() == ny1 - 1
                or xs.max() == nx1 - 1):
            continue                                    # open to the border
        y0, y1 = max(ys.min() - 3, 0), min(ys.max() + 4, ny1)
        x0, x1 = max(xs.min() - 3, 0), min(xs.max() + 4, nx1)
        R = reg[y0:y1, x0:x1]
        F = fixed[y0:y1, x0:x1]
        P = np.pad(R, 1)
        near = (P[:-2, 1:-1] | P[2:, 1:-1] | P[1:-1, :-2] | P[1:-1, 2:]
                | P[:-2, :-2] | P[:-2, 2:] | P[2:, :-2] | P[2:, 2:])
        border = near & F & ~R                          # its contour nodes
        if not border.any():
            continue
        zb = Z[y0:y1, x0:x1][border]
        L = float(np.median(zb))
        if float(zb.max() - zb.min()) > 1e-6 * max(1.0, abs(L)):
            continue                                    # bounded by >1 level
        # A ring of free nodes just outside the contour: which way is down,
        # and how steep.
        Pb = np.pad(border, 2)
        ring = np.zeros_like(R)
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                ring |= Pb[2 + dy:Pb.shape[0] - 2 + dy, 2 + dx:Pb.shape[1] - 2 + dx]
        ring &= ~R & ~F
        if not ring.any():
            continue
        zo = Z[y0:y1, x0:x1][ring]
        sgn = 1.0 if float(zo.mean()) < L else -1.0     # summit / pit
        s = float(slope[y0:y1, x0:x1][ring].mean())
        if s <= 0:
            continue
        d = _chamfer(R, border) * h
        dmax = float(d[R].max())
        if dmax <= 0:
            continue
        dd = d[R]
        sub = Z[y0:y1, x0:x1]
        sub[R] = L + sgn * s * (dd - dd * dd / (2.0 * dmax))
    return Z


def largest_inner_rect(hull, res=400):
    """The largest axis-aligned rectangle inside the convex polygon
    ``hull`` → (x0, y0, x1, y1). Rasterised (``res`` nodes on the long
    side) + the classic largest-rectangle-in-a-histogram per row; the four
    corners are hull nodes, so the rectangle lies inside the hull."""
    import numpy as np
    hx = [q[0] for q in hull]
    hy = [q[1] for q in hull]
    bx0, bx1, by0, by1 = min(hx), max(hx), min(hy), max(hy)
    step = max(bx1 - bx0, by1 - by0) / res
    xs = bx0 + np.arange(int(math.floor((bx1 - bx0) / step)) + 1) * step
    ys = by0 + np.arange(int(math.floor((by1 - by0) / step)) + 1) * step
    X, Y = np.meshgrid(xs, ys)
    M = _inside_convex(hull, X, Y, step * 1e-6)
    ny1, nx1 = M.shape
    hgt = np.zeros(nx1, dtype=np.int64)
    best = (0.0, None)
    for i in range(ny1):
        hgt = np.where(M[i], hgt + 1, 0)
        H = hgt.tolist() + [0]
        stack = []
        for j, hj in enumerate(H):
            start = j
            while stack and stack[-1][1] >= hj:
                s0, sh = stack.pop()
                w = j - s0                       # nodes
                area = (w - 1) * (sh - 1)        # cells
                if sh >= 2 and w >= 2 and area > best[0]:
                    best = (area, (s0, i - sh + 1, j - 1, i))
                start = s0
            stack.append((start, hj))
    if best[1] is None:
        raise TopoError("No rectangle fits inside the data.")
    j0, i0, j1, i1 = best[1]
    return (float(xs[j0]), float(ys[i0]), float(xs[j1]), float(ys[i1]))


def clip_tin_to_rect(V, T, rect):
    """Cut a TIN at an axis-aligned rectangle; new rim vertices take their
    height from the triangle they lie in."""
    import numpy as np
    x0, y0, x1, y1 = rect
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    tx0 = np.minimum(np.minimum(A[:, 0], B[:, 0]), C[:, 0])
    tx1 = np.maximum(np.maximum(A[:, 0], B[:, 0]), C[:, 0])
    ty0 = np.minimum(np.minimum(A[:, 1], B[:, 1]), C[:, 1])
    ty1 = np.maximum(np.maximum(A[:, 1], B[:, 1]), C[:, 1])
    inside = (tx0 >= x0) & (tx1 <= x1) & (ty0 >= y0) & (ty1 <= y1)
    outside = (tx1 <= x0) | (tx0 >= x1) | (ty1 <= y0) | (ty0 >= y1)
    keep = [T[inside]]
    rect_poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    extra_v, extra_t = [], []
    base = len(V)
    for t in np.nonzero(~inside & ~outside)[0].tolist():
        a, b, c = T[t].tolist()
        pa, pb, pc = V[a], V[b], V[c]
        poly = _clip_convex([(pa[0], pa[1]), (pb[0], pb[1]), (pc[0], pc[1])],
                            rect_poly)
        if len(poly) < 3:
            continue
        det = ((pb[1] - pc[1]) * (pa[0] - pc[0])
               + (pc[0] - pb[0]) * (pa[1] - pc[1]))
        if abs(det) < 1e-18:
            continue
        ids = []
        for (px, py) in poly:
            l1 = ((pb[1] - pc[1]) * (px - pc[0]) + (pc[0] - pb[0]) * (py - pc[1])) / det
            l2 = ((pc[1] - pa[1]) * (px - pc[0]) + (pa[0] - pc[0]) * (py - pc[1])) / det
            z = l1 * pa[2] + l2 * pb[2] + (1 - l1 - l2) * pc[2]
            ids.append(base + len(extra_v))
            extra_v.append((px, py, z))
        for k in range(1, len(ids) - 1):
            extra_t.append((ids[0], ids[k], ids[k + 1]))
    if extra_v:
        V = np.vstack([V, np.asarray(extra_v)])
        keep.append(np.asarray(extra_t, dtype=np.int64))
    T2 = np.vstack([k_.reshape(-1, 3) for k_ in keep]).astype(np.int64)
    span = max(x1 - x0, y1 - y0)
    return weld(V, T2, span * 1e-9)


def rim_points(V, spacing, k=12):
    """Extra points along the bounding box (corners included), their height
    by inverse-distance weighting of the ``k`` nearest data points — the
    surface is carried out to a full rectangle."""
    import numpy as np
    x0, y0 = V[:, 0].min(), V[:, 1].min()
    x1, y1 = V[:, 0].max(), V[:, 1].max()
    out = []
    for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x1, y0, x1, y1),
                             (x1, y1, x0, y1), (x0, y1, x0, y0)):
        L = math.hypot(bx - ax, by - ay)
        m = max(1, int(math.ceil(L / spacing)))
        for i in range(m):                       # end = next side's start
            t = i / m
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    R = np.asarray(out, dtype=float)
    k = min(k, len(V))
    Z = np.empty(len(R))
    for s0 in range(0, len(R), 64):
        r = R[s0:s0 + 64]
        d2 = ((r[:, None, 0] - V[None, :, 0]) ** 2
              + (r[:, None, 1] - V[None, :, 1]) ** 2)
        idx = np.argpartition(d2, k - 1, axis=1)[:, :k]
        dk = np.take_along_axis(d2, idx, 1)
        w = 1.0 / np.maximum(dk, 1e-12)
        Z[s0:s0 + 64] = (w * V[idx, 2]).sum(1) / w.sum(1)
    pts = np.column_stack([R, Z])
    # Drop rim points that coincide with data points.
    key = {(round(x, 6), round(y, 6)) for x, y in V[:, :2].tolist()}
    keep = [i for i, (x, y) in enumerate(R.tolist())
            if (round(x, 6), round(y, 6)) not in key]
    return pts[keep]


def _box_blur(A, r, axis):
    """Moving average of width 2r+1 along ``axis`` (edges repeated)."""
    import numpy as np
    if r <= 0:
        return A
    pad = [(0, 0), (0, 0)]
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(A, pad, mode="edge"), axis=axis)
    n = A.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return (hi - lo) / (2 * r + 1)


def _gauss(A, sigma_cells):
    """Gaussian blur ≈ three box blurs (fast at any radius)."""
    r = int(round((math.sqrt(4.0 * sigma_cells ** 2 + 1.0) - 1.0) / 2.0))
    for _ in range(3):
        A = _box_blur(_box_blur(A, r, 0), r, 1)
    return A


def smooth_surface(Z, fixed, vals, radius, h, iters=3):
    """«Perfect» instead of «raw»: blur away the creases the contours leave
    in the surface, then pull it back towards the contours (the residual at
    the contour nodes, spread out and blurred the same way). The surface no
    longer passes exactly through every line — a few centimetres off —
    but has no kinks, and real slopes have none either."""
    if radius <= 0:
        return Z
    sig = radius / h
    Zs = _gauss(Z, sig)
    for _ in range(iters):
        R = Z * 0.0
        R[fixed] = (vals - Zs)[fixed]
        C = _relax(R, fixed, 300, 1.9, 1e-7)
        Zs = Zs + _gauss(C, sig)
    return Zs


def grid_dims(bounds, p):
    """``(h, nx, ny)`` for the bounds ``(x0, y0, x1, y1)``."""
    x0, y0, x1, y1 = bounds
    w, d = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    if p["res_mode"] == "size":
        h = min(max(p["cell_size"], max(w, d) / MAX_CELLS_SIDE),
                max(w, d) / MIN_CELLS_SIDE)
    else:
        h = max(w, d) / p["cells"]
    nx = max(1, min(MAX_CELLS_SIDE, math.ceil(w / h - 1e-9)))
    ny = max(1, min(MAX_CELLS_SIDE, math.ceil(d / h - 1e-9)))
    return h, nx, ny


def estimate_triangles(segs, p):
    """Terrain triangles the grid will give (before anything is computed):
    2 per cell, times the share of the bounding box the hull covers."""
    import numpy as np
    bounds = contour_bounds(segs)
    h, nx, ny = grid_dims(bounds, p)
    fill = 1.0
    box = max((bounds[2] - bounds[0]) * (bounds[3] - bounds[1]), 1e-12)
    if p["boundary"] in ("hull", "cut"):
        S = np.asarray(segs, dtype=float)
        hull = convex_hull(np.round(np.concatenate([S[:, 0:2], S[:, 3:5]]), 9))
        if len(hull) >= 3:
            if p["boundary"] == "cut":
                r = largest_inner_rect(hull, 200)
                fill = min(1.0, (r[2] - r[0]) * (r[3] - r[1]) / box)
            else:
                a = 0.0
                for i in range(len(hull)):
                    x0, y0 = hull[i]
                    x1, y1 = hull[(i + 1) % len(hull)]
                    a += x0 * y1 - x1 * y0
                fill = min(1.0, abs(a) * 0.5 / box)
    return int(2 * nx * ny * fill), (h, nx, ny)


def check_size(n, force=False):
    if n > MAX_TRIANGLES and not force:
        raise TopoError(
            f"About {n:,} triangles — the limit is {MAX_TRIANGLES:,} "
            f"(each one needs about 2 KB of memory in IngeTrazo). "
            "Use fewer cells or a larger cell size.")


def contour_bounds(segs):
    import numpy as np
    S = np.asarray(segs, dtype=float)
    xs = np.concatenate([S[:, 0], S[:, 3]])
    ys = np.concatenate([S[:, 1], S[:, 4]])
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


def terrain_from_contours(segs, p):
    """``(V (k,3), T (m,3) CCW triangles)`` of the terrain."""
    import numpy as np
    S = np.asarray(segs, dtype=float)
    if len(S) == 0:
        raise TopoError("No contour lines in the selection.")
    zs = S[:, [2, 5]]
    if float(zs.max() - zs.min()) < 1e-6:
        raise TopoError(
            "All contours lie at the same altitude — give them their heights "
            "first with «Edit Contours».")
    check_size(estimate_triangles(segs, p)[0], p.get("force", False))
    bounds = contour_bounds(segs)
    h, nx, ny = grid_dims(bounds, p)
    x0, y0 = bounds[0], bounds[1]
    Z, fixed = solve_height_grid(S, x0, y0, h, nx, ny)
    if p["peaks"] == "round":
        Z = round_extrema(Z, fixed, h)
    if p.get("smooth_r", 0.0) > 0:
        _fx, vals = _rasterize(S, x0, y0, h, nx, ny)
        Z = smooth_surface(Z, fixed, vals, p["smooth_r"], h)

    xs = x0 + np.arange(nx + 1) * h
    ys = y0 + np.arange(ny + 1) * h
    X, Y = np.meshgrid(xs, ys)

    if p["boundary"] in ("hull", "cut"):
        xy = np.concatenate([S[:, 0:2], S[:, 3:5]])
        hull = convex_hull(np.round(xy, 9))
        if len(hull) < 3:
            raise TopoError("The contours do not span an area.")
        if p["boundary"] == "cut":
            rx0, ry0, rx1, ry1 = largest_inner_rect(hull)
            hull = [(rx0, ry0), (rx1, ry0), (rx1, ry1), (rx0, ry1)]
    else:
        hull = [(bounds[0], bounds[1]), (bounds[2], bounds[1]),
                (bounds[2], bounds[3]), (bounds[0], bounds[3])]

    eps = h * 1e-7
    inside = _inside_convex(hull, X, Y, eps)
    full = inside[:-1, :-1] & inside[:-1, 1:] & inside[1:, :-1] & inside[1:, 1:]

    # Full cells: two triangles each, split along the diagonal whose ends
    # are closer in height (follows ridges and valleys).
    idx = np.arange((ny + 1) * (nx + 1)).reshape(ny + 1, nx + 1)
    a = idx[:-1, :-1][full]
    b = idx[:-1, 1:][full]
    c = idx[1:, 1:][full]
    d = idx[1:, :-1][full]
    Zf = Z.ravel()
    diag_ac = np.abs(Zf[a] - Zf[c]) <= np.abs(Zf[b] - Zf[d])
    # Both pairs are fans from their first vertex, so a cell can later be
    # given back as ONE quad (IngeTrazo draws a convex quad as the same fan).
    t1 = np.where(diag_ac[:, None], np.stack([a, b, c], 1), np.stack([b, c, d], 1))
    t2 = np.where(diag_ac[:, None], np.stack([a, c, d], 1), np.stack([b, d, a], 1))
    V = np.column_stack([X.ravel(), Y.ravel(), Zf])
    tris = [t1, t2]
    nfull = len(a)
    cells = [np.arange(nfull), np.arange(nfull)]
    next_cell = nfull

    # Partial cells along the boundary: clip the square, fan-triangulate.
    corner_in = (inside[:-1, :-1].astype(int) + inside[:-1, 1:] +
                 inside[1:, :-1] + inside[1:, 1:])
    # Partial cells: 1–3 corners inside, or crossed by the hull's outline
    # (a cell with no corner inside can still hold a sharp hull corner).
    part = (corner_in > 0) & (corner_in < 4)
    hp = np.asarray(hull + hull[:1], dtype=float)
    for k in range(len(hull)):
        (ax, ay), (bx, by) = hp[k], hp[k + 1]
        ns = int(math.ceil(math.hypot(bx - ax, by - ay) / (h * 0.25))) + 1
        t = np.linspace(0.0, 1.0, ns)
        cx_ = np.floor((ax + (bx - ax) * t - x0) / h).astype(np.int64)
        cy_ = np.floor((ay + (by - ay) * t - y0) / h).astype(np.int64)
        ok = (cx_ >= 0) & (cx_ < nx) & (cy_ >= 0) & (cy_ < ny)
        part[cy_[ok], cx_[ok]] = True
    part &= ~full
    extra_v = []
    extra_t = []
    extra_c = []
    base = len(V)
    iy_all, ix_all = np.nonzero(part)
    for iy, ix in zip(iy_all.tolist(), ix_all.tolist()):
        cx0, cy0 = x0 + ix * h, y0 + iy * h
        sq = [(cx0, cy0), (cx0 + h, cy0), (cx0 + h, cy0 + h), (cx0, cy0 + h)]
        poly = _clip_convex(sq, hull)
        if len(poly) < 3:
            continue
        area = 0.0
        for k in range(len(poly)):
            area += (poly[k][0] * poly[(k + 1) % len(poly)][1]
                     - poly[(k + 1) % len(poly)][0] * poly[k][1])
        if area * 0.5 < h * h * 1e-6:
            continue
        z00, z10 = Z[iy, ix], Z[iy, ix + 1]
        z01, z11 = Z[iy + 1, ix], Z[iy + 1, ix + 1]
        ids = []
        for (px, py) in poly:
            fu = min(max((px - cx0) / h, 0.0), 1.0)
            fv = min(max((py - cy0) / h, 0.0), 1.0)
            z = (z00 * (1 - fu) * (1 - fv) + z10 * fu * (1 - fv)
                 + z01 * (1 - fu) * fv + z11 * fu * fv)
            ids.append(base + len(extra_v))
            extra_v.append((px, py, z))
        for k in range(1, len(ids) - 1):
            extra_t.append((ids[0], ids[k], ids[k + 1]))
            extra_c.append(next_cell)
        next_cell += 1
    if extra_v:
        V = np.vstack([V, np.asarray(extra_v)])
        tris.append(np.asarray(extra_t, dtype=np.int64).reshape(-1, 3))
        cells.append(np.asarray(extra_c, dtype=np.int64))
    T = np.vstack([t.reshape(-1, 3) for t in tris]).astype(np.int64)
    C = np.concatenate(cells).astype(np.int64)
    V, T, ok = weld(V, T, h * 1e-6, with_mask=True)
    return V, T, C[ok]


# ---------------------------------------------------------------------------
# Points → Delaunay TIN (Bowyer–Watson, numpy-vectorised circle test)
# ---------------------------------------------------------------------------

def _circum(P, a, b, c):
    ax, ay = P[a]
    bx, by = P[b]
    cx, cy = P[c]
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-300:
        return (0.0, 0.0, math.inf)
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return (ux, uy, (ax - ux) ** 2 + (ay - uy) ** 2)


def _incircle_ok(cx, cy, r2, px, py):
    return (cx - px) ** 2 + (cy - py) ** 2 < r2


def delaunay(xy):
    """Delaunay triangulation of (n, 2) points → (m, 3) CCW index array.

    Bowyer–Watson with a neighbour map: each point is found by walking from
    the last new triangle (points inserted in a snake order over a grid, so
    the walk is short) and only the triangles around it are tested — about
    linear time instead of quadratic."""
    import numpy as np
    xy = np.asarray(xy, dtype=float)
    n = len(xy)
    if n < 3:
        raise TopoError("At least three points are needed.")
    mn, mx = xy.min(0), xy.max(0)
    span = float(max(mx - mn)) or 1.0
    c = (mn + mx) / 2
    P = ((xy - c) / span).tolist()
    big = 1.0e4
    P += [[-big, -big], [big, -big], [0.0, big]]
    X = [q[0] for q in P]
    Y = [q[1] for q in P]

    tri = []          # [a, b, c] CCW, or None when deleted
    cc = []           # (cx, cy, r2)
    edge = {}         # directed edge (a, b) -> triangle id

    def circ(a, b, c3):
        ax, ay, bx, by, cx_, cy_ = X[a], Y[a], X[b], Y[b], X[c3], Y[c3]
        d = 2.0 * (ax * (by - cy_) + bx * (cy_ - ay) + cx_ * (ay - by))
        if abs(d) < 1e-300:
            return (0.0, 0.0, math.inf)
        a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx_ * cx_ + cy_ * cy_
        ux = (a2 * (by - cy_) + b2 * (cy_ - ay) + c2 * (ay - by)) / d
        uy = (a2 * (cx_ - bx) + b2 * (ax - cx_) + c2 * (bx - ax)) / d
        return (ux, uy, (ax - ux) ** 2 + (ay - uy) ** 2)

    def add(a, b, c3):
        t = len(tri)
        tri.append([a, b, c3])
        cc.append(circ(a, b, c3))
        edge[(a, b)] = t
        edge[(b, c3)] = t
        edge[(c3, a)] = t
        return t

    last = add(n, n + 1, n + 2)

    # Insertion order: snake over a √n × √n grid of buckets (locality).
    k = max(1, int(math.sqrt(n / 4.0)))
    gx = np.minimum((((xy[:, 0] - mn[0]) / (span or 1)) * k).astype(int), k - 1)
    gy = np.minimum((((xy[:, 1] - mn[1]) / (span or 1)) * k).astype(int), k - 1)
    gx = np.where(gy % 2 == 1, k - 1 - gx, gx)
    order = np.lexsort((xy[:, 0], gx, gy)).tolist()

    def orient(a, b, px, py):
        return (X[b] - X[a]) * (py - Y[a]) - (Y[b] - Y[a]) * (px - X[a])

    for i in order:
        px, py = X[i], Y[i]
        # 1. Walk to the triangle that holds the point.
        t = last
        if tri[t] is None:
            t = next(j for j in range(len(tri) - 1, -1, -1) if tri[j] is not None)
        steps = 0
        while True:
            a, b, c3 = tri[t]
            if orient(a, b, px, py) < 0:
                nt = edge.get((b, a))
            elif orient(b, c3, px, py) < 0:
                nt = edge.get((c3, b))
            elif orient(c3, a, px, py) < 0:
                nt = edge.get((a, c3))
            else:
                break
            steps += 1
            if nt is None or steps > 4 * len(tri) + 10:
                # Fallback (degenerate walk): any triangle whose circle
                # holds the point.
                nt = next(j for j, tt in enumerate(tri) if tt is not None
                          and _incircle_ok(*cc[j], px, py))
                t = nt
                break
            t = nt
        # 2. The cavity: triangles around it whose circle holds the point.
        bad = {t}
        stack = [t]
        while stack:
            u = stack.pop()
            a, b, c3 = tri[u]
            for e0, e1 in ((a, b), (b, c3), (c3, a)):
                v = edge.get((e1, e0))
                if v is not None and v not in bad:
                    cx_, cy_, r2 = cc[v]
                    if (cx_ - px) ** 2 + (cy_ - py) ** 2 < r2:
                        bad.add(v)
                        stack.append(v)
        # 3. Its rim, then re-triangulate as a fan to the point.
        rim = []
        for u in bad:
            a, b, c3 = tri[u]
            for e0, e1 in ((a, b), (b, c3), (c3, a)):
                if edge.get((e1, e0)) not in bad:
                    rim.append((e0, e1))
        for u in bad:
            a, b, c3 = tri[u]
            for e in ((a, b), (b, c3), (c3, a)):
                if edge.get(e) == u:
                    del edge[e]
            tri[u] = None
        for e0, e1 in rim:
            last = add(e0, e1, i)

    T = np.asarray([tt for tt in tri if tt is not None], dtype=np.int64)
    T = T[(T < n).all(1)]
    A, B, C = xy[T[:, 0]], xy[T[:, 1]], xy[T[:, 2]]
    cr = (B[:, 0] - A[:, 0]) * (C[:, 1] - A[:, 1]) - (B[:, 1] - A[:, 1]) * (C[:, 0] - A[:, 0])
    T[cr < 0] = T[cr < 0][:, [0, 2, 1]]
    return T[np.abs(cr) > span * span * 1e-14]


def trim_long_edges(V, T, max_len):
    """Peel rim triangles with an edge longer than ``max_len`` (a concave
    outline instead of the convex hull)."""
    import numpy as np
    if max_len <= 0 or len(T) == 0:
        return T
    T = T.copy()
    while True:
        e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
        k = np.sort(e, 1)
        _u, inv, cnt = np.unique(k, axis=0, return_inverse=True,
                                 return_counts=True)
        inv = inv.ravel()
        rim = (cnt[inv] == 1)
        L = np.hypot(*(V[e[:, 0], :2] - V[e[:, 1], :2]).T)
        bad_e = rim & (L > max_len)
        m = len(T)
        bad_t = bad_e[:m] | bad_e[m:2 * m] | bad_e[2 * m:]
        if not bad_t.any():
            return T
        T = T[~bad_t]
        if len(T) == 0:
            raise TopoError("Max. edge length removed every triangle — "
                            "increase it or set it to 0.")


def estimate_point_triangles(n_points):
    """A Delaunay TIN of n points has ≈ 2n triangles."""
    return max(0, 2 * int(n_points) - 2)


def terrain_from_points(points, p):
    import numpy as np
    V = np.asarray(points, dtype=float)
    if len(V) < 3:
        raise TopoError("Select at least three points (guide points, "
                        "vertices or survey points).")
    check_size(estimate_point_triangles(len(V)), p.get("force", False))
    mode = p["boundary"]
    if mode == "box":
        ext = V[:, :2].max(0) - V[:, :2].min(0)
        area = float(ext[0] * ext[1]) or float(max(ext)) ** 2 or 1.0
        spacing = max(math.sqrt(area / len(V)) * 1.5, float(max(ext)) / 400)
        V = np.vstack([V, rim_points(V, spacing)])
    T = delaunay(V[:, :2])
    if len(T) == 0:
        raise TopoError("The points are collinear — no surface.")
    if mode == "hull":
        T = trim_long_edges(V, T, p["max_edge"])
    elif mode == "cut":
        hull = convex_hull(np.round(V[:, :2], 9))
        V, T = clip_tin_to_rect(V, T, largest_inner_rect(hull))
    return V, T


def weld(V, T, tol, with_mask=False):
    """Merge coincident vertices; drop degenerate triangles."""
    import numpy as np
    key = np.round(V / max(tol, 1e-9)).astype(np.int64)
    _u, first, inv = np.unique(key, axis=0, return_index=True,
                               return_inverse=True)
    inv = inv.ravel()
    V2 = V[first]
    T2 = inv[T]
    ok = (T2[:, 0] != T2[:, 1]) & (T2[:, 1] != T2[:, 2]) & (T2[:, 0] != T2[:, 2])
    if with_mask:
        return V2, T2[ok], ok
    return V2, T2[ok]


# ---------------------------------------------------------------------------
# Skirt, bottom, the group
# ---------------------------------------------------------------------------

def rim_loops(T):
    """Directed rim edges (interior on the left) chained into loops."""
    import numpy as np
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    k = np.sort(e, 1)
    _u, inv, cnt = np.unique(k, axis=0, return_inverse=True, return_counts=True)
    rim = e[cnt[inv.ravel()] == 1]
    nxt: dict = {}
    for a, b in rim.tolist():
        nxt.setdefault(a, []).append(b)
    loops = []
    used = set()
    for a, b in rim.tolist():
        if (a, b) in used:
            continue
        loop = [a]
        cur, to = a, b
        while (cur, to) not in used:
            used.add((cur, to))
            loop.append(to)
            cur = to
            outs = [x for x in nxt.get(cur, []) if (cur, x) not in used]
            if not outs:
                break
            to = outs[0]
        if loop[-1] == loop[0]:
            loop.pop()
        loops.append(loop)
    return rim, loops


def cells_to_polys(T, C):
    """Triangles that fan from one cell (same id in ``C``, consecutive) →
    one polygon per cell: a quad for a full grid cell, the clipped polygon
    on the rim. IngeTrazo draws a convex polygon as the fan from its first
    vertex — exactly the triangles it was made of."""
    import numpy as np
    order = np.argsort(C, kind="stable")      # a cell's fan, in fan order
    T, C = T[order], C[order]
    polys = []
    cur, poly = None, None
    for (a, b, c), cid in zip(T.tolist(), C.tolist()):
        if cid == cur and poly is not None and poly[0] == a and poly[-1] == b:
            poly.append(c)
            continue
        if poly is not None:
            polys.append(poly)
        cur, poly = cid, [a, b, c]
    if poly is not None:
        polys.append(poly)
    return polys


def build_polygons(V, T, p, segs=None):
    """Extra polygons (skirt quads, bottom loops) as ``(rings, kinds)`` and
    extra edges — the terrain triangles themselves stay arrays."""
    import numpy as np
    polys, kinds = [], []
    zmin = float(V[:, 2].min())
    zmax = float(V[:, 2].max())
    base = zmin - p["skirt_depth"]
    edges = []
    if p["skirt"]:
        rim, loops = rim_loops(T)
        if p["skirt_depth"] > 1e-9:
            for a, b in rim.tolist():
                A, B = V[a].tolist(), V[b].tolist()
                polys.append([tuple(B), tuple(A), (A[0], A[1], base),
                              (B[0], B[1], base)])
                kinds.append("skirt")
        if p["bottom"]:
            for loop in loops:
                if len(loop) < 3:
                    continue
                xy = V[loop, :2]
                area = 0.5 * float(np.sum(xy[:, 0] * np.roll(xy[:, 1], -1)
                                          - np.roll(xy[:, 0], -1) * xy[:, 1]))
                if area <= 0:
                    continue                    # a hole in the rim
                polys.append([(float(V[i, 0]), float(V[i, 1]), base)
                              for i in reversed(loop)])
                kinds.append("bottom")
    map_z = base if p["skirt"] else zmin - max(1.0, 0.05 * (zmax - zmin))
    if segs:
        for s in segs:
            if p.get("contours"):
                edges.append((tuple(s[:3]), tuple(s[3:])))
            if p.get("map"):
                edges.append(((s[0], s[1], map_z), (s[3], s[4], map_z)))
    return polys, kinds, edges


def build_group(V, T, p, data, name="Terrain", C=None):
    """The terrain as a new classic Group (mesh in world coordinates)."""
    import numpy as np
    from PySide6.QtGui import QVector3D
    from core.group import Group
    from core.mesh import Mesh

    check_size(len(T), p.get("force", False))
    segs = data.get("segs") if data.get("mode") == "contours" else None
    polys, kinds, edges = build_polygons(V, T, p, segs)
    if C is not None and p.get("quads"):
        tp = cells_to_polys(T, C)
        pos = V[np.concatenate([np.asarray(q, dtype=np.int64) for q in tp])]
        sizes = np.asarray([len(q) for q in tp], dtype=np.int64)
    else:
        pos = V[T].reshape(-1, 3)
        sizes = np.full(len(T), 3, dtype=np.int64)
    nt = len(sizes)
    if polys:
        pos = np.vstack([pos, np.asarray([q for r in polys for q in r],
                                         dtype=float)])
        sizes = np.concatenate([sizes, np.asarray([len(r) for r in polys],
                                                  dtype=np.int64)])
    mesh = Mesh()
    faces = mesh.add_faces_bulk(np.ascontiguousarray(pos, dtype=float), sizes,
                                np.ones(len(sizes), dtype=np.int64))
    if p["soft"]:
        kind = {id(f): ("terrain" if i < nt else kinds[i - nt])
                for i, f in enumerate(faces)}
        for e in mesh.edges:
            fs = e.faces
            if len(fs) >= 2:
                k0 = kind.get(id(fs[0]))
                if k0 in ("terrain", "skirt") and all(
                        kind.get(id(f)) == k0 for f in fs[1:]):
                    e.soft = True
    for a, b in edges:
        try:
            mesh.add_edge(QVector3D(*a), QVector3D(*b))
        except ValueError:
            pass
    g = Group(mesh=mesh, name=name)
    g.ext = {KEY: data}
    g_faces = nt
    return g, g_faces


def make_terrain(data, p, name="Terrain"):
    """Source data (``{"mode", "segs"|"points"}``) + params →
    ``(Group, (triangles, seconds))``."""
    p = normalize_params(p)
    t0 = time.perf_counter()
    C = None
    if data["mode"] == "contours":
        V, T, C = terrain_from_contours(data["segs"], p)
    else:
        V, T = terrain_from_points(data["points"], p)
    rec = {"mode": data["mode"], "version": VERSION,
           "params": {k: v for k, v in p.items() if k != "force"}}
    if data["mode"] == "contours":
        rec["segs"] = [[round(float(v), 6) for v in s] for s in data["segs"]]
        if data.get("cset") is not None:
            rec["lines"] = data["cset"].to_record()
    else:
        rec["points"] = [[round(float(v), 6) for v in q] for q in data["points"]]
    g, nfaces = build_group(V, T, p, rec, name, C)
    unit = "quads" if (C is not None and p.get("quads")) else "triangles"
    return g, (nfaces, time.perf_counter() - t0, unit)


# ---------------------------------------------------------------------------
# Contour set — the lines the Altitude Editor works on
# ---------------------------------------------------------------------------

def _dp_keep(P, tol):
    """Douglas–Peucker on the XY of an open polyline → kept indices."""
    import numpy as np
    n = len(P)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = P[b, :2] - P[a, :2]
        L = float(np.hypot(*seg))
        q = P[a + 1:b, :2] - P[a, :2]
        if L < 1e-12:
            d = np.hypot(q[:, 0], q[:, 1])
        else:
            d = np.abs(q[:, 0] * seg[1] - q[:, 1] * seg[0]) / L
        k = int(np.argmax(d))
        if d[k] > tol:
            m = a + 1 + k
            keep[m] = True
            stack.append((a, m))
            stack.append((m, b))
    return keep


def simplify_line(xyz, tol, closed):
    """Fewer vertices, same shape: drop the ones closer than ``tol`` to the
    line through their neighbours (Douglas–Peucker, in plan)."""
    import numpy as np
    if tol <= 0 or len(xyz) < 3:
        return xyz
    if closed:
        P = xyz[:-1] if np.allclose(xyz[0], xyz[-1]) else xyz
        if len(P) < 4:
            return xyz
        far = int(np.argmax(np.hypot(*(P[:, :2] - P[0, :2]).T)))
        A = P[:far + 1]
        B = np.vstack([P[far:], P[:1]])
        ka, kb = _dp_keep(A, tol), _dp_keep(B, tol)
        out = np.vstack([A[ka], B[kb][1:]])
        if len(out) < 4:                  # keep at least a triangle
            return xyz
        return out
    return xyz[_dp_keep(xyz, tol)]


def smooth_line(xyz, passes, closed):
    """Remove small zigzags without shrinking the line: Taubin smoothing
    (a step towards the neighbours, then a slightly larger one back).
    Open lines keep their end points."""
    import numpy as np
    if passes <= 0 or len(xyz) < 3:
        return xyz
    loop = closed and np.allclose(xyz[0], xyz[-1])
    P = xyz[:-1].copy() if loop else xyz.copy()
    if len(P) < 3:
        return xyz
    for _ in range(int(passes)):
        for f in (0.5, -0.53):
            if closed:
                avg = (np.roll(P, 1, 0) + np.roll(P, -1, 0)) * 0.5
                P = P + f * (avg - P)
            else:
                avg = (P[:-2] + P[2:]) * 0.5
                P[1:-1] = P[1:-1] + f * (avg - P[1:-1])
    return np.vstack([P, P[:1]]) if loop else P


def line_length(xyz):
    import numpy as np
    return float(np.sum(np.hypot(*np.diff(xyz[:, :2], axis=0).T)))


class ContourSet:
    """The contour lines of one terrain: each line keeps its original
    vertices; ``alt`` (an altitude the user gave it, or None = keep its own
    heights) and ``on`` (included or not) are the edits."""

    def __init__(self, lines):
        import numpy as np
        self.lines = []
        for ln in lines:
            xyz = np.asarray(ln["xyz"], dtype=float).reshape(-1, 3)
            if len(xyz) < 2:
                continue
            closed = bool(ln.get("closed", np.allclose(xyz[0], xyz[-1])))
            alt = ln.get("alt")
            self.lines.append({"xyz": xyz, "closed": closed,
                               "alt": None if alt is None else float(alt),
                               "on": bool(ln.get("on", True))})
        self.clean = {"simplify": 0.0, "smooth": 0, "min_len": 0.0}
        self._geo = {}

    def set_clean(self, simplify=0.0, smooth=0, min_len=0.0):
        """Clean-up settings (metres / passes); True when they changed."""
        new = {"simplify": max(0.0, float(simplify)),
               "smooth": max(0, int(smooth)),
               "min_len": max(0.0, float(min_len))}
        if new == self.clean:
            return False
        if (new["simplify"], new["smooth"]) != (self.clean["simplify"],
                                                self.clean["smooth"]):
            self._geo = {}
        self.clean = new
        return True

    def length(self, i):
        return line_length(self.lines[i]["xyz"])

    def is_short(self, i):
        m = self.clean["min_len"]
        return m > 0 and self.length(i) < m

    def active(self, i):
        return self.lines[i]["on"] and not self.is_short(i)

    def cleaned(self, i):
        """Smoothed and simplified vertices (own heights), cached."""
        g = self._geo.get(i)
        if g is None:
            ln = self.lines[i]
            g = smooth_line(ln["xyz"], self.clean["smooth"], ln["closed"])
            g = simplify_line(g, self.clean["simplify"], ln["closed"])
            self._geo[i] = g
        return g

    def geom(self, i):
        """What the terrain is built from: cleaned, at the effective height."""
        ln = self.lines[i]
        g = self.cleaned(i)
        if ln["alt"] is None:
            return g
        q = g.copy()
        q[:, 2] = ln["alt"]
        return q

    def stats(self):
        """(vertices before, after, short lines ignored) of the lines on."""
        before = after = short = 0
        for i, ln in enumerate(self.lines):
            if not ln["on"]:
                continue
            if self.is_short(i):
                short += 1
                continue
            before += len(ln["xyz"])
            after += len(self.cleaned(i))
        return before, after, short

    @classmethod
    def from_segs(cls, segs):
        import numpy as np
        out = []
        for pl in chain_segments(segs):
            xyz = np.asarray(pl, dtype=float)
            out.append({"xyz": xyz, "closed": len(xyz) > 2 and
                        bool(np.allclose(xyz[0], xyz[-1]))})
        # Longest first: the list reads from the main lines down.
        out.sort(key=lambda d: -float(np.sum(np.hypot(
            *np.diff(d["xyz"][:, :2], axis=0).T))))
        return cls(out)

    @classmethod
    def from_record(cls, rec):
        return cls(rec)

    def to_record(self):
        return [{"xyz": [[round(float(v), 4) for v in q] for q in ln["xyz"]],
                 "closed": ln["closed"], "alt": ln["alt"], "on": ln["on"]}
                for ln in self.lines]

    def __len__(self):
        return len(self.lines)

    def own_z(self, i):
        """The line's own (mean) height and whether it varies (3D line)."""
        z = self.lines[i]["xyz"][:, 2]
        return float(z.mean()), float(z.max() - z.min()) > 1e-3

    def altitude(self, i):
        ln = self.lines[i]
        return ln["alt"] if ln["alt"] is not None else self.own_z(i)[0]

    def xyz(self, i):
        """Vertices at the effective height."""
        ln = self.lines[i]
        if ln["alt"] is None:
            return ln["xyz"]
        q = ln["xyz"].copy()
        q[:, 2] = ln["alt"]
        return q

    def segs(self):
        out = []
        for i, ln in enumerate(self.lines):
            if not self.active(i):
                continue
            q = self.geom(i)
            for a, b in zip(q[:-1], q[1:]):
                if (a[0], a[1]) != (b[0], b[1]):
                    out.append((float(a[0]), float(a[1]), float(a[2]),
                                float(b[0]), float(b[1]), float(b[2])))
        return out

    def all_flat(self):
        """True when every included line sits at one height (a 2D DWG)."""
        zs = {round(self.altitude(i), 4) for i in range(len(self.lines))
              if self.active(i)}
        return len(zs) <= 1


def project_lines(cset, to_pixels):
    """Screen polylines of every line: ``[(px, py, ok) arrays]``."""
    out = []
    for i in range(len(cset)):
        px, py, front = to_pixels(cset.geom(i))
        out.append((px, py, front))
    return out


def pick_line(proj, x, y, tol=8.0):
    """Index of the line nearest to the pixel (within ``tol``), or None."""
    import numpy as np
    best, best_d = None, tol
    for i, (px, py, ok) in enumerate(proj):
        if len(px) < 2:
            continue
        ax, ay, bx, by = px[:-1], py[:-1], px[1:], py[1:]
        good = ok[:-1] & ok[1:]
        if not good.any():
            continue
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = np.clip(((x - ax) * dx + (y - ay) * dy) / np.where(L2 > 0, L2, 1),
                    0, 1)
        d = np.hypot(ax + t * dx - x, ay + t * dy - y)
        d = np.where(good, d, np.inf)
        m = float(d.min())
        if m < best_d:
            best, best_d = i, m
    return best


def profile_order(proj, p0, p1, only=None):
    """Lines crossed by the screen segment p0→p1, in order from p0 (each
    line once, at its first crossing)."""
    import numpy as np
    (x0, y0), (x1, y1) = p0, p1
    rx, ry = x1 - x0, y1 - y0
    hits = []
    for i, (px, py, ok) in enumerate(proj):
        if only is not None and i not in only:
            continue
        if len(px) < 2:
            continue
        ax, ay, bx, by = px[:-1], py[:-1], px[1:], py[1:]
        sx, sy = bx - ax, by - ay
        den = rx * sy - ry * sx
        with np.errstate(divide="ignore", invalid="ignore"):
            t = ((ax - x0) * sy - (ay - y0) * sx) / den
            u = ((ax - x0) * ry - (ay - y0) * rx) / den
        good = (ok[:-1] & ok[1:] & (np.abs(den) > 1e-12)
                & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1))
        if good.any():
            hits.append((float(t[good].min()), i))
    hits.sort()
    return [i for _t, i in hits]


#: The open contour editor (one at a time) — what the overlay draws and the
#: viewport clicks talk to.
_EDITOR = None
_APP = None


def _overlay(viewport, painter):
    ed = _EDITOR
    if ed is None or _APP is None:
        return
    try:
        ed.paint(viewport, painter)
    except RuntimeError:            # the editor's widgets are gone
        pass


def _pick(viewport, px, py):
    ed = _EDITOR
    if ed is None:
        return None
    return ed.pick(px, py)


def _on_pick(item):
    ed = _EDITOR
    if ed is not None and item is not None:
        ed.picked(item)


def _on_delete(item):
    ed = _EDITOR
    if ed is not None and isinstance(item, int):
        ed.exclude([item])


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

def _make_command_class():
    from core.history import Command, InsertGroupCommand

    class TerrainCommand(Command):
        """Insert a new terrain, or replace ``old`` by ``new`` in place."""

        def __init__(self, new, old=None):
            self.new = new
            self.old = old
            self._insert = InsertGroupCommand(new) if old is None else None
            self._owner = None
            self._index = None

        def _owner_of(self, scene, g):
            if g in scene.groups:
                return scene.groups
            ctx = getattr(scene, "edit_group", None)
            kids = getattr(ctx, "children", None)
            if kids is not None and g in kids:
                return kids
            return None

        def do(self, scene):
            if self._insert is not None:
                self._insert.do(scene)
            else:
                owner = self._owner_of(scene, self.old)
                if owner is None:
                    raise RuntimeError("The terrain is no longer in the model.")
                i = owner.index(self.old)
                owner[i] = self.new
                self._owner, self._index = owner, i
                scene.selection.discard(self.old)
            scene.selection.clear()
            scene.selection.add(self.new)
            scene.version += 1

        def undo(self, scene):
            if self._insert is not None:
                self._insert.undo(scene)
            elif self._owner is not None and self.new in self._owner:
                self._owner[self._owner.index(self.new)] = self.old
            scene.selection.discard(self.new)
            if self.old is not None:
                scene.selection.add(self.old)
            scene.version += 1

    return TerrainCommand


_CMD = None


def run_terrain(viewport, data, p, old=None):
    """Build and insert (or replace ``old``) as one undo step. Returns the
    command; raises TopoError / RuntimeError."""
    global _CMD
    if _CMD is None:
        _CMD = _make_command_class()
    name = old.name if old is not None else _next_name(viewport.scene)
    g, stats = make_terrain(data, p, name)
    if old is not None:
        g.layer = old.layer
        g.material = getattr(old, "material", None)
    cmd = _CMD(g, old)
    cmd.stats = stats
    hist = viewport.history
    hist.execute(cmd)
    err = getattr(hist, "last_error", None)
    if err:
        raise RuntimeError(err)
    notify = getattr(viewport, "notify_scene_changed", None)
    if notify:
        notify()
    viewport.update()
    return cmd


def _next_name(scene):
    used = {g.name for g in scene.groups}
    if "Terrain" not in used:
        return "Terrain"
    n = 2
    while f"Terrain {n}" in used:
        n += 1
    return f"Terrain {n}"


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------

def _unit():
    try:
        from core import units
        code = units.model_unit()
        short = {"in": "in", "in-frac": "in", "ft": "ft", "ft-in": "ft",
                 "ft-in-frac": "ft"}.get(code, code)
        return units.bare_number_scale(), short
    except Exception:  # noqa: BLE001
        return 1.0, "m"


def _fmt(v, scale, unit):
    return f"{v / scale:,.2f} {unit}"


def _make_editor_class():
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QFont, QPen
    from PySide6.QtWidgets import (QAbstractItemView, QCheckBox,
                                   QDoubleSpinBox, QGridLayout, QGroupBox,
                                   QHBoxLayout, QHeaderView, QLabel,
                                   QPushButton, QTableWidget,
                                   QTableWidgetItem, QVBoxLayout)

    class ContourEditor(QGroupBox):
        """Altitude Editor: the contour lines in a list and in the viewport
        — include/exclude, altitudes typed, clicked in order or along a
        profile."""

        COL_ON, COL_ALT, COL_INFO = 0, 1, 2

        def __init__(self, dialog, cset):
            super().__init__("Contours — Altitude Editor", dialog)
            self.dlg = dialog
            self.cset = cset
            self.scale_m, self.ulabel = dialog.scale_m, dialog.ulabel
            self.sel: set = set()
            self.mode = None              # None | "seq" | "profile"
            self.seq_k = 0
            self.prof_pts: list = []
            self._filling = False
            self._build()
            self.fill()

        # ---- UI ------------------------------------------------------------
        def _spin(self, lo=-1e6, hi=1e6):
            s = QDoubleSpinBox(self)
            s.setRange(lo, hi)
            s.setDecimals(3)
            s.setSuffix(f" {self.ulabel}")
            s.setKeyboardTracking(False)
            return s

        def _build(self):
            lay = QVBoxLayout(self)
            hint = QLabel(
                "Click a line in the viewport (Select tool) or in the list; "
                "Shift/Ctrl-click selects a range. Double-click an altitude "
                "to type it. Del in the viewport ignores the picked line.",
                self)
            hint.setWordWrap(True)
            lay.addWidget(hint)

            self.table = QTableWidget(0, 3, self)
            self.table.setHorizontalHeaderLabels(["On", "Altitude", "Line"])
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.table.verticalHeader().setDefaultSectionSize(22)
            hh = self.table.horizontalHeader()
            hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(2, QHeaderView.Stretch)
            self.table.setMinimumWidth(300)
            self.table.setMinimumHeight(220)
            self.table.itemSelectionChanged.connect(self._table_sel)
            self.table.itemChanged.connect(self._item_changed)
            lay.addWidget(self.table, 1)

            # Selected lines
            g = QGridLayout()
            g.addWidget(QLabel("Selected:"), 0, 0)
            self.alt = self._spin()
            g.addWidget(self.alt, 0, 1)
            b = QPushButton("Set altitude", self)
            b.clicked.connect(self._set_alt)
            g.addWidget(b, 0, 2)
            b = QPushButton("✓ Use selected lines", self)
            b.setToolTip("Switch ON every selected line (one or a whole "
                         "range) — they shape the terrain.")
            b.clicked.connect(lambda: self._set_on(True))
            g.addWidget(b, 1, 1)
            b = QPushButton("✗ Ignore selected lines", self)
            b.setToolTip("Switch OFF every selected line (one or a whole "
                         "range) — the terrain is made without them.")
            b.clicked.connect(lambda: self._set_on(False))
            g.addWidget(b, 1, 2)
            b = QPushButton("Reset to original height", self)
            b.setToolTip("Forget the altitude set here: the selected lines "
                         "go back to the heights they had in the model "
                         "(not to 0 — unless they were at 0).")
            b.clicked.connect(self._reset_alt)
            g.addWidget(b, 2, 1, 1, 2)
            lay.addLayout(g)

            # Automatic numbering
            auto = QGroupBox("Number the lines", self)
            al = QGridLayout(auto)
            al.addWidget(QLabel("Start:"), 0, 0)
            self.start = self._spin()
            al.addWidget(self.start, 0, 1)
            al.addWidget(QLabel("Interval:"), 0, 2)
            self.step = self._spin(-1e5, 1e5)
            self.step.setValue(1.0 / self.scale_m)
            al.addWidget(self.step, 0, 3)
            self.b_seq = QPushButton("Click lines in order", self)
            self.b_seq.setCheckable(True)
            self.b_seq.setToolTip(
                "Each line you click in the viewport gets the next altitude: "
                "Start, Start + Interval, …  Click the button again to stop.")
            self.b_seq.toggled.connect(self._seq_toggled)
            al.addWidget(self.b_seq, 1, 0, 1, 2)
            self.b_prof = QPushButton("Profile: click 2 points", self)
            self.b_prof.setCheckable(True)
            self.b_prof.setToolTip(
                "Click two points in the viewport, e.g. from the foot of a "
                "hill to its top: every line the profile crosses is numbered "
                "in that order — Start, Start + Interval, …")
            self.b_prof.toggled.connect(self._prof_toggled)
            al.addWidget(self.b_prof, 1, 2, 1, 2)
            self.mode_lbl = QLabel(self)
            self.mode_lbl.setWordWrap(True)
            al.addWidget(self.mode_lbl, 2, 0, 1, 4)
            lay.addWidget(auto)

            row = QHBoxLayout()
            self.show_alt = QCheckBox("Altitudes in the viewport", self)
            self.show_alt.setChecked(True)
            self.show_alt.toggled.connect(lambda _c: self._repaint())
            row.addWidget(self.show_alt)
            row.addStretch(1)
            lay.addLayout(row)
            self._mode_text()

        # ---- list ----------------------------------------------------------
        def _info(self, i):
            ln = self.cset.lines[i]
            _z, varies = self.cset.own_z(i)
            kind = "closed" if ln["closed"] else "open"
            extra = []
            if ln["alt"] is not None:
                extra.append("set")
            elif varies:
                extra.append("3D")
            if self.cset.is_short(i):
                extra.append("too short")
            return f"#{i + 1} · {kind}" + (f" · {', '.join(extra)}" if extra else "")

        def fill(self):
            self._filling = True
            t = self.table
            t.setRowCount(len(self.cset))
            for i, ln in enumerate(self.cset.lines):
                on = QTableWidgetItem()
                on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                            | Qt.ItemIsSelectable)
                on.setCheckState(Qt.Checked if ln["on"] else Qt.Unchecked)
                t.setItem(i, self.COL_ON, on)
                a = QTableWidgetItem(
                    f"{self.cset.altitude(i) / self.scale_m:.3f}")
                a.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(i, self.COL_ALT, a)
                info = QTableWidgetItem(self._info(i))
                info.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                t.setItem(i, self.COL_INFO, info)
                grey = QColor(140, 140, 140)
                for c in range(3):
                    it = t.item(i, c)
                    if not self.cset.active(i):
                        it.setForeground(grey)
            self._filling = False

        def _refresh_rows(self, rows):
            self._filling = True
            for i in rows:
                ln = self.cset.lines[i]
                self.table.item(i, self.COL_ON).setCheckState(
                    Qt.Checked if ln["on"] else Qt.Unchecked)
                self.table.item(i, self.COL_ALT).setText(
                    f"{self.cset.altitude(i) / self.scale_m:.3f}")
                self.table.item(i, self.COL_INFO).setText(self._info(i))
                col = QColor(140, 140, 140) if not self.cset.active(i) else \
                    self.table.palette().text().color()
                for c in range(3):
                    self.table.item(i, c).setForeground(col)
            self._filling = False

        def _table_sel(self):
            if self._filling:
                return
            self.sel = {ix.row() for ix in self.table.selectionModel().selectedRows()}
            if len(self.sel) == 1:
                i = next(iter(self.sel))
                self.alt.setValue(self.cset.altitude(i) / self.scale_m)
            self._repaint()

        def select(self, rows, scroll=True):
            from PySide6.QtCore import QItemSelectionModel
            self._filling = True
            self.table.clearSelection()
            sm = self.table.selectionModel()
            flags = (QItemSelectionModel.Select | QItemSelectionModel.Rows)
            for i in rows:
                sm.select(self.table.model().index(i, 0), flags)
            self._filling = False
            self.sel = set(rows)
            if rows and scroll:
                self.table.scrollToItem(self.table.item(min(rows), 0))
            if len(self.sel) == 1:
                self.alt.setValue(
                    self.cset.altitude(next(iter(self.sel))) / self.scale_m)
            self._repaint()

        def _item_changed(self, item):
            if self._filling:
                return
            i = item.row()
            ln = self.cset.lines[i]
            if item.column() == self.COL_ON:
                on = item.checkState() == Qt.Checked
                rows = self.sel if i in self.sel and len(self.sel) > 1 else {i}
                for r in rows:
                    self.cset.lines[r]["on"] = on
                self._refresh_rows(rows)
                self._edited()
                return
            elif item.column() == self.COL_ALT:
                try:
                    v = float(item.text().replace(",", ".").split()[0])
                    ln["alt"] = v * self.scale_m
                except (ValueError, IndexError):
                    pass
            self._refresh_rows([i])
            self._edited()

        # ---- edits -----------------------------------------------------------
        def _edited(self):
            self._repaint()
            self.dlg.contours_changed()

        def _set_alt(self):
            for i in self.sel:
                self.cset.lines[i]["alt"] = self.alt.value() * self.scale_m
            self._refresh_rows(self.sel)
            self._edited()

        def _reset_alt(self):
            for i in self.sel:
                self.cset.lines[i]["alt"] = None
            self._refresh_rows(self.sel)
            self._edited()

        def _set_on(self, on):
            for i in self.sel:
                self.cset.lines[i]["on"] = on
            self._refresh_rows(self.sel)
            self._edited()

        def exclude(self, rows):
            for i in rows:
                self.cset.lines[i]["on"] = False
            self._refresh_rows(rows)
            self._edited()

        def assign(self, order, start, step):
            for k, i in enumerate(order):
                self.cset.lines[i]["alt"] = start + k * step
            self._refresh_rows(order)
            self.select(order, scroll=True)
            self._edited()

        # ---- numbering modes ---------------------------------------------------
        def _seq_toggled(self, on):
            if on:
                self.b_prof.setChecked(False)
                self.mode, self.seq_k = "seq", 0
                self.dlg.activate_select_tool()
            elif self.mode == "seq":
                self.mode = None
            self._mode_text()

        def _prof_toggled(self, on):
            if on:
                self.b_seq.setChecked(False)
                self.mode, self.prof_pts = "profile", []
                self.dlg.activate_select_tool()
            elif self.mode == "profile":
                self.mode, self.prof_pts = None, []
            self._mode_text()
            self._repaint()

        def _mode_text(self):
            s, u = self.scale_m, self.ulabel
            if self.mode == "seq":
                nxt = self.start.value() + self.seq_k * self.step.value()
                self.mode_lbl.setText(
                    f"<b>Click the lines in order.</b> Next: {nxt:.3f} {u}")
                self.mode_lbl.setStyleSheet("color: #e8912d;")
            elif self.mode == "profile":
                n = len(self.prof_pts)
                self.mode_lbl.setText(
                    f"<b>Click point {n + 1} of 2</b> in the viewport.")
                self.mode_lbl.setStyleSheet("color: #e8912d;")
            else:
                self.mode_lbl.setText("")
                self.mode_lbl.setStyleSheet("")

        # ---- viewport ------------------------------------------------------------
        def _to_px(self, pts):
            import numpy as np
            return _APP.world_to_pixels(np.asarray(pts, dtype=float))

        def pick(self, px, py):
            if self.mode == "profile":
                return ("pt", float(px), float(py))
            proj = project_lines(self.cset, self._to_px)
            return pick_line(proj, px, py)

        def picked(self, item):
            if isinstance(item, tuple):                     # a profile point
                self.prof_pts.append(item[1:])
                _APP.release_pick()
                if len(self.prof_pts) == 2:
                    proj = project_lines(self.cset, self._to_px)
                    on = {i for i in range(len(self.cset)) if self.cset.active(i)}
                    order = profile_order(proj, *self.prof_pts, only=on)
                    p0, p1 = self.prof_pts
                    self.prof_pts = []
                    self.b_prof.setChecked(False)
                    if order:
                        self.assign(order, self.start.value() * self.scale_m,
                                    self.step.value() * self.scale_m)
                        self.mode_lbl.setText(
                            f"Profile: {len(order)} lines numbered.")
                    else:
                        self.mode_lbl.setText("The profile crossed no line.")
                self._mode_text() if self.mode else None
                self._repaint()
                return
            i = int(item)
            if self.mode == "seq":
                _APP.release_pick()
                v = (self.start.value() + self.seq_k * self.step.value())
                self.seq_k += 1
                self.cset.lines[i]["alt"] = v * self.scale_m
                self._refresh_rows([i])
                self.select([i])
                self._mode_text()
                self._edited()
                return
            self.select([i])

        def _repaint(self):
            if _APP is not None:
                _APP.viewport.update()

        def paint(self, viewport, painter):
            import numpy as np
            proj = project_lines(self.cset, self._to_px)
            painter.setRenderHint(painter.RenderHint.Antialiasing, True)
            f = QFont(painter.font())
            f.setPointSizeF(max(7.0, f.pointSizeF() * 0.9))
            painter.setFont(f)
            for i, (px, py, ok) in enumerate(proj):
                ln = self.cset.lines[i]
                sel = i in self.sel
                if not self.cset.active(i):
                    pen = QPen(QColor(150, 150, 150, 200), 1.2, Qt.DashLine)
                elif sel:
                    pen = QPen(QColor(255, 128, 0), 3.5)
                elif ln["alt"] is not None:
                    pen = QPen(QColor(30, 160, 80), 1.8)
                else:
                    pen = QPen(QColor(40, 110, 220), 1.5)
                painter.setPen(pen)
                pts = [QPointF(float(x), float(y))
                       for x, y, o in zip(px, py, ok) if o]
                if len(pts) >= 2:
                    painter.drawPolyline(pts)
                if self.show_alt.isChecked() and self.cset.active(i) and len(pts) >= 2:
                    m = pts[len(pts) // 2]
                    txt = f"{self.cset.altitude(i) / self.scale_m:.2f}"
                    painter.setPen(QColor(0, 0, 0, 170))
                    painter.drawText(m + QPointF(4, -3), txt)
                    painter.setPen(QColor(255, 255, 255) if not sel
                                   else QColor(255, 140, 0))
                    painter.drawText(m + QPointF(3, -4), txt)
            if self.mode == "profile" and self.prof_pts:
                painter.setPen(QPen(QColor(255, 128, 0), 2))
                for (x, y) in self.prof_pts:
                    painter.drawEllipse(QPointF(x, y), 5, 5)

    return ContourEditor


def _make_dialog_class():
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox,
                                   QDialog, QDoubleSpinBox, QFormLayout,
                                   QGroupBox, QHBoxLayout, QLabel,
                                   QMessageBox, QPushButton, QRadioButton,
                                   QSpinBox, QVBoxLayout)

    class TopoDialog(QDialog):
        def __init__(self, viewport, data, old=None, parent=None):
            super().__init__(parent)
            self.viewport = viewport
            self.data = data
            self.old = old
            self.mode = data["mode"]
            self.scale_m, self.ulabel = _unit()
            self.cset = None
            self.editor = None
            if self.mode == "contours":
                self.cset = data.get("cset") or ContourSet.from_segs(data["segs"])
                data["cset"] = self.cset
                data["segs"] = self.cset.segs()
            if old is not None:
                self.p = normalize_params(terrain_data(old).get("params"))
            else:
                self.p = load_params()
                if (self.mode == "contours" and self.data["segs"] and
                        estimate_triangles(self.data["segs"], self.p)[0]
                        > MAX_TRIANGLES):
                    d = default_params()
                    self.p["res_mode"] = d["res_mode"]
                    self.p["cells"] = d["cells"]
            if self.cset is not None:
                self.cset.set_clean(self.p["simplify"], self.p["smooth"],
                                    self.p["min_len"])
                data["segs"] = self.cset.segs()
            self._preview_cmd = None
            self._safe_res = None
            self._block = None          # why nothing can be built now
            self._busy = False
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(250)
            self._timer.timeout.connect(self._refresh_preview)
            what = ("Terrain from Contours" if self.mode == "contours"
                    else "Terrain from Points")
            self.setWindowTitle(f"{TITLE} {VERSION} — {'Edit Terrain' if old else what}")
            self.setModal(False)
            self.setAttribute(Qt.WA_DeleteOnClose, True)
            self._build_ui()
            self._load_into_ui()
            if self.cset is not None and self.cset.all_flat():
                # A flat DWG: the heights have to be given first.
                self.b_edit.setChecked(True)

        # ---- UI ------------------------------------------------------------
        def _dspin(self, lo, hi, dec):
            s = QDoubleSpinBox(self)
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setSuffix(f" {self.ulabel}")
            s.setKeyboardTracking(False)
            s.setMinimumWidth(110)
            s.valueChanged.connect(self._changed)
            return s

        def _footprint_combo(self, outline_text):
            c = QComboBox(self)
            c.addItem(outline_text, "hull")
            c.addItem("Rectangle — interpolated to the full box", "box")
            c.addItem("Rectangle — cut inside the data", "cut")
            c.setToolTip(
                "Outline: the terrain ends where the data ends.\n"
                "Interpolated: the surface is carried out to the bounding "
                "box.\nCut: the largest rectangle that fits inside the data.")
            c.currentIndexChanged.connect(self._changed)
            return c

        def _check(self, text):
            c = QCheckBox(text, self)
            c.toggled.connect(self._changed)
            return c

        def _build_ui(self):
            root = QHBoxLayout(self)
            if self.cset is not None:
                self.editor = _make_editor_class()(self, self.cset)
                self.editor.setVisible(False)
                root.addWidget(self.editor, 1)
            lay = QVBoxLayout()
            root.addLayout(lay)
            src = QGroupBox("Source", self)
            sl = QVBoxLayout(src)
            self.src_lbl = QLabel(self._source_text(), self)
            self.src_lbl.setWordWrap(True)
            self.src_lbl.setMinimumWidth(330)
            sl.addWidget(self.src_lbl)
            if self.cset is not None:
                self.b_edit = QPushButton("◂ Edit Contours (altitudes, on/off)", self)
                self.b_edit.setCheckable(True)
                self.b_edit.toggled.connect(self._editor_toggled)
                sl.addWidget(self.b_edit)
            lay.addWidget(src)

            if self.mode == "contours":
                cbox = QGroupBox("Clean up lines", self)
                cl = QFormLayout(cbox)
                self.simplify = self._dspin(0.0, 1e4, 3)
                self.simplify.setSpecialValueText("Off")
                self.simplify.setToolTip(
                    "Drop vertices that lie closer than this to the line "
                    "through their neighbours — fewer points, same shape.")
                cl.addRow("Simplify (tolerance):", self.simplify)
                self.smooth = QSpinBox(self)
                self.smooth.setRange(0, 10)
                self.smooth.setSpecialValueText("Off")
                self.smooth.setSuffix(" passes")
                self.smooth.setKeyboardTracking(False)
                self.smooth.setToolTip(
                    "Remove small zigzags and steps (scanned or vectorised "
                    "plans) without shrinking the lines. 1–3 passes are "
                    "usually enough.")
                self.smooth.valueChanged.connect(self._changed)
                cl.addRow("Smooth:", self.smooth)
                self.min_len = self._dspin(0.0, 1e6, 3)
                self.min_len.setSpecialValueText("Off")
                self.min_len.setToolTip(
                    "Lines shorter than this are ignored automatically "
                    "(tiny fragments, text leftovers).")
                cl.addRow("Ignore lines shorter than:", self.min_len)
                self.clean_lbl = QLabel(self)
                self.clean_lbl.setWordWrap(True)
                cl.addRow(self.clean_lbl)
                lay.addWidget(cbox)

                gbox = QGroupBox("Grid", self)
                gl = QFormLayout(gbox)
                self.r_cells = QRadioButton("Cells on long side", self)
                self.r_size = QRadioButton("Cell size", self)
                grp = QButtonGroup(self)
                grp.addButton(self.r_cells)
                grp.addButton(self.r_size)
                self.r_cells.toggled.connect(self._changed)
                self.cells = QSpinBox(self)
                self.cells.setRange(MIN_CELLS_SIDE, MAX_CELLS_SIDE)
                self.cells.setKeyboardTracking(False)
                self.cells.valueChanged.connect(self._changed)
                b = contour_bounds(self.data["segs"])
                long_side = max(b[2] - b[0], b[3] - b[1], 1e-6)
                self.cell_size = self._dspin(
                    long_side / MAX_CELLS_SIDE / self.scale_m,
                    long_side / MIN_CELLS_SIDE / self.scale_m, 3)
                gl.addRow(self.r_cells, self.cells)
                gl.addRow(self.r_size, self.cell_size)
                self.boundary = self._footprint_combo("Outline (convex hull)")
                gl.addRow("Footprint:", self.boundary)
                self.peaks = QComboBox(self)
                self.peaks.addItem("Rounded (interpolated)", "round")
                self.peaks.addItem("Flat", "flat")
                self.peaks.setToolTip(
                    "Inside the highest / lowest closed contour: a dome that "
                    "continues the slope of the terrain, or a flat top.")
                self.peaks.currentIndexChanged.connect(self._changed)
                gl.addRow("Summits && pits:", self.peaks)
                self.smooth_r = self._dspin(0.0, 1e4, 2)
                self.smooth_r.setSpecialValueText("Off (raw)")
                self.smooth_r.setToolTip(
                    "Off: the surface runs exactly through every contour "
                    "line, with a slight crease at each one (raw, honest).\n"
                    "A radius (e.g. 2–5 m): the whole surface is rounded "
                    "off without creases and then pulled back to the lines "
                    "— it may miss a line by a few centimetres, but looks "
                    "calm and is usually closer to the real ground.")
                gl.addRow("Smooth surface (radius):", self.smooth_r)
                self.quads = QCheckBox("Quads instead of triangles", self)
                self.quads.setToolTip(
                    "One four-sided face per grid cell instead of two "
                    "triangles: half the faces, same shape — handy for "
                    "export to 3ds Max, Blender & co.")
                self.quads.toggled.connect(self._changed)
                gl.addRow("Faces:", self.quads)
                self.grid_lbl = QLabel(self)
                self.grid_lbl.setWordWrap(True)
                gl.addRow(self.grid_lbl)
                self.warn_lbl = QLabel(self)
                gl.addRow(self.warn_lbl)
                self.force = QCheckBox("Build anyway — at my own risk", self)
                self.force.setToolTip(
                    "Each triangle needs about 2 KB of memory in IngeTrazo; "
                    "millions of them can freeze or crash the program. "
                    "Save your model first.")
                self.force.setStyleSheet("color: #e0483e;")
                self.force.toggled.connect(self._changed)
                gl.addRow(self.force)
                lay.addWidget(gbox)
            else:
                tbox = QGroupBox("Triangulation", self)
                tl = QFormLayout(tbox)
                import numpy as np
                pts = np.asarray(self.data["points"], dtype=float)
                ext = pts[:, :2].max(0) - pts[:, :2].min(0)
                diag = float(np.hypot(*ext)) or 1.0
                area = float(ext[0] * ext[1]) or diag * diag
                self._spacing = math.sqrt(area / max(len(pts), 1))
                # Longer than the diagonal = no edge is ever cut = «Off».
                self.max_edge = self._dspin(0.0, diag / self.scale_m, 3)
                self.max_edge.setSpecialValueText("Off")
                self.boundary = self._footprint_combo("Outline of the points")
                tl.addRow("Footprint:", self.boundary)
                tl.addRow("Max. edge length on the rim:", self.max_edge)
                self.grid_lbl = QLabel(self)
                self.grid_lbl.setWordWrap(True)
                tl.addRow(self.grid_lbl)
                self.warn_lbl = QLabel(self)
                tl.addRow(self.warn_lbl)
                self.force = QCheckBox("Build anyway — at my own risk", self)
                self.force.setStyleSheet("color: #e0483e;")
                self.force.setToolTip(
                    "Each triangle needs about 2 KB of memory in IngeTrazo; "
                    "millions of them can freeze or crash the program. "
                    "Save your model first.")
                self.force.toggled.connect(self._changed)
                tl.addRow(self.force)
                lay.addWidget(tbox)

            obox = QGroupBox("Output", self)
            ol = QVBoxLayout(obox)
            self.soft = self._check("Soft edges (smooth look)")
            ol.addWidget(self.soft)
            row = QHBoxLayout()
            self.skirt = self._check("Skirt, depth below the lowest point:")
            self.depth = self._dspin(0.0, 1e6, 3)
            row.addWidget(self.skirt)
            row.addWidget(self.depth)
            row.addStretch(1)
            ol.addLayout(row)
            self.bottom = self._check("Close the bottom (solid)")
            ol.addWidget(self.bottom)
            # Only meaningful for contours — not even created for points
            # (a widget outside every layout floats over «Source»).
            self.contours = self.map = None
            if self.mode == "contours":
                self.contours = self._check("Include the contours")
                self.map = self._check(
                    "Include a contour map (flat, at the base)")
                ol.addWidget(self.contours)
                ol.addWidget(self.map)
            lay.addWidget(obox)

            # Live-preview banner: impossible to miss while the preview runs.
            self.pv_lbl = QLabel(self)
            self.pv_lbl.setWordWrap(True)
            self.pv_lbl.setVisible(False)
            from PySide6.QtWidgets import QSizePolicy
            self.pv_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
            lay.addStretch(1)
            lay.addWidget(self.pv_lbl)

            bl = QHBoxLayout()
            self.preview = QPushButton("Preview", self)
            self.preview.setCheckable(True)
            self.preview.setMinimumWidth(130)
            self.preview.toggled.connect(self._preview_toggled)
            reset = QPushButton("Reset", self)
            reset.clicked.connect(self._reset)
            ok = QPushButton("OK", self)
            self.ok_btn = ok
            ok.setDefault(True)
            ok.clicked.connect(self.accept)
            cancel = QPushButton("Cancel", self)
            cancel.clicked.connect(self.reject)
            bl.addWidget(self.preview)
            bl.addWidget(reset)
            bl.addStretch(1)
            bl.addWidget(ok)
            bl.addWidget(cancel)
            lay.addLayout(bl)

        def _editor_toggled(self, on):
            global _EDITOR
            self.editor.setVisible(on)
            self.b_edit.setText("▸ Hide Contour Editor" if on else
                                "◂ Edit Contours (altitudes, on/off)")
            _EDITOR = self.editor if on else None
            if on:
                self.activate_select_tool()
            elif _APP is not None:
                _APP.release_pick()
            if _APP is not None:
                _APP.viewport.update()
            QTimer.singleShot(0, self.adjustSize)

        def contours_changed(self):
            """The editor changed altitudes or on/off: new source."""
            self.data["segs"] = self.cset.segs()
            self.src_lbl.setText(self._source_text())
            self._changed()

        def activate_select_tool(self):
            win = _APP.window if _APP is not None else None
            fn = getattr(win, "_activate_tool", None)
            if fn is not None:
                try:
                    fn("select")
                except Exception:  # noqa: BLE001
                    pass

        def done(self, r):
            global _EDITOR
            if _EDITOR is self.editor:
                _EDITOR = None
            if _APP is not None:
                try:
                    _APP.release_pick()
                    _APP.viewport.update()
                except Exception:  # noqa: BLE001
                    pass
            super().done(r)

        def _source_text(self):
            s, u = self.scale_m, self.ulabel
            if self.mode == "contours":
                segs = self.data["segs"]
                n = len(self.cset)
                off = sum(1 for i in range(n) if not self.cset.active(i))
                head = f"{n} contour lines" + (f" ({off} not used)" if off else "")
                if not segs:
                    return head + " — none included."
                zs = [v for sg in segs for v in (sg[2], sg[5])]
                levels = len({round(z, 3) for z in zs})
                txt = (f"{head}, {len(segs):,} segments, {levels} "
                       f"{'altitude' if levels == 1 else 'altitudes'} "
                       f"from {_fmt(min(zs), s, u)} to {_fmt(max(zs), s, u)}")
                if levels <= 1:
                    txt += ("<br><b style='color:#e8912d'>All at one height — "
                            "give the lines their altitudes in the editor.</b>")
                return txt
            pts = self.data["points"]
            zs = [q[2] for q in pts]
            return (f"{len(pts):,} points ({self.data.get('label', 'selection')}), "
                    f"altitude {_fmt(min(zs), s, u)} – {_fmt(max(zs), s, u)}")

        # ---- params <-> UI ---------------------------------------------------
        def _load_into_ui(self):
            self._busy = True
            p, s = self.p, self.scale_m
            if self.mode == "contours":
                (self.r_size if p["res_mode"] == "size" else self.r_cells).setChecked(True)
                self.cells.setValue(p["cells"])
                self.cell_size.setValue(p["cell_size"] / s)

                self.peaks.setCurrentIndex(max(0, self.peaks.findData(p["peaks"])))
                self.quads.setChecked(p["quads"])
                self.smooth_r.setValue(p["smooth_r"] / s)
                self.simplify.setValue(p["simplify"] / s)
                self.smooth.setValue(p["smooth"])
                self.min_len.setValue(p["min_len"] / s)
            else:
                self.max_edge.setValue(p["max_edge"] / s)
            self.boundary.setCurrentIndex(
                max(0, self.boundary.findData(p["boundary"])))
            if getattr(self, "force", None) is not None:
                self.force.setChecked(False)
                p["force"] = False
            self.soft.setChecked(p["soft"])
            self.skirt.setChecked(p["skirt"])
            self.depth.setValue(p["skirt_depth"] / s)
            self.bottom.setChecked(p["bottom"])
            if self.contours is not None:
                self.contours.setChecked(p["contours"])
                self.map.setChecked(p["map"])
            self._busy = False
            self._sync()

        def _read_ui(self):
            p, s = self.p, self.scale_m
            if self.mode == "contours":
                p["res_mode"] = "size" if self.r_size.isChecked() else "cells"
                p["cells"] = self.cells.value()
                p["cell_size"] = self.cell_size.value() * s
                p["peaks"] = self.peaks.currentData()
                p["force"] = self.force.isChecked()
                p["quads"] = self.quads.isChecked()
                p["smooth_r"] = self.smooth_r.value() * s
                p["simplify"] = self.simplify.value() * s
                p["smooth"] = self.smooth.value()
                p["min_len"] = self.min_len.value() * s
            else:
                p["max_edge"] = self.max_edge.value() * s
                p["force"] = self.force.isChecked()
            p["boundary"] = self.boundary.currentData()
            p["soft"] = self.soft.isChecked()
            p["skirt"] = self.skirt.isChecked()
            p["skirt_depth"] = self.depth.value() * s
            p["bottom"] = self.bottom.isChecked()
            if self.contours is not None:
                p["contours"] = self.contours.isChecked()
                p["map"] = self.map.isChecked()
            self.p = normalize_params(p)

        def _limit(self, n, hint):
            """Colour the info line, show the warning and the own-risk box,
            enable OK / Preview — the same for both modes."""
            p = self.p
            too_big = n > MAX_TRIANGLES
            if too_big:
                warn = (f"<b>Too many triangles</b> — safe limit "
                        f"{MAX_TRIANGLES:,}. {hint}")
                color = "#e0483e"
            elif n > WARN_TRIANGLES:
                warn = "Large: slow to build and to orbit."
                color = "#e8912d"
            else:
                warn, color = "", ""
            style = f"color: {color};" if color else ""
            self.grid_lbl.setStyleSheet(style)
            self.warn_lbl.setStyleSheet(style)
            self.warn_lbl.setText(warn)
            self.warn_lbl.setVisible(bool(warn))
            if not too_big and self.force.isChecked():
                self.force.blockSignals(True)
                self.force.setChecked(False)
                self.force.blockSignals(False)
                p["force"] = False
            self.force.setVisible(too_big)
            allowed = (not too_big) or self.force.isChecked()
            self.ok_btn.setEnabled(allowed)
            self.preview.setEnabled(True)
            self._block = None if allowed else (
                f"Over the safe limit of {MAX_TRIANGLES:,} triangles — "
                f"{hint[:-1].lower() if hint.endswith('.') else hint} "
                f"or tick «Build anyway».")
            if self.isVisible():
                QTimer.singleShot(0, self.adjustSize)
            return too_big

        def _sync(self):
            p = self.p
            if self.mode == "contours":
                b, a, short = self.cset.stats()
                txt = (f"Vertices: {b:,}" if (b == a) else
                       f"Vertices: {b:,} → <b>{a:,}</b> "
                       f"({100 * (1 - a / max(b, 1)):.0f} % fewer)")
                if short:
                    txt += f" · {short} short line{'s' if short > 1 else ''} ignored"
                self.clean_lbl.setText(txt)
                self.cells.setEnabled(p["res_mode"] == "cells")
                self.cell_size.setEnabled(p["res_mode"] == "size")
                flat = bool(self.data["segs"]) and self.cset.all_flat()
                if not self.data["segs"] or flat:
                    self.grid_lbl.setText(
                        "No contour line is included." if not flat else
                        "All included lines are at one height — give them "
                        "their altitudes first (Edit Contours).")
                    self.warn_lbl.setVisible(False)
                    self.force.setVisible(False)
                    self.ok_btn.setEnabled(False)
                    self.preview.setEnabled(True)
                    self._block = (
                        "No contour line is included — tick at least two."
                        if not flat else
                        "All included lines are at one height — give at "
                        "least two of them different altitudes.")
                    return
                n, (h, nx, ny) = estimate_triangles(self.data["segs"], p)
                faces = (f"≈ {n // 2:,} quads" if p.get("quads")
                         else f"≈ {n:,} triangles")
                self.grid_lbl.setText(
                    f"Grid: {nx} × {ny} cells, "
                    f"{_fmt(h, self.scale_m, self.ulabel)} each ({faces})")
                if not self._limit(n, "Use fewer cells."):
                    self._safe_res = (p["res_mode"], p["cells"], p["cell_size"])
            else:
                n = estimate_point_triangles(len(self.data["points"]))
                sp = _fmt(self._spacing, self.scale_m, self.ulabel)
                self.grid_lbl.setText(
                    f"The surface goes exactly through every point "
                    f"(≈ {n:,} triangles). Typical point spacing ≈ {sp} — "
                    f"for a concave rim try 2–3× that.")
                self._limit(n, "Thin out the points first.")
                self.max_edge.setEnabled(p["boundary"] == "hull")
            self.depth.setEnabled(p["skirt"])
            self.bottom.setEnabled(p["skirt"])

        _PV_ON = ("QPushButton { background: #2e9e4f; color: white; "
                  "font-weight: bold; border: 1px solid #1f7a3a; "
                  "border-radius: 3px; padding: 3px 8px; }")
        _BANNER = {
            "live": "background: #1f5f33; color: #e8ffe9;",
            "busy": "background: #7a5a12; color: #fff6e0;",
            "paused": "background: #5c4a1a; color: #ffe9b8;",
            "error": "background: #7a1f1f; color: #ffecec;",
        }

        def _banner(self, kind, text):
            self.pv_lbl.setStyleSheet(
                self._BANNER[kind] + " padding: 6px 8px; border-radius: 3px;")
            self.pv_lbl.setText(text)
            self.pv_lbl.setVisible(True)

        def _style_preview(self, on):
            if on:
                self.preview.setText("● Live Preview ON")
                self.preview.setStyleSheet(self._PV_ON)
                self.preview.setToolTip(
                    "The preview follows every change by itself — "
                    "click to switch it off.")
            else:
                self.preview.setText("Preview")
                self.preview.setStyleSheet("")
                self.preview.setToolTip(
                    "Show the terrain in the model; it then updates by "
                    "itself on every change.")
                self.pv_lbl.setVisible(False)
            if self.isVisible():
                QTimer.singleShot(0, self.adjustSize)

        def _changed(self, *_a):
            if self._busy:
                return
            self._read_ui()
            if self.cset is not None and self.cset.set_clean(
                    self.p["simplify"], self.p["smooth"], self.p["min_len"]):
                self.data["segs"] = self.cset.segs()
                self.src_lbl.setText(self._source_text())
                if self.editor is not None:
                    self.editor.fill()
                if _APP is not None:
                    _APP.viewport.update()
            self._sync()
            if self.preview.isChecked():
                if not self._block:
                    self._banner("busy", "⟳ Updating the preview…")
                self._timer.start()

        def _reset(self):
            self.p = default_params()
            self._load_into_ui()
            self._changed()

        # ---- preview ---------------------------------------------------------
        def _undo_preview(self):
            cmd = self._preview_cmd
            self._preview_cmd = None
            if cmd is None:
                return
            stack = getattr(self.viewport.history, "undo_stack", [])
            if stack and stack[-1] is cmd:
                self.viewport.history.undo()
                self.viewport.update()

        def _run(self):
            return run_terrain(self.viewport, self.data, self.p, self.old)

        def _refresh_preview(self):
            self._undo_preview()
            if not self.preview.isChecked():
                return
            if self._block:
                self._banner("paused", f"⏸ <b>Preview paused</b> — "
                             f"{self._block} It comes back by itself.")
                return
            self._banner("busy", "⟳ Updating the preview…")
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
            try:
                self._preview_cmd = self._run()
                tris, sec, unit = self._preview_cmd.stats
                self._banner(
                    "live",
                    f"● <b>LIVE PREVIEW</b> — {tris:,} {unit} "
                    f"({sec:.1f} s) · changes apply instantly")
            except Exception as exc:  # noqa: BLE001
                self._preview_cmd = None
                self._banner("error", f"Preview failed: {exc}")

        def _preview_toggled(self, on):
            self._style_preview(on)
            if on:
                self._refresh_preview()
            else:
                self._timer.stop()
                self._undo_preview()

        # ---- close -------------------------------------------------------------
        def _save(self):
            """Remember the settings — but never a resolution over the safe
            limit: one that crashed IngeTrazo must not come back."""
            p = dict(self.p)
            if self.mode == "contours" and self.data["segs"] and estimate_triangles(
                    self.data["segs"], p)[0] > MAX_TRIANGLES:
                if self._safe_res is not None:
                    p["res_mode"], p["cells"], p["cell_size"] = self._safe_res
                else:
                    d = default_params()
                    p["res_mode"], p["cells"] = d["res_mode"], d["cells"]
            save_params(p)

        def accept(self):
            self._read_ui()
            self._timer.stop()
            self._undo_preview()
            self._save()
            try:
                cmd = self._run()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, TITLE, str(exc))
                return
            tris, sec, unit = cmd.stats
            self.viewport.flash_status(
                f"{TITLE}: «{cmd.new.name}» — {tris:,} {unit} "
                f"in {sec:.1f} s (one undo step)", 5000)
            super().accept()

        def reject(self):
            self._timer.stop()
            self._undo_preview()
            self._read_ui()
            self._save()
            super().reject()

    return TopoDialog


_DIALOG = None


_OPEN = None


def _dialog(viewport, data, old, parent):
    """Non-modal: the viewport stays usable (clicking contours, orbiting)
    while the dialog is open. One dialog at a time."""
    global _DIALOG, _OPEN
    if _DIALOG is None:
        _DIALOG = _make_dialog_class()
    if _OPEN is not None:
        try:
            _OPEN.reject()
        except RuntimeError:
            pass
    dlg = _DIALOG(viewport, data, old, parent or viewport.window())
    _OPEN = dlg
    dlg.show()
    dlg.raise_()
    return dlg


def selected_terrain(scene):
    from core.group import Group
    for ent in scene.selection:
        if isinstance(ent, Group) and terrain_data(ent) is not None:
            return ent
    return None


def show_contours(viewport, parent=None):
    segs = gather_segments(viewport.scene)
    if not segs:
        viewport.flash_status(
            f"{TITLE}: select contour lines (edges or a group of them) first.",
            5000)
        return
    _dialog(viewport, {"mode": "contours", "segs": segs}, None, parent)


def show_points(viewport, parent=None):
    pts, label = gather_points(viewport.scene)
    if len(pts) < 3:
        viewport.flash_status(
            f"{TITLE}: select at least three guide points or vertices first.",
            5000)
        return
    _dialog(viewport, {"mode": "points", "points": pts, "label": label},
            None, parent)


def show_edit(viewport, parent=None):
    g = selected_terrain(viewport.scene)
    if g is None:
        viewport.flash_status(
            f"{TITLE}: select a terrain made with this plugin first.", 5000)
        return
    rec = terrain_data(g)
    data = {"mode": rec["mode"]}
    if rec["mode"] == "contours":
        data["segs"] = [tuple(s) for s in rec.get("segs", [])]
        if rec.get("lines"):
            data["cset"] = ContourSet.from_record(rec["lines"])
    else:
        data["points"] = [tuple(q) for q in rec.get("points", [])]
        data["label"] = "stored with the terrain"
    _dialog(viewport, data, g, parent)

# ---------------------------------------------------------------------------
# Toolbar (PESI3D): icons drawn in IngeTrazo's own icon style
# ---------------------------------------------------------------------------

def _pesi3d_icons():
    """Icon key → draw(painter, ink, accent) on a 48 px canvas."""
    import math  # noqa: F401
    from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: F401
    from PySide6.QtGui import (QBrush, QColor, QPainterPath, QPen,  # noqa: F401
                               QPolygonF)

    def _a(c, alpha):
        return QColor(c.red(), c.green(), c.blue(), alpha)

    def _dot(p, acc, x, y, r=3.2, color=None):
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(color or acc)
        p.drawEllipse(QPointF(x, y), r, r)
        p.restore()

    def _poly(pts):
        return QPolygonF([QPointF(x, y) for x, y in pts])

    def _thin(p, ink, alpha=120, width=1.8, dashed=False):
        pen = QPen(_a(ink, alpha), width, Qt.DashLine if dashed else Qt.SolidLine)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)

    def _pencil(p, ink, acc):
        """Small pencil in the lower right corner = «Edit …»."""
        p.save()
        p.translate(35.5, 34.5)
        p.rotate(45)
        pen = QPen(ink, 2.2)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(acc)
        p.drawRect(QRectF(-3.6, -11.0, 7.2, 13.0))
        p.setBrush(QBrush(ink))
        p.drawPolygon(_poly([(-3.6, 2.0), (3.6, 2.0), (0.0, 8.0)]))
        p.restore()

    def _blob(cx, cy, rx, ry, wob):
        path = QPainterPath()
        import math
        n = 48
        for i in range(n + 1):
            t = 2 * math.pi * i / n
            r = 1 + wob * math.sin(3 * t + 0.6) + wob * 0.6 * math.cos(2 * t)
            x, y = cx + rx * r * math.cos(t), cy + ry * r * math.sin(t)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        return path

    def topo_contours(p, ink, acc):
        p.setBrush(Qt.NoBrush)
        p.drawPath(_blob(24, 25, 17, 14, 0.07))
        p.drawPath(_blob(25, 24, 10.5, 8.5, 0.09))
        p.save()
        p.setBrush(_a(acc, 200))
        p.setPen(Qt.NoPen)
        p.drawPath(_blob(26, 23, 4.5, 3.6, 0.1))
        p.restore()

    def topo_points(p, ink, acc):
        pts = [(9, 34), (18, 22), (26, 36), (31, 16), (39, 30), (24, 27)]
        tris = [(0, 1, 5), (0, 5, 2), (1, 3, 5), (3, 4, 5), (5, 4, 2)]
        p.save()
        _thin(p, ink, 150, 1.8)
        p.setBrush(_a(acc, 70))
        for a, b, c in tris:
            p.drawPolygon(_poly([pts[a], pts[b], pts[c]]))
        p.restore()
        for x, y in pts:
            _dot(p, acc, x, y, 3.6)

    def _hill(p, ink, acc):
        path = QPainterPath()
        path.moveTo(6, 34)
        path.cubicTo(12, 32, 14, 16, 21, 15)
        path.cubicTo(27, 14, 28, 25, 33, 24)
        path.cubicTo(36, 23.5, 38, 20, 42, 21)
        fill = QPainterPath(path)
        fill.lineTo(42, 34)
        fill.closeSubpath()
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(_a(acc, 120))
        p.drawPath(fill)
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.drawLine(QPointF(6, 37), QPointF(42, 37))

    def topo_edit(p, ink, acc):
        p.save()
        p.translate(-2, -3)
        _hill(p, ink, acc)
        p.restore()
        _pencil(p, ink, acc)

    return {"contours": topo_contours, "points": topo_points, "edit": topo_edit}


def _pesi3d_toolbar(app, title, entries):
    """A toolbar of this plugin's own — one icon per command (PESI3D).

    ``entries`` = (icon key, text, tip, callable). The icons are drawn
    like IngeTrazo's own (views/icons.py: 48 px, ink = the palette's text
    colour, 3 px pen, the orange accent) and redrawn when the theme flips.
    The toolbar moves, floats and hides like the built-in ones (right-click
    on any toolbar); its place is kept by its objectName."""
    try:
        from PySide6.QtCore import QEvent, QObject, QSize, Qt
        from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
        from PySide6.QtWidgets import QApplication, QToolBar
    except Exception:  # noqa: BLE001 — no Qt, no toolbar
        return None
    win = getattr(app, "window", None)
    if win is None:
        return None
    draws = _pesi3d_icons()

    def make_icon(key):
        draw = draws.get(key)
        if draw is None:
            return QIcon()
        qa = QApplication.instance()
        ink = (QColor(qa.palette().windowText().color()) if qa is not None
               else QColor(40, 44, 52))
        pm = QPixmap(48, 48)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(ink, 3.0)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        try:
            draw(p, ink, QColor(243, 115, 41))
        finally:
            p.end()
        return QIcon(pm)

    name = f"pesi3d_{getattr(app, 'key', title)}"
    tb = None
    make = getattr(win, "_new_toolbar", None)     # the host's own builder
    if callable(make):
        try:
            tb = make(title, name)
        except Exception:  # noqa: BLE001
            tb = None
    if tb is None:
        tb = QToolBar(title, win)
        tb.setObjectName(name)
        tb.setMovable(True)
        tb.setFloatable(True)
        try:
            from views.icons import toolbar_icon_px
            px = int(toolbar_icon_px())
        except Exception:  # noqa: BLE001
            px = 24
        tb.setIconSize(QSize(px, px))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        win.addToolBar(Qt.TopToolBarArea, tb)

    actions = []
    for key, text, tip, fn in entries:
        act = QAction(make_icon(key), text, tb)
        act.setToolTip(f"{text}\n{tip}" if tip else text)
        if tip:
            act.setStatusTip(tip)
        act.triggered.connect(lambda _c=False, f=fn: f())
        tb.addAction(act)
        actions.append((act, key))

    class _ThemeWatch(QObject):
        def eventFilter(self, obj, event):  # noqa: N802 — Qt override
            if event.type() in (QEvent.PaletteChange,
                                QEvent.ApplicationPaletteChange,
                                QEvent.StyleChange):
                for a, k in actions:
                    a.setIcon(make_icon(k))
            return False

    watch = _ThemeWatch(tb)
    tb.installEventFilter(watch)
    tb._pesi3d_watch = watch
    _pesi3d_place_later(win)
    return tb


def _pesi3d_place_later(win):
    """A toolbar the saved window layout does not know yet lands at the end
    of the top row, squeezed behind the built-in ones. Once the window is
    laid out, put new PESI3D toolbars on a row of their own under the
    built-in ones — only the first time each one appears; after that the
    user's own arrangement (saved with the window) wins. Every PESI3D
    plugin carries this code; the first one to get here does it for all."""
    if getattr(win, "_pesi3d_place_pending", False):
        return
    win._pesi3d_place_pending = True
    from PySide6.QtCore import QSettings, Qt, QTimer
    from PySide6.QtWidgets import QToolBar

    def place():
        win._pesi3d_place_pending = False
        try:
            st = QSettings()
            key = "plugins/pesi3d/placed_toolbars"
            placed = st.value(key) or []
            if isinstance(placed, str):
                placed = [placed]
            placed = list(placed)
            bars = [t for t in win.findChildren(QToolBar)
                    if t.objectName().startswith("pesi3d_")]
            new = [t for t in bars if t.objectName() not in placed]
            if not new:
                return
            fresh = not placed            # no PESI3D row yet → open one
            for i, t in enumerate(sorted(new, key=lambda t: t.objectName())):
                shown = not t.isHidden()
                win.removeToolBar(t)
                if fresh and i == 0:
                    win.addToolBarBreak(Qt.TopToolBarArea)
                win.addToolBar(Qt.TopToolBarArea, t)
                t.setVisible(shown)
            st.setValue(key, placed + [t.objectName() for t in new])
        except Exception:  # noqa: BLE001 — layout only, never break the app
            pass

    QTimer.singleShot(0, place)


def setup(app) -> None:
    global _APP
    from PySide6.QtCore import QTimer
    _APP = app
    app.add_overlay(_overlay)
    app.add_pickable(_pick, on_select=_on_pick, delete=_on_delete)
    sub = app.add_menu(TITLE)
    sub.addAction("Terrain from Contours…",
                  lambda: show_contours(app.viewport, app.window))
    sub.addAction("Terrain from Points…",
                  lambda: show_points(app.viewport, app.window))
    sub.addSeparator()
    sub.addAction("Edit Terrain…", lambda: show_edit(app.viewport, app.window))

    def later(fn):
        return lambda _c=False: QTimer.singleShot(
            0, lambda: fn(app.viewport, app.window))

    def context(menu, selection) -> None:
        scene = app.viewport.scene
        if not scene.selection:
            return
        menu.addSeparator()
        if selected_terrain(scene) is not None:
            menu.addAction("Edit Terrain…").triggered.connect(
                later(show_edit))
            return
        sm = menu.addMenu(TITLE)
        sm.addAction("Terrain from Contours…").triggered.connect(
            later(show_contours))
        sm.addAction("Terrain from Points…").triggered.connect(
            later(show_points))

    app.add_context_menu(context)

    _pesi3d_toolbar(app, TITLE, [
        ("contours", "Terrain from Contours…",
         "Build a terrain from the selected contour lines.",
         lambda: show_contours(app.viewport, app.window)),
        ("points", "Terrain from Points…",
         "Build a terrain from the selected points.",
         lambda: show_points(app.viewport, app.window)),
        ("edit", "Edit Terrain…",
         "Change the settings of the selected terrain.",
         lambda: show_edit(app.viewport, app.window)),
    ])

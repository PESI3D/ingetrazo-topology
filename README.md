# Topology — terrain for IngeTrazo

Builds a terrain from **contour lines** or from **points** — for site models, landscape and urban planning.

[Deutsch → LIESMICH.md](LIESMICH.md)

## Installation
1. In IngeTrazo: **Extensions ▸ Open plugins folder** (Windows: `%APPDATA%\ingetrazo\plugins\`, Linux: `~/.local/share/ingetrazo/plugins/`).
2. Copy `topology_tool.py` into that folder.
3. Restart IngeTrazo → **Extensions ▸ Topology ▸** *Terrain from Contours… · Terrain from Points… · Edit Terrain…* (also on the right-click menu).

Requires IngeTrazo ≥ 0.5 (extension API 2).

## Features
- **Terrain from Contours** — a regular grid with smooth interpolation between the lines (no terraces). Rounded or flat **summits & pits**.
- **Smooth surface (radius)** — *Off (raw)* runs exactly through every line; a radius of 2–5 m removes the creases at the lines for a calm, natural surface.
- **Clean up lines** — **Simplify**, **Smooth** and **Ignore lines shorter than**, for jagged or messy DWG contours. Non-destructive.
- **Altitude Editor** — for contours without heights (flat 2D DWG): set heights in a list or in the viewport, number many lines at once with a 2-click profile, switch lines on/off.
- **Terrain from Points** — Delaunay TIN exactly through survey points, guide points or vertices; trim long rim triangles.
- **Footprint** — Outline · Rectangle interpolated to the full box · Rectangle cut inside the data.
- **Quads instead of triangles** (contours) — half the faces, identical shape; handy for export.
- **Skirt** with closed bottom (watertight solid), contours and a flat contour map inside the result, soft edges.
- **Live Preview** — green **● Live Preview ON** button and status line; the dialog does not block the viewport (orbit, pan, zoom).
- **Edit Terrain…** — the terrain remembers its source and settings; change anything later, one undo step.
- Safety limit at 1,000,000 triangles (override at your own risk).

Full guide: [HOWTO.md](HOWTO.md)

## Example files
The [`examples/`](examples) folder has four scenes, all starting at the origin (0, 0, 0), about 200 × 150 m:
`contours.igz` · `contours_flat.igz` (Altitude Editor) · `contours_messy.igz` (Clean up lines) · `point_cloud.igz`.

## Changelog
- **1.0** — first release.

## Licence
GPL-3.0-or-later · © 2026 Pesi (pesi3d.de)

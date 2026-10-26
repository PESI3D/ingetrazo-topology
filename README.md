# Topology — terrain for IngeTrazo

Builds a terrain from **contour lines**, from **points** or from the **spot heights of a site plan (PDF)** — for site models, landscape and urban planning.

[Deutsch → LIESMICH.md](LIESMICH.md)

## Installation
1. In IngeTrazo: **Extensions ▸ Open extensions folder** (Windows: `%APPDATA%\ingetrazo\plugins\`, Linux: `~/.local/share/ingetrazo/plugins/`).
2. Unzip `topology_tool_1.2.2.zip` (from the [release](../../releases/latest)) there, so that the folder `topology_tool` sits directly in the plugins folder. Delete an older `topology_tool.py` (1.0/1.1 was a single file).
3. Restart IngeTrazo → **Extensions ▸ Topology ▸** *Terrain from Contours… · Terrain from Points… · Terrain from Plan… · Edit Terrain…* and the **Topology** toolbar.

Requires IngeTrazo ≥ 0.5.6 (extension API 2) · tested with IngeTrazo 0.5.8.

## Features
- **Terrain from Contours** — a regular grid with smooth interpolation between the lines (no terraces). Rounded or flat **summits & pits**.
- **Smooth surface (radius)** — *Off (raw)* runs exactly through every line; a radius of 2–5 m removes the creases at the lines for a calm, natural surface.
- **Clean up lines** — **Simplify**, **Smooth** and **Ignore lines shorter than**, for jagged or messy DWG contours. Non-destructive.
- **Altitude Editor** — for contours without heights (flat 2D DWG): set heights in a list or in the viewport, number many lines at once with a 2-click profile, switch lines on/off.
- **Terrain from Points** — Delaunay TIN exactly through survey points, guide points or vertices; trim long rim triangles. Survey CSVs come in through IngeTrazo's own **Terrain ▸ Survey points ▸ Import CSV…**.
- **Terrain from Plan** — reads the **spot heights of a site plan PDF** (+402,35 · 403.120 · KD …) with their point symbols, the scale from the title block (or **Measure scale** with two clicks), shows them on the sheet to check, then builds the terrain. Floor levels and pipe inverts are left out.
- **Footprint** — Outline · Rectangle interpolated to the full box · Rectangle cut inside the data.
- **Quads instead of triangles** (contours) — half the faces, identical shape; handy for export.
- **Skirt** with closed bottom (watertight solid), contours and a flat contour map inside the result, soft edges.
- **Live Preview** — green **● Live Preview ON** button and status line; the dialog does not block the viewport (orbit, pan, zoom).
- **Edit Terrain…** — the terrain remembers its source and settings; change anything later, one undo step.
- Safety limit at 1,000,000 triangles (override at your own risk).

Full guide: [HOWTO.md](HOWTO.md)

## Example files
The [`examples/`](examples) folder has four scenes, all starting at the origin (0, 0, 0), about 200 × 150 m:
`contours.igz` · `contours_flat.igz` (Altitude Editor) · `contours_messy.igz` (Clean up lines) · `point_cloud.igz` — and `site_plan_spot_heights_1-200_A3.pdf`, an invented site plan for **Terrain from Plan**.

## Changelog
- **1.2.2** — cleaner edges in **Terrain from Points**: needle-thin, steep triangles along the rim (points lying almost in a line on the outline or almost on the box) are peeled off, so no more spikes or creases at the edge; tested with IngeTrazo 0.5.8 and 0.5.10.
- **1.2.1** — the PDF reader no longer fills IngeTrazo's log with what it repairs by itself in a PDF («incorrect startxref pointer» …); tested with IngeTrazo 0.5.8.
- **1.2** — **Terrain from Plan…**: spot heights read from a site plan (vector PDF) — height texts paired with their point symbols, scale from the plan or measured with two clicks, sheet view with green/orange points, codes (KD, KS, floor levels), duplicates removed; terrain at the origin (or real heights). Topology is now a plugin **folder** (`topology_tool/`, with pypdf inside) — delete the old `topology_tool.py`.
- **1.1** — own toolbar **Topology** with one icon per command (Terrain from Contours… · Terrain from Points… · Edit Terrain…). It starts on a row of its own under the built-in toolbars; move, float or hide it like those (right-click on a toolbar). Icons drawn in IngeTrazo's own style, they follow the light/dark theme.
- **1.0** — first release.

## Licence
GPL-3.0-or-later · © 2026 Pesi (pesi3d.de) · includes [pypdf](https://github.com/py-pdf/pypdf) (BSD-3-Clause, `topology_tool/_vendor/pypdf/LICENSE`)

# Topology — How-to

Terrain from contour lines or points for IngeTrazo.
Version 1.2 · © 2026 Pesi (pesi3d.de) · GPL-3.0-or-later

---

## 1. Installation

1. In IngeTrazo: **Extensions ▸ Open extensions folder** (Windows: `%APPDATA%\ingetrazo\plugins\`).
2. Unzip `topology_tool_1.2.1.zip` there, so that the folder **`topology_tool`** sits directly in the plugins folder (it contains `__init__.py`). Installed through **Extensions ▸ Manage extensions…** this happens by itself.
3. Delete an older **`topology_tool.py`** from the plugins folder (Topology 1.0 / 1.1 was a single file).
4. Restart IngeTrazo. The version is shown in the dialog titles (`Topology 1.2 — …`).

Menu: **Extensions ▸ Topology ▸** *Terrain from Contours… · Terrain from Points… · Terrain from Plan… · Edit Terrain…* — and the **Topology** toolbar (one icon each). Contours and points are also on the right-click menu.

---

## 2. Terrain from Contours

1. Select the contour lines (loose edges or a group, e.g. an imported DWG).
2. Right-click ▸ **Topology ▸ Terrain from Contours…**
3. Set the options, switch on **Preview**, then **OK**.

| Field | Meaning |
|---|---|
| **Cells on long side / Cell size** | Grid resolution. 300–500 cells is usually plenty. |
| **Footprint** | *Outline* = ends where the data ends · *Rectangle — interpolated* = carried out to the bounding box · *Rectangle — cut* = largest rectangle inside the data |
| **Summits & pits** | *Rounded* = domes/dips inside the highest/lowest closed line · *Flat* = flat top |
| **Smooth surface (radius)** | *Off (raw)* = the surface runs exactly through every line, with a slight crease at each one. A radius (2–5 m) rounds the whole surface off without creases and pulls it back to the lines — a few centimetres off the lines, but calm and usually closer to the real ground. |
| **Faces: Quads instead of triangles** | One four-sided face per grid cell instead of two triangles — half the faces, identical shape. Handy for export to 3ds Max, Blender etc. (contours only; points always give triangles). |
| **Soft edges** | Hides the triangle edges (smooth look). |
| **Skirt, depth** | Vertical sides down to *lowest point − depth*. |
| **Close the bottom** | Closes the base → watertight solid. |
| **Include the contours / contour map** | Adds the lines at their height / flat at the base. |

### Clean up lines (contours only)

For dense, jagged or fragmented lines (DWG from scans, vectorised plans). Non-destructive: the original lines stay, everything can be changed again with **Edit Terrain**. The viewport shows the cleaned lines.

| Field | Effect |
|---|---|
| **Simplify (tolerance)** | Drops vertices closer than this to the line through their neighbours — fewer points, same shape. Start with 0.1–0.5 m. |
| **Smooth** | 1–3 passes remove small zigzags and steps without shrinking the lines. |
| **Ignore lines shorter than** | Tiny fragments (text leftovers, stray strokes) are switched off automatically; the list marks them *too short*. |

The line below shows the result, e.g. *Vertices: 5,987 → 1,028 (83 % fewer) · 12 short lines ignored*.

**Safety limit:** above 1,000,000 triangles the info line turns red and OK is blocked (each triangle needs about 2 KB of memory). **Build anyway — at my own risk** unlocks it once — save your model first.

---

## 3. Altitude Editor (Edit Contours)

Use it when the contours have no heights (flat 2D DWG) or wrong heights. The dialog stays open while you work in the viewport.

Open it with **◂ Edit Contours** in the **Source** box (opens by itself when all lines are flat).

### Viewport colours

| Colour | Meaning |
|---|---|
| Blue | Line keeps its original height |
| Green | Height set in the editor |
| Orange (thick) | Selected |
| Grey dashed | Ignored (not used) |

### Selecting lines

* Click a line in the viewport (**Select** tool) or in the list.
* **Shift / Ctrl-click** in the list selects a range or several lines.
* **Del** in the viewport ignores the picked line.

### Editing selected lines

| Button | Effect |
|---|---|
| **Set altitude** | Gives all selected lines the value in **Selected**. |
| **✓ Use selected lines** | Switches the selected lines ON — they shape the terrain. |
| **✗ Ignore selected lines** | Switches them OFF — the terrain is made without them. |
| **Reset to original height** | Back to the height the line had in the model (not 0, unless it was at 0). |

Double-click an **Altitude** cell to type a single value. The **On** box in a selected row acts on all selected rows.

### Number the lines — many heights at once

**Start** = altitude of the first line · **Interval** = contour interval (e.g. 2 m).

**Profile: click 2 points** — fastest, one slope at a time

1. Set **Start** to the lowest line (e.g. 402) and **Interval** (e.g. 2).
2. Click the button → “Click point 1 of 2”.
3. In the viewport (top view is easiest) click **outside the lowest line**, then **on the summit**.
4. Every line the imaginary segment crosses is numbered in that order: 402, 404, 406 …

* Several hills → one profile per hill.
* Depression → click from the rim to the bottom and use a **negative Interval**.
* “The profile crossed no line.” → the segment did not touch any drawn line (clicks too close together, or one click landed on the dialog).

**Click lines in order** — full control, for complex shapes

1. Set **Start** and **Interval**, click the button.
2. Click the lines one after another in the viewport; each gets the next value (“Next: …” shows it).
3. Click the button again to stop.

| Method | Advantage | When |
|---|---|---|
| **Profile** | 2 clicks for a whole slope | simple hill or slope |
| **Click in order** | you decide the order | valleys, ridges, several peaks |
| **Set altitude / list** | exact single values | corrections, special heights |

**Tip:** after giving lines real heights (e.g. 400 m) they jump up in the viewport — use Zoom Extents or orbit.

---

## 4. Live Preview

Click **Preview** → the button turns green (**● Live Preview ON**) and a status line appears:

* green **LIVE PREVIEW** — every change applies instantly,
* amber **Updating…** / **Preview paused** — with the reason (e.g. all lines at one height); it resumes by itself,
* red — the preview failed.

Click the green button again to switch the preview off.

---

## 5. Terrain from Points

Select guide points (Tape Measure), vertices/edges or — with nothing selected — the document's survey points, then **Terrain from Points…**

> **Survey point lists (CSV) from the surveyor:** read them with IngeTrazo's own import — **Terrain** panel ▸ **Survey points** ▸ **Import CSV…** (UTM, columns point, north, east, elevation, description). Then, with nothing selected, **Terrain from Points…** builds the terrain from exactly those points.

* The surface goes exactly through every point (Delaunay TIN).
* **Max. edge length on the rim** (only with *Outline*): cuts long rim triangles → concave outline. Rule of thumb: 2–3 × the typical point spacing shown in the dialog.
* **Footprint**, **Skirt**, **Close the bottom**, **Soft edges** as above.

---

## 6. Terrain from Plan (spot heights from a PDF)

Many site plans and survey plans carry the ground as **spot heights**: a point symbol with a height next to it. **Terrain from Plan…** reads them straight out of the plan's PDF — no points have to be drawn or typed.

![Terrain from Plan](docs/terrain_from_plan.png)

### What can be read

| On the plan | Example | Used for the terrain? |
|---|---|---|
| Spot height with symbol (quartered circle, dot, cross …) | `+402,35` · `403.120` | **yes** — the point is the symbol, not the text |
| Manhole cover | `KD 400,95` | **yes** (lies on the ground) |
| Pipe / manhole invert | `KS 398,70` | no — starts unticked |
| Floor level of a building | `±0,00 = +403,33` | no — starts unticked |
| Slopes, Δh, dimensions | `2,5%` · `Δh 0.70` · `28,00` | ignored |

The PDF has to be a **vector PDF** from CAD (ArchiCAD, AutoCAD, Vectorworks …). A scanned plan (only a picture) cannot be read.

### Step by step

1. **Extensions ▸ Topology ▸ Terrain from Plan…** (or the icon in the **Topology** toolbar) and choose the PDF. A multi-page PDF gets a **Sheet** selector.
2. **Check the scale.** **Scale 1 :** shows the scale read from the plan, in green: *read from the plan: «1:200»*. In orange it was not found — type it in or use **Measure scale…**:
   * click two points on the sheet whose real distance you know (clicks snap to line ends — a dimension line, a grid, a building edge),
   * type the real distance in metres → the scale is worked out, shown in blue: *measured: 50.0 mm on paper = 10 m*.
3. **Check the points.** Left: the sheet with every height found — **green** = used, **orange** = left out. Right: the table with **Point / text** (as written on the plan), **Easting / Northing** (metres on the plan), **Height**, **Code** and **Note**.
   * Click a point on the sheet → its row is selected; select a row → the point is ringed in blue with its text.
   * **Use** ticks or unticks a single height.
   * **Codes** ticks or unticks a whole kind (e.g. tick *KS* if you want pipe inverts in, untick *KD* to leave covers out).
   * **Show only points with a note** lists just the doubtful ones. Notes: *no point symbol found — text position used* (the height is placed where its text starts), *duplicate of row n* (the same height twice at one spot), *floor level — not the ground*, *height far off*.
   * Wheel = zoom, drag = pan, double-click = whole sheet.
4. **Placement:** the south-west corner of the heights goes to x 0 / y 0 and the lowest height to z 0 — the terrain is right at the origin. Tick **Keep real heights** for z = height above sea level.
5. **Build Terrain ▸** opens *Terrain from Points* (section 5) with the live preview: footprint, skirt, closed bottom, soft edges — then **OK**. The terrain remembers the plan; **Edit Terrain…** works as usual.

### Tips

* Few heights give a rough terrain — a plan with heights only along the driveway gives a terrain only there. Combine with contour lines where you have them.
* The scale is the one thing to check every time: a wrong scale makes the terrain too big or too small (the heights stay right).
* If a height sits in the wrong place, untick it — or measure the scale again if *all* positions look stretched.

---

## 7. Edit Terrain

Select a terrain made with this plugin ▸ right-click ▸ **Edit Terrain…** — source, heights, on/off states and all settings come back; OK rebuilds it in place (one undo step).

---

## 8. Example files

The `examples/` folder has four scenes to try the plugin with (**File ▸ Open**) — all of them start at the origin (0, 0, 0) and measure about 200 × 150 m — and one plan for **Terrain from Plan…**.

| File | Contains | Try |
|---|---|---|
| `contours.igz` | Group *Contours*: clean contour lines, heights 0–34 m | **Terrain from Contours…** with the default settings |
| `contours_flat.igz` | Group *Contours_Flat*: the same lines, all at height 0 (like a flat 2D DWG) | **Altitude Editor** — *Number the lines* to give them heights |
| `contours_messy.igz` | Group *Contours_Messy*: zigzagging lines plus small stray fragments | **Clean up lines** — Simplify, Smooth, Ignore lines shorter than |
| `point_cloud.igz` | 860 survey points (guide points) | **Terrain from Points…** — Footprint, Max. edge length on the rim |
| `site_plan_spot_heights_1-200_A3.pdf` | An invented site plan 1:200 with 27 spot heights (quartered circles and red survey dots), manholes (KD/KS) and a floor level | **Terrain from Plan…** — scale is read from the title block |

# Topology — Gelände für IngeTrazo

Erzeugt ein Gelände aus **Höhenlinien** oder aus **Punkten** — für Lagepläne, Landschafts- und Städtebau.

[English → README.md](README.md)

## Installation
1. In IngeTrazo: **Extensions ▸ Open plugins folder** (Windows: `%APPDATA%\ingetrazo\plugins\`, Linux: `~/.local/share/ingetrazo/plugins/`).
2. `topology_tool.py` in diesen Ordner kopieren.
3. IngeTrazo neu starten → **Extensions ▸ Topology ▸** *Terrain from Contours… · Terrain from Points… · Edit Terrain…* (auch im Rechtsklick-Menü).

Benötigt IngeTrazo ≥ 0.5 (Extension-API 2).

## Funktionen
- **Terrain from Contours** — regelmäßiges Raster mit weicher Interpolation zwischen den Linien (keine Terrassen). Kuppen und Senken rund oder flach (**Summits & pits**).
- **Smooth surface (radius)** — *Off (raw)* geht exakt durch jede Linie; ein Radius von 2–5 m nimmt die Knicke an den Linien heraus — ruhige, natürliche Fläche.
- **Clean up lines** — **Simplify**, **Smooth** und **Ignore lines shorter than** für zackige oder unsaubere DWG-Höhenlinien. Nicht-destruktiv.
- **Altitude Editor** — für Höhenlinien ohne Höhe (flaches 2D-DWG): Höhen in der Liste oder im Viewport setzen, viele Linien mit einem 2-Klick-Profil durchnummerieren, Linien ein-/ausschalten.
- **Terrain from Points** — Delaunay-TIN exakt durch Vermessungspunkte, Hilfspunkte oder Vertices; lange Randdreiecke abschneiden.
- **Footprint** — Outline · Rechteck auf die volle Box interpoliert · Rechteck innerhalb der Daten geschnitten.
- **Quads statt Dreiecke** (Höhenlinien) — halb so viele Flächen, gleiche Form; praktisch für den Export.
- **Skirt** mit geschlossenem Boden (wasserdichter Körper), Höhenlinien und flacher Höhenplan im Ergebnis, weiche Kanten.
- **Live Preview** — grüner Button **● Live Preview ON** und Statuszeile; der Dialog blockiert den Viewport nicht (Orbit, Pan, Zoom).
- **Edit Terrain…** — das Gelände merkt sich Quelle und Einstellungen; alles später änderbar, ein Undo-Schritt.
- Sicherheitsgrenze bei 1.000.000 Dreiecken (auf eigenes Risiko übergehbar).

Ausführliche Anleitung: [ANLEITUNG.md](ANLEITUNG.md)

## Beispieldateien
Im Ordner [`examples/`](examples) liegen vier Szenen, alle ab Nullpunkt (0, 0, 0), ca. 200 × 150 m:
`contours.igz` · `contours_flat.igz` (Altitude Editor) · `contours_messy.igz` (Clean up lines) · `point_cloud.igz`.

## Änderungen
- **1.1** — eigene Werkzeugleiste **Topology** mit einem Icon je Befehl (Terrain from Contours… · Terrain from Points… · Edit Terrain…). Sie erscheint in einer eigenen Zeile unter den eingebauten Leisten und lässt sich wie diese verschieben, abdocken oder ausblenden (Rechtsklick auf eine Leiste). Icons im Stil von IngeTrazo, passend zum hellen/dunklen Theme.
- **1.0** — erste Veröffentlichung.

## Lizenz
GPL-3.0-or-later · © 2026 Pesi (pesi3d.de)

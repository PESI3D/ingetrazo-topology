# Topology — Gelände für IngeTrazo

Erzeugt ein Gelände aus **Höhenlinien**, aus **Punkten** oder aus den **Höhenkoten eines Lageplans (PDF)** — für Lagepläne, Landschafts- und Städtebau.

[English → README.md](README.md)

## Installation
1. In IngeTrazo: **Extensions ▸ Open extensions folder** (Windows: `%APPDATA%\ingetrazo\plugins\`, Linux: `~/.local/share/ingetrazo/plugins/`).
2. `topology_tool_1.2.2.zip` (aus dem [Release](../../releases/latest)) dort entpacken, sodass der Ordner `topology_tool` direkt im Plugins-Ordner liegt. Eine ältere `topology_tool.py` löschen (1.0/1.1 war eine einzelne Datei).
3. IngeTrazo neu starten → **Extensions ▸ Topology ▸** *Terrain from Contours… · Terrain from Points… · Terrain from Plan… · Edit Terrain…* und die Werkzeugleiste **Topology**.

Benötigt IngeTrazo ≥ 0.5.6 (Extension-API 2) · getestet mit IngeTrazo 0.6.0.

## Funktionen
- **Terrain from Contours** — regelmäßiges Raster mit weicher Interpolation zwischen den Linien (keine Terrassen). Kuppen und Senken rund oder flach (**Summits & pits**).
- **Smooth surface (radius)** — *Off (raw)* geht exakt durch jede Linie; ein Radius von 2–5 m nimmt die Knicke an den Linien heraus — ruhige, natürliche Fläche.
- **Clean up lines** — **Simplify**, **Smooth** und **Ignore lines shorter than** für zackige oder unsaubere DWG-Höhenlinien. Nicht-destruktiv.
- **Altitude Editor** — für Höhenlinien ohne Höhe (flaches 2D-DWG): Höhen in der Liste oder im Viewport setzen, viele Linien mit einem 2-Klick-Profil durchnummerieren, Linien ein-/ausschalten.
- **Terrain from Points** — Delaunay-TIN exakt durch Vermessungspunkte, Hilfspunkte oder Vertices; lange Randdreiecke abschneiden. Vermesser-CSV kommen über IngeTrazos eigenes **Terrain ▸ Survey points ▸ Import CSV…** herein.
- **Terrain from Plan** — liest die **Höhenkoten eines Lageplans (PDF)** (+402,35 · 403.120 · KD …) samt Punktsymbol, den Maßstab aus dem Plankopf (oder **Measure scale** mit zwei Klicks), zeigt sie zum Prüfen im Plan und baut daraus das Gelände. Fußbodenhöhen und Kanalsohlen bleiben draußen.
- **Footprint** — Outline · Rechteck auf die volle Box interpoliert · Rechteck innerhalb der Daten geschnitten.
- **Quads statt Dreiecke** (Höhenlinien) — halb so viele Flächen, gleiche Form; praktisch für den Export.
- **Skirt** mit geschlossenem Boden (wasserdichter Körper), Höhenlinien und flacher Höhenplan im Ergebnis, weiche Kanten.
- **Live Preview** — grüner Button **● Live Preview ON** und Statuszeile; der Dialog blockiert den Viewport nicht (Orbit, Pan, Zoom).
- **Edit Terrain…** — das Gelände merkt sich Quelle und Einstellungen; alles später änderbar, ein Undo-Schritt.
- Sicherheitsgrenze bei 1.000.000 Dreiecken (auf eigenes Risiko übergehbar).

Ausführliche Anleitung: [ANLEITUNG.md](ANLEITUNG.md)

## Beispieldateien
Im Ordner [`examples/`](examples) liegen vier Szenen, alle ab Nullpunkt (0, 0, 0), ca. 200 × 150 m:
`contours.igz` · `contours_flat.igz` (Altitude Editor) · `contours_messy.igz` (Clean up lines) · `point_cloud.igz` — dazu `site_plan_spot_heights_1-200_A3.pdf`, ein erfundener Lageplan für **Terrain from Plan**.

## Einheiten
Der Dialog arbeitet in den **Einheiten des geöffneten Dokuments** — eingestellt in IngeTrazo unter **Fenster ▸ Einheiten…** (Strg+U) oder **Fenster ▸ Einstellungen… ▸ Einheiten**:
- Längenfelder zeigen und lesen die Längeneinheit und -form des Dokuments: m, cm, mm, in, ft, Fuß-Zoll (`9'10"`) oder Fuß-Zoll mit Brüchen (`9'10 1/8"`).
- Eingabe mit oder ohne Einheit: `25`, `2,5 cm`, `1'2"`, `3/4"`. Eine Zahl ohne Einheit gilt in der Dokumenteinheit (im Fuß-Zoll-Dokument: Zoll); ein Wert in einer anderen Einheit wird umgerechnet.
- Winkel stehen in der Winkeleinheit des Dokuments (Grad, Radiant oder Gon), Flächen in seiner Flächeneinheit; Prozentfelder und Anzahlen haben keine Einheit. Das Dezimaltrennzeichen folgt dem Dokument — beim Tippen gehen `.` und `,`.
- Ein Wechsel der Einheiten ändert nur Anzeige und Eingabe — das Modell bleibt exakt gleich. Nach dem Ändern den Dialog neu öffnen.
- In IngeTrazo-Versionen vor 0.5.8 sind die Felder einfache Zahlen in Metern.
- **Aus einem Plan gelesene Höhen bleiben in der Einheit des Plans** und werden darin gerechnet: ein metrischer Plan (`1:200`, `+402,35`) wird in Metern gelesen, ein Zoll-Plan (`1" = 20'`, `512'-6"`, `513.25'`) in Fuß. **Heights in** bleibt auf *Automatic (from the plan)* — m, cm, mm, ft und in nur für den Notfall (Höhen in einer anderen Einheit geschrieben, z. B. cm). Ist der Plan in einem anderen System als das Dokument, stellt ein Häkchen daneben (vorgewählt) das Dokument beim Erzeugen des Geländes auf die Einheit des Plans um. Details: [Anleitung](ANLEITUNG.md#9-einheiten).

## Änderungen
- **1.3** — **Dokumenteinheiten**: alle Felder, der Höhen-Editor und die Vermessungstabelle zeigen und lesen die Einheiten aus Einstellungen ▸ Einheiten; **Terrain from Plan…**: die Einheit des Plans ist die Arbeitseinheit — neu **Heights in** (*Automatic (from the plan)*; m, cm, mm, ft, in zum Umrechnen), **Zoll-Pläne** (Maßstäbe `1" = 20'`, Höhen `512'-6"` / `513.25'`, US-Codes FF, TW, INV abgehakt) und ein Häkchen, das das Dokument auf die Einheit des Plans umstellt; *Measure scale…* nimmt die Strecke in jeder Einheit. Getestet mit IngeTrazo 0.6.0.
- **1.2.2** — sauberere Ränder bei **Terrain from Points**: nadeldünne, steile Dreiecke am Rand (Punkte fast in einer Linie auf dem Umriss oder fast auf dem Rechteck) werden entfernt — keine Zacken oder Knicke mehr an der Kante; getestet mit IngeTrazo 0.5.8 und 0.5.10.
- **1.2.1** — der PDF-Leser füllt IngeTrazos Protokoll nicht mehr mit dem, was er in einem PDF selbst repariert («incorrect startxref pointer» …); getestet mit IngeTrazo 0.5.8.
- **1.2** — **Terrain from Plan…**: Höhenkoten aus einem Lageplan (Vektor-PDF) — Höhentexte samt Punktsymbol, Maßstab aus dem Plan oder mit zwei Klicks gemessen, Planansicht mit grünen/orangen Punkten, Codes (KD, KS, Fußbodenhöhen), Doppelte entfernt; Gelände im Nullpunkt (oder mit echten Höhen). Topology ist jetzt ein Plugin-**Ordner** (`topology_tool/`, mit pypdf darin) — die alte `topology_tool.py` löschen.
- **1.1** — eigene Werkzeugleiste **Topology** mit einem Icon je Befehl (Terrain from Contours… · Terrain from Points… · Edit Terrain…). Sie erscheint in einer eigenen Zeile unter den eingebauten Leisten und lässt sich wie diese verschieben, abdocken oder ausblenden (Rechtsklick auf eine Leiste). Icons im Stil von IngeTrazo, passend zum hellen/dunklen Theme.
- **1.0** — erste Veröffentlichung.

## Lizenz
GPL-3.0-or-later · © 2026 Pesi (pesi3d.de) · enthält [pypdf](https://github.com/py-pdf/pypdf) (BSD-3-Clause, `topology_tool/_vendor/pypdf/LICENSE`)

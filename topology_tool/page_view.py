# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""The sheet as a picture, inside the Plan Import dialog.

Drawn from what the importer reads anyway — the page's paths (in their
fill and stroke colours) and its texts at their positions — so no PDF
viewer module is needed (IngeTrazo's builds do not ship Qt's). Texts that
read like a plan scale («1:100») are marked in red, so the scale can be
read off the sheet. Wheel: zoom at the cursor · drag: pan · double-click:
whole sheet.
"""
from __future__ import annotations

LONG_SIDE = 4096      # px of the rendered sheet's long side (zoom limit)


def render_page(vec, texts, long_side: int = LONG_SIDE):
    """``QImage`` of the sheet: white paper, filled shapes, lines, texts.
    ``vec`` = PageVectors, ``texts`` = [PageText] (may be empty)."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter,
                               QPainterPath, QPen)

    w, h = max(vec.width, 1.0), max(vec.height, 1.0)
    s = long_side / max(w, h)
    img = QImage(max(1, int(w * s)), max(1, int(h * s)),
                 QImage.Format.Format_RGB32)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def col(rgb):
        r, g, b = (max(0, min(255, int(round(c * 255)))) for c in rgb)
        return QColor(r, g, b)

    def qpath(runs):
        path = QPainterPath()
        last = None
        for pts, _c in runs:
            x0, y0 = pts[0]
            if last is None or abs(last[0] - x0) > 1e-6 \
                    or abs(last[1] - y0) > 1e-6:
                path.moveTo(x0 * s, (h - y0) * s)
            for x, y in pts[1:]:
                path.lineTo(x * s, (h - y) * s)
            last = pts[-1]
        return path

    no_pen = QPen(Qt.PenStyle.NoPen)
    for pp in vec.paths:
        path = qpath(pp.runs)
        if pp.fill:
            path.setFillRule(Qt.FillRule.OddEvenFill if pp.evenodd
                             else Qt.FillRule.WindingFill)
            p.fillPath(path, QBrush(col(pp.fill_rgb)))
        if pp.stroke:
            pen = QPen(col(pp.stroke_rgb), max(pp.width * s, 0.6))
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.strokePath(path, pen)
    p.setPen(no_pen)

    font = QFont("Arial")
    for t in texts or ():
        px = t.size * s
        if px < 1.0:
            continue
        font.setPixelSize(max(1, int(round(px))))
        font.setBold(t.scale)
        p.save()
        p.translate(t.x * s, (h - t.y) * s)
        p.rotate(-t.angle)
        p.setFont(font)
        if t.scale:
            fm = p.fontMetrics()
            r = fm.boundingRect(t.text)
            p.fillRect(r.adjusted(-3, -2, 3, 2), QColor(255, 225, 225))
            p.setPen(QColor(210, 0, 0))
        else:
            p.setPen(QColor(30, 30, 30))
        p.drawText(QPointF(0, 0), t.text)
        p.restore()
    p.end()
    return img


def page_points(vec):
    """Every path vertex of the sheet in page points — snap targets for
    measuring (``(N, 2)`` float array)."""
    import numpy as np
    pts = [pt for pp in vec.paths for run, _c in pp.runs for pt in run]
    if not pts:
        return np.zeros((0, 2))
    return np.unique(np.round(np.asarray(pts, dtype=float), 3), axis=0)


def make_page_view_class():
    import math

    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen
    from PySide6.QtWidgets import QSizePolicy, QWidget

    SNAP_PX = 10          # snap radius on screen

    class PageView(QWidget):
        """Zoomable picture of one sheet; can measure a distance on it."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setMinimumSize(360, 300)
            self.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Expanding)
            self.setMouseTracking(True)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._img = None
            self._msg = "No page."
            self._caption = ""
            self._zoom = 1.0           # screen px per image px
            self._off = QPointF(0, 0)  # image origin on screen
            self._drag = None
            self._fitted = True
            self._page = None          # (w_pt, h_pt) of the shown sheet
            # measuring
            self._measure = None       # callback(dist_pt, a_pt, b_pt)
            self._snap = None          # (N, 2) page points
            self._pts = []             # picked points (page pt)
            self._hover = None         # (page pt, snapped)
            # crop frames
            self._crops = []           # [(rect, current, label)]
            self._rect_cb = None       # callback(rect) while drawing one
            self._rect_a = None        # first corner (page pt)
            self._rect_b = None
            self._npts = 2             # 2 = measure, 1 = pick a point
            self._refs = []            # [(pt, current, label)]

        # ---- content ---------------------------------------------------
        def set_message(self, text):
            self.cancel_measure()
            self._img = None
            self._msg = text
            self.update()

        def set_image(self, img, caption="", keep_view=False, page=None):
            if page is not None and page != self._page:
                self.cancel_measure()
                self.cancel_rect()
            self._img = img
            self._page = page
            self._caption = caption
            if not keep_view or self._fitted:
                self.fit()
            self.update()

        def fit(self):
            self._fitted = True
            if self._img is None:
                return
            m = 8
            sw = max(1, self.width() - 2 * m)
            sh = max(1, self.height() - 2 * m - 18)
            self._zoom = min(sw / self._img.width(),
                             sh / self._img.height())
            self._off = QPointF(
                (self.width() - self._img.width() * self._zoom) / 2,
                m + (sh - self._img.height() * self._zoom) / 2)
            self.update()

        # ---- page pt <-> screen ----------------------------------------
        def _k(self):
            """Image px per page point."""
            w, h = self._page
            return self._img.width() / max(w, 1e-9)

        def _to_screen(self, x, y):
            k = self._k() * self._zoom
            return QPointF(self._off.x() + x * k,
                           self._off.y() + (self._page[1] - y) * k)

        def _to_page(self, pos):
            k = self._k() * self._zoom
            return ((pos.x() - self._off.x()) / k,
                    self._page[1] - (pos.y() - self._off.y()) / k)

        # ---- crop frames -----------------------------------------------
        def set_crops(self, crops):
            """``[(rect, current, label)]`` — frames drawn on the sheet;
            outside the current one is dimmed."""
            self._crops = list(crops or [])
            self.update()

        def start_rect(self, callback):
            """Left-drag draws a frame → ``callback((x0, y0, x1, y1))`` in
            page points. Wheel zooms, middle-drag pans, Esc cancels."""
            if self._img is None or self._page is None:
                return False
            self.cancel_measure()
            self._rect_cb = callback
            self._rect_a = self._rect_b = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setFocus()
            self.update()
            return True

        def cancel_rect(self):
            if self._rect_cb is None:
                return
            self._rect_cb = None
            self._rect_a = self._rect_b = None
            self.unsetCursor()
            self.update()

        def drawing_rect(self):
            return self._rect_cb is not None

        def _paint_crops(self, p):
            if not self._crops and self._rect_a is None:
                return
            from PySide6.QtGui import QPainterPath
            cur = [c for c in self._crops if c[1]]
            if cur and self._rect_a is None:
                # dim everything outside the current frame
                r0 = cur[0][0]
                a = self._to_screen(r0[0], r0[3])
                b = self._to_screen(r0[2], r0[1])
                outer = QPainterPath()
                outer.addRect(QRectF(self.rect()))
                inner = QPainterPath()
                inner.addRect(QRectF(a, b))
                p.fillPath(outer.subtracted(inner), QColor(0, 0, 0, 90))
            for rect, current, label in self._crops:
                a = self._to_screen(rect[0], rect[3])
                b = self._to_screen(rect[2], rect[1])
                pen = QPen(QColor(255, 140, 0) if current
                           else QColor(40, 110, 230), 2.5 if current else 1.5)
                if not current:
                    pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(a, b))
                if label:
                    r = p.fontMetrics().boundingRect(label)
                    box = QRectF(a.x(), a.y() - r.height() - 4,
                                 r.width() + 8, r.height() + 4)
                    p.fillRect(box, pen.color())
                    p.setPen(QColor(255, 255, 255))
                    p.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
            if self._rect_a is not None and self._rect_b is not None:
                a = self._to_screen(*self._rect_a)
                b = self._to_screen(*self._rect_b)
                p.setPen(QPen(QColor(255, 140, 0), 2, Qt.PenStyle.DashLine))
                p.setBrush(QColor(255, 140, 0, 40))
                p.drawRect(QRectF(a, b).normalized())

        # ---- measuring -------------------------------------------------
        def start_measure(self, snap_points, callback):
            self.cancel_rect()
            self._npts = 2
            return self._start_measure(snap_points, callback)

        def start_pick(self, snap_points, callback):
            """One click on the sheet → ``callback((x, y))`` (page points,
            snapped to line ends)."""
            self.cancel_rect()
            self.cancel_measure()
            self._npts = 1
            return self._start_measure(snap_points, callback)

        def picking(self):
            return self._measure is not None and self._npts == 1

        def set_refs(self, refs):
            """Reference points to show: ``[((x, y), current, label)]``."""
            self._refs = list(refs or [])
            self.update()

        def _paint_refs(self, p):
            if not self._refs:
                return
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            for (x, y), current, label in self._refs:
                c = self._to_screen(x, y)
                col = QColor(230, 120, 0) if current else QColor(40, 110, 220)
                p.setPen(QPen(col, 2))
                p.drawEllipse(c, 7, 7)
                p.drawLine(QPointF(c.x() - 12, c.y()),
                           QPointF(c.x() + 12, c.y()))
                p.drawLine(QPointF(c.x(), c.y() - 12),
                           QPointF(c.x(), c.y() + 12))
                if label:
                    r = p.fontMetrics().boundingRect(label)
                    box = QRectF(c.x() + 10, c.y() - 10 - r.height(),
                                 r.width() + 8, r.height() + 4)
                    p.fillRect(box, col)
                    p.setPen(QColor(255, 255, 255))
                    p.drawText(box.adjusted(4, 0, 0, 0),
                               Qt.AlignmentFlag.AlignVCenter
                               | Qt.AlignmentFlag.AlignLeft, label)

        def _start_measure(self, snap_points, callback):
            """Two clicks on the sheet → ``callback(dist_pt, a, b)``.
            Clicks snap to line ends; drag still pans, wheel still zooms,
            Esc cancels."""
            if self._img is None or self._page is None:
                return False
            self._measure = callback
            self._snap = snap_points
            self._pts = []
            self._hover = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setFocus()
            self.update()
            return True

        def cancel_measure(self):
            if self._measure is None:
                return
            self._measure = None
            self._pts = []
            self._hover = None
            self.unsetCursor()
            self.update()

        def measuring(self):
            return self._measure is not None

        def _snapped(self, pos):
            x, y = self._to_page(pos)
            if self._snap is not None and len(self._snap):
                import numpy as np
                k = self._k() * self._zoom
                d = np.hypot(self._snap[:, 0] - x, self._snap[:, 1] - y)
                i = int(np.argmin(d))
                if d[i] * k <= SNAP_PX:
                    return (float(self._snap[i, 0]),
                            float(self._snap[i, 1])), True
            return (x, y), False

        # ---- painting --------------------------------------------------
        def paintEvent(self, _ev):
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(96, 98, 102))
            if self._img is None:
                p.setPen(QColor(235, 235, 235))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           self._msg)
                return
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,
                            self._zoom < 1.0)
            target = QRectF(self._off.x(), self._off.y(),
                            self._img.width() * self._zoom,
                            self._img.height() * self._zoom)
            p.fillRect(target.adjusted(2, 2, 2, 2), QColor(60, 60, 60))
            p.drawImage(target, self._img)
            self._paint_crops(p)
            self._paint_refs(p)
            if self._measure is not None:
                self._paint_measure(p)
            band = QRectF(0, self.height() - 18, self.width(), 18)
            p.fillRect(band, QColor(40, 40, 40, 200))
            p.setPen(QColor(235, 235, 235))
            if self._measure is not None and self._npts == 1:
                text = ("REFERENCE POINT: click it (snaps to line ends) · "
                        "drag: pan · wheel: zoom · Esc: cancel")
            elif self._measure is not None:
                hint = ("MEASURE: click start and end of a known distance "
                        "(snaps to line ends) · drag: pan · wheel: zoom · "
                        "Esc: cancel")
                text = hint
            elif self._rect_cb is not None:
                text = ("CROP: drag a frame around the drawing · "
                        "middle-drag: pan · wheel: zoom · Esc: cancel")
            else:
                hint = "Wheel: zoom · drag: pan · double-click: whole sheet"
                text = (f"{self._caption}   —   {hint}" if self._caption
                        else hint)
            p.drawText(band.adjusted(6, 0, -6, 0),
                       Qt.AlignmentFlag.AlignVCenter
                       | Qt.AlignmentFlag.AlignLeft, text)

        def _paint_measure(self, p):
            from .pdf_vectors import PT_TO_MM
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            red = QColor(220, 30, 30)
            pts = list(self._pts)
            if self._hover is not None and len(pts) < self._npts:
                pts_line = pts + [self._hover[0]]
            else:
                pts_line = pts
            if len(pts_line) == 2:
                a = self._to_screen(*pts_line[0])
                b = self._to_screen(*pts_line[1])
                p.setPen(QPen(red, 2))
                p.drawLine(a, b)
                d_mm = math.dist(pts_line[0], pts_line[1]) * PT_TO_MM
                p.setPen(QColor(255, 255, 255))
                mid = QPointF((a.x() + b.x()) / 2 + 8,
                              (a.y() + b.y()) / 2 - 8)
                label = f"{d_mm:.1f} mm on paper"
                r = p.fontMetrics().boundingRect(label)
                p.fillRect(QRectF(mid.x() - 3, mid.y() - r.height(),
                                  r.width() + 6, r.height() + 4),
                           QColor(180, 20, 20))
                p.drawText(mid, label)
            for q in pts:
                c = self._to_screen(*q)
                p.setPen(QPen(red, 2))
                p.drawEllipse(c, 5, 5)
            if self._hover is not None:
                c = self._to_screen(*self._hover[0])
                if self._hover[1]:
                    p.setPen(QPen(QColor(0, 160, 0), 2))
                    p.drawRect(QRectF(c.x() - 5, c.y() - 5, 10, 10))
                else:
                    p.setPen(QPen(red, 1))
                    p.drawLine(QPointF(c.x() - 6, c.y()),
                               QPointF(c.x() + 6, c.y()))
                    p.drawLine(QPointF(c.x(), c.y() - 6),
                               QPointF(c.x(), c.y() + 6))

        def resizeEvent(self, ev):
            if self._fitted:
                self.fit()
            super().resizeEvent(ev)

        # ---- navigation ------------------------------------------------
        def wheelEvent(self, ev):
            if self._img is None:
                return
            step = ev.angleDelta().y() / 120.0
            factor = 1.25 ** step
            z_fit = min(self.width() / self._img.width(),
                        self.height() / self._img.height())
            new = max(z_fit * 0.5, min(4.0, self._zoom * factor))
            pos = ev.position()
            # keep the image point under the cursor where it is
            ix = (pos.x() - self._off.x()) / self._zoom
            iy = (pos.y() - self._off.y()) / self._zoom
            self._zoom = new
            self._off = QPointF(pos.x() - ix * new, pos.y() - iy * new)
            self._fitted = False
            self.update()

        def mousePressEvent(self, ev):
            if self._rect_cb is not None and \
                    ev.button() == Qt.MouseButton.LeftButton:
                self._rect_a = self._to_page(ev.position())
                self._rect_b = self._rect_a
                self.update()
                return
            if ev.button() in (Qt.MouseButton.LeftButton,
                               Qt.MouseButton.MiddleButton):
                self._drag = (ev.position(), QPointF(self._off), False)

        def mouseMoveEvent(self, ev):
            if self._rect_cb is not None and self._rect_a is not None \
                    and self._drag is None:
                self._rect_b = self._to_page(ev.position())
                self.update()
                return
            if self._drag is not None:
                start, off, moved = self._drag
                d = ev.position() - start
                if moved or abs(d.x()) + abs(d.y()) > 4:
                    self._drag = (start, off, True)
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    self._off = off + d
                    self._fitted = False
                    self.update()
                return
            if self._measure is not None and self._img is not None:
                self._hover = self._snapped(ev.position())
                self.update()

        def mouseReleaseEvent(self, ev):
            if self._rect_cb is not None and self._rect_a is not None \
                    and ev.button() == Qt.MouseButton.LeftButton:
                a, b = self._rect_a, self._to_page(ev.position())
                cb = self._rect_cb
                self.cancel_rect()
                rect = (min(a[0], b[0]), min(a[1], b[1]),
                        max(a[0], b[0]), max(a[1], b[1]))
                k = self._k() * self._zoom
                if (rect[2] - rect[0]) * k > 8 and (rect[3] - rect[1]) * k > 8:
                    w, h = self._page
                    rect = (max(0.0, rect[0]), max(0.0, rect[1]),
                            min(w, rect[2]), min(h, rect[3]))
                    cb(rect)
                return
            drag = self._drag
            self._drag = None
            if self._measure is not None or self._rect_cb is not None:
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.unsetCursor()
            if drag is None or drag[2] or self._measure is None:
                return
            if ev.button() != Qt.MouseButton.LeftButton:
                return
            q, _snap = self._snapped(ev.position())
            self._pts.append(q)
            self.update()
            if self._npts == 1:
                cb = self._measure
                self.cancel_measure()
                cb(q)
                return
            if len(self._pts) == 2:
                a, b = self._pts
                cb = self._measure
                self.cancel_measure()
                d = math.dist(a, b)
                if d > 1e-6:
                    cb(d, a, b)

        def mouseDoubleClickEvent(self, _ev):
            if self._measure is None and self._rect_cb is None:
                self.fit()

        def keyPressEvent(self, ev):
            if ev.key() == Qt.Key.Key_Escape and self._measure is not None:
                self.cancel_measure()
                ev.accept()
                return
            if ev.key() == Qt.Key.Key_Escape and self._rect_cb is not None:
                self.cancel_rect()
                ev.accept()
                return
            super().keyPressEvent(ev)

    return PageView

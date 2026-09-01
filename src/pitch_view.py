from __future__ import annotations
import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QMenu, QGraphicsLineItem, QGraphicsPathItem, QToolTip,
)
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, Slot
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QCursor, QPolygonF, QPainterPath,
)

pg.setConfigOption("background", "w")
pg.setConfigOption("foreground", "k")


class _ClippedPathItem(QGraphicsPathItem):
    """Path item whose painting is clipped to a fixed scene-space rect."""

    def __init__(self, path: QPainterPath, clip_rect: QRectF):
        super().__init__(path)
        self._clip_rect = QRectF(clip_rect)

    def paint(self, painter, option, widget=None):
        painter.save()
        painter.setClipRect(self._clip_rect)
        super().paint(painter, option, widget)
        painter.restore()

# Note names for the pitch (Y) axis, based on A4 = 440 Hz equal temperament.
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_A4_MIDI = 69  # A4


def _midi_to_freq(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - _A4_MIDI) / 12.0)


def _freq_to_midi(freq: float) -> float:
    return _A4_MIDI + 12.0 * math.log2(max(freq, 1e-6) / 440.0)


def _note_name(midi: int) -> str:
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


# Unvoiced stretches longer than this are not drawn at all (only short gaps
# between voiced parts keep the dashed baseline marker).
SILENT_HIDE_S = 1.0


class PitchView(QGraphicsView):
    selection_changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent = parent

        self._times: np.ndarray | None = None
        self._f0: np.ndarray | None = None
        self._confidence: np.ndarray | None = None
        self._fmin: float = 50.0
        self._fmax: float = 500.0
        self._duration: float = 0.0
        self._title: str = ""
        self._zoom: float = 1.0
        self._view_offset: float = 0.0  # start of the visible window (seconds)
        self._playhead_time: float = -1.0

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)

        self._bg_rect = self._scene.addRect(0, 0, 1, 1)
        self._bg_rect.setBrush(QBrush(QColor(245, 245, 245)))
        self._bg_rect.setPen(QPen(QColor(200, 200, 200)))

        self._title_item = self._scene.addText("")
        self._title_item.setFont(QFont("Consolas", 12))
        self._title_item.setPos(10, 8)

        self._time_label = self._scene.addText("")
        self._time_label.setFont(QFont("Consolas", 8))

        self._playhead_line = QGraphicsLineItem()
        self._playhead_line.setPen(QPen(QColor(231, 76, 60), 2))

        self._selection_rect = self._scene.addRect(0, 0, 0, 0)
        self._selection_rect.setBrush(QBrush(QColor(52, 152, 219, 60)))
        self._selection_rect.setPen(QPen(QColor(52, 152, 219), 1))

        self._pitch_items: list = []

        self._selection_start = -1.0
        self._selection_end = -1.0
        self._selecting = False
        self._select_start_x = 0.0
        self._dragging_zoom = False
        self._zoom_start_x = 0.0
        self._edge_drag: str | None = None  # "start" | "end" while adjusting
        self._EDGE_PX = 6  # grab width of a selection edge

        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.setMouseTracking(True)

    def set_data(
        self,
        times: np.ndarray,
        f0: np.ndarray,
        confidence: np.ndarray,
        fmin: float,
        fmax: float,
    ):
        self._times = times
        self._f0 = f0
        self._confidence = confidence
        self._fmin = fmin
        self._fmax = fmax
        self._duration = times[-1] if len(times) > 0 and times[-1] > 0 else len(f0) * (frame_dt(times))
        # Selection is stored in seconds, so it is meaningless across datasets.
        self._selection_start = -1.0
        self._selection_end = -1.0
        self._selection_rect.setRect(0, 0, 0, 0)
        self._view_offset = 0.0
        self._redraw()

    def set_title(self, title: str):
        self._title = title
        self._title_item.setPlainText(title)

    def set_zoom(self, zoom: float):
        self._zoom = zoom
        self._clamp_offset()
        self._redraw()

    def set_playhead(self, time_val: float):
        self._playhead_time = time_val
        self._update_playhead()

    def setDragAccept(self, enabled: bool):
        self.setAcceptDrops(enabled)

    def _view_rect(self) -> QRectF:
        vp = self.viewport()
        w = vp.width()
        h = vp.height()
        margin_left = 60
        margin_top = 30
        margin_right = 20
        margin_bottom = 30
        return QRectF(margin_left, margin_top, w - margin_left - margin_right, h - margin_top - margin_bottom)

    def _time_to_x(self, t: float) -> float:
        vr = self._view_rect()
        win = self._duration / self._zoom
        if win <= 0:
            return vr.left()
        return vr.left() + ((t - self._view_offset) / win) * vr.width()

    def _clamp_offset(self):
        if self._duration <= 0:
            self._view_offset = 0.0
            return
        win = self._duration / self._zoom
        if win >= self._duration - 1e-9:
            self._view_offset = 0.0
        else:
            self._view_offset = max(0.0, min(self._view_offset, self._duration - win))

    def ensure_visible(self, t: float) -> bool:
        """Pan the window so time t stays visible; redraws only if it moved."""
        if self._times is None or self._duration <= 0:
            return False
        win = self._duration / self._zoom
        if win >= self._duration - 1e-9:
            self._view_offset = 0.0
            return False
        margin = win * 0.08
        new_offset = self._view_offset
        if t < self._view_offset + margin:
            new_offset = t - margin
        elif t > self._view_offset + win - margin:
            new_offset = t - win + margin
        new_offset = max(0.0, min(new_offset, self._duration - win))
        if abs(new_offset - self._view_offset) < 1e-6:
            return False
        self._view_offset = new_offset
        self._redraw()
        return True

    def _freq_to_y(self, f: float) -> float:
        vr = self._view_rect()
        if self._fmax == self._fmin:
            return vr.center().y()
        ratio = (f - self._fmin) / (self._fmax - self._fmin)
        return vr.bottom() - ratio * vr.height()

    def _redraw(self):
        for item in self._scene.items():
            if item is not self._bg_rect and item is not self._title_item:
                self._scene.removeItem(item)
        self._pitch_items.clear()

        if self._times is None:
            return

        self._clamp_offset()
        vr = self._view_rect()
        self._bg_rect.setRect(vr)

        # The purge above also removed the selection rect; put it back so the
        # highlight stays visible after zoom / data changes.
        self._scene.addItem(self._selection_rect)

        win = self._duration / self._zoom
        if self._view_offset > 0:
            label_text = f"显示: {self._view_offset:.1f}s - {self._view_offset + win:.1f}s"
        else:
            label_text = f"显示范围: {win:.1f}s"
        self._time_label.setPlainText(label_text)
        self._time_label.setPos(vr.right() - 130, vr.bottom() + 5)
        self._scene.addItem(self._time_label)

        self._draw_grid(vr)
        self._draw_pitch_curve(vr)

        if self._selection_start >= 0 and self._selection_end >= 0:
            x1 = self._time_to_x(self._selection_start)
            x2 = self._time_to_x(self._selection_end)
            if abs(self._selection_end - self._selection_start) < 1e-6:
                # Single-click marker: render as a thin vertical line.
                self._selection_rect.setRect(x1 - 1, vr.top(), 2, vr.height())
            else:
                self._selection_rect.setRect(
                    min(x1, x2), vr.top(), abs(x2 - x1), vr.height()
                )

        self._update_playhead()

    def _draw_grid(self, vr: QRectF):
        step = 1.0
        if self._zoom >= 4:
            step = 0.1
        elif self._zoom >= 2:
            step = 0.5

        win = self._duration / self._zoom
        t0 = math.floor(self._view_offset / step) * step
        x = t0
        while x <= self._view_offset + win + step:
            px = self._time_to_x(x)
            if vr.left() <= px <= vr.right():
                line = self._scene.addLine(px, vr.top(), px, vr.bottom())
                line.setPen(QPen(QColor(220, 220, 220), 0.5))
                label = self._scene.addText(f"{x:.1f}s")
                label.setFont(QFont("Consolas", 7))
                label.setPos(px - 15, vr.bottom() + 2)
            x += step

        # Pitch grid: one line per semitone (A4 = 440 Hz), labeled with note
        # names. Label density adapts to the pixel spacing so labels never
        # overlap: every semitone → naturals only → C per octave → every
        # other octave's C.
        half_px = abs(
            self._freq_to_y(_midi_to_freq(_A4_MIDI + 1))
            - self._freq_to_y(_midi_to_freq(_A4_MIDI))
        )
        midi_lo = math.floor(_freq_to_midi(self._fmin))
        midi_hi = math.ceil(_freq_to_midi(self._fmax))

        for midi in range(midi_lo, midi_hi + 1):
            f = _midi_to_freq(midi)
            if f < self._fmin or f > self._fmax:
                continue
            py = self._freq_to_y(f)
            if not (vr.top() <= py <= vr.bottom()):
                continue
            line = self._scene.addLine(vr.left(), py, vr.right(), py)
            line.setPen(QPen(QColor(220, 220, 220), 0.5))

            name = _note_name(midi)
            if half_px >= 10:
                show = True
            elif half_px >= 5:
                show = "#" not in name  # naturals only
            elif half_px >= 2.5:
                show = midi % 12 == 0  # C per octave
            else:
                show = midi % 12 == 0 and (midi // 12) % 2 == 0  # C every other octave
            if show:
                label = self._scene.addText(name)
                label.setFont(QFont("Consolas", 7))
                label.setPos(vr.left() - 38, py - 7)

    @staticmethod
    def _polyline_item(points: list[QPointF], pen: QPen, clip_rect: QRectF) -> QGraphicsPathItem:
        """Open polyline clipped to clip_rect while painting.

        PySide6 does not expose QGraphicsItem.setClipRect, so clipping is done
        inside paint() — this keeps pan/zoom from drawing outside the plot.
        """
        path = QPainterPath()
        if points:
            path.moveTo(points[0])
            for p in points[1:]:
                path.lineTo(p)
        item = _ClippedPathItem(path, clip_rect)
        item.setPen(pen)
        item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        return item

    def _draw_pitch_curve(self, vr: QRectF):
        if self._f0 is None:
            return

        voiced_mask = ~np.isnan(self._f0) & (self._confidence > 0.05)
        unvoiced_mask = ~voiced_mask

        if voiced_mask.any():
            vx = self._time_to_x(self._times[voiced_mask])
            vy = self._freq_to_y(self._f0[voiced_mask])
            points = [QPointF(float(x), float(y)) for x, y in zip(vx, vy)]
            item = self._polyline_item(points, QPen(QColor(41, 128, 185), 1.5), vr)
            self._scene.addItem(item)
            self._pitch_items.append(item)

        if unvoiced_mask.any():
            # Hide the unvoiced dash line for long silent stretches (>= 1 s):
            # only short gaps stay marked so the baseline reads as "brief gaps",
            # not as a permanent floor line.
            idx_u = np.flatnonzero(unvoiced_mask)
            breaks = np.flatnonzero(np.diff(idx_u) > 1)
            runs = np.split(idx_u, breaks + 1)
            keep = np.concatenate(
                [r for r in runs if self._times[r[-1]] - self._times[r[0]] < SILENT_HIDE_S]
            ) if any(self._times[r[-1]] - self._times[r[0]] < SILENT_HIDE_S for r in runs) else np.array([], dtype=int)
            if keep.size:
                ux = self._time_to_x(self._times[keep])
                y_const = self._freq_to_y(self._fmin)
                points = [QPointF(float(x), float(y_const)) for x in ux]
                pen = QPen(QColor(189, 195, 199), 0.5)
                pen.setStyle(Qt.PenStyle.DashLine)
                item = self._polyline_item(points, pen, vr)
                self._scene.addItem(item)
                self._pitch_items.append(item)

    def _update_playhead(self):
        in_scene = self._playhead_line.scene() is self._scene
        if self._playhead_time < 0:
            if in_scene:
                self._scene.removeItem(self._playhead_line)
            return
        vr = self._view_rect()
        x = self._time_to_x(self._playhead_time)
        if vr.left() <= x <= vr.right():
            self._playhead_line.setLine(x, vr.top(), x, vr.bottom())
            if not in_scene:
                self._scene.addItem(self._playhead_line)
        elif in_scene:
            self._scene.removeItem(self._playhead_line)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._dragging_zoom = True
            self._zoom_start_x = event.position().x()
            self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
        elif event.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_hit_test(event.position().x())
            if edge:
                self._edge_drag = edge
                self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self._selecting = True
                self._select_start_x = event.position().x()
                self._selection_rect.setRect(0, 0, 0, 0)
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging_zoom:
            dx = event.position().x() - self._zoom_start_x
            scale = 1.0 + dx / 300.0
            scale = max(0.5, min(2.0, scale))
            steps = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]
            nearest = min(steps, key=lambda s: abs(s - self._zoom * scale))
            if self._parent is not None:
                self._parent._zoom_to_index(steps.index(nearest))
            return

        if self._edge_drag:
            t = self._time_at_px(event.position().x())
            if self._edge_drag == "start":
                self._selection_start = min(t, self._selection_end)
            else:
                self._selection_end = max(t, self._selection_start)
            self._redraw()
            return

        if self._selecting:
            vr = self._view_rect()
            x1 = self._select_start_x
            x2 = event.position().x()
            self._selection_rect.setRect(min(x1, x2), vr.top(), abs(x2 - x1), vr.height())
        else:
            # Resize cursor over draggable edges; pitch tooltip elsewhere.
            if self._edge_hit_test(event.position().x()):
                self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
            elif self.viewport().cursor().shape() == Qt.CursorShape.SizeHorCursor:
                self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self._hover_info(event)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging_zoom:
            self._dragging_zoom = False
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return

        if self._edge_drag:
            self._edge_drag = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self.selection_changed.emit(self._selection_start, self._selection_end)
            return

        if self._selecting and event.button() == Qt.MouseButton.LeftButton:
            self._selecting = False
            vr = self._view_rect()
            win = self._duration / self._zoom
            if win <= 0:
                return
            x1 = self._select_start_x
            x2 = event.position().x()
            frac1 = max(0.0, min(1.0, (x1 - vr.left()) / vr.width()))
            frac2 = max(0.0, min(1.0, (x2 - vr.left()) / vr.width()))
            t1 = min(self._duration, self._view_offset + frac1 * win)
            t2 = min(self._duration, self._view_offset + frac2 * win)
            if abs(x2 - x1) < 4:
                # Plain click (no drag): drop a single-point marker line.
                t = min(t1, t2)
                self._selection_start = self._selection_end = t
            else:
                self._selection_start = min(t1, t2)
                self._selection_end = max(t1, t2)
            self._redraw()
            self.selection_changed.emit(self._selection_start, self._selection_end)

        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]
            cur = steps.index(self._zoom) if self._zoom in steps else 2
            if event.angleDelta().y() > 0:
                cur = min(cur + 1, len(steps) - 1)
            else:
                cur = max(cur - 1, 0)
            if self._parent is not None:
                self._parent._zoom_to_index(cur)
            event.accept()
            return
        # Plain wheel pans horizontally when zoomed in.
        if self._times is not None and self._duration > 0:
            win = self._duration / self._zoom
            if win < self._duration - 1e-9:
                shift = win * 0.15
                if event.angleDelta().y() > 0:
                    self._view_offset -= shift
                else:
                    self._view_offset += shift
                self._clamp_offset()
                self._redraw()
                event.accept()
                return
        super().wheelEvent(event)

    def _show_context_menu(self, event):
        if self._selection_start < 0 or self._selection_end < 0:
            return
        menu = QMenu(self)
        if abs(self._selection_end - self._selection_start) < 1e-6:
            action = menu.addAction("从此处播放到结尾")
        else:
            action = menu.addAction("播放选区")
        action.triggered.connect(self._play_selection)
        if self._selection_end > self._selection_start:
            loop_action = menu.addAction("循环播放选区")
            loop_action.setCheckable(True)
            loop_action.setChecked(
                bool(self._parent is not None and getattr(self._parent, "_loop_enabled", False))
            )
            loop_action.triggered.connect(
                lambda checked: self._parent._toggle_loop(checked)
                if self._parent is not None else None
            )
        menu.exec(QCursor.pos())

    @Slot()
    def _play_selection(self):
        if self._parent is None or self._selection_start < 0:
            return
        if self._selection_end > self._selection_start:
            self._parent._start_playback(self._selection_start, self._selection_end)
        else:
            self._parent._start_playback(self._selection_start, None)

    def selection_start_time(self) -> float:
        return self._selection_start

    def selection_end_time(self) -> float:
        return self._selection_end

    def clear_selection(self):
        """Remove any selection or single-click marker."""
        if self._selection_start < 0 and self._selection_end < 0:
            return
        self._selection_start = -1.0
        self._selection_end = -1.0
        self._selection_rect.setRect(0, 0, 0, 0)
        self.selection_changed.emit(-1.0, -1.0)

    def nudge_selection(self, delta: float):
        """Shift the selection (or marker) by delta seconds, clamped to the clip."""
        if self._times is None or self._selection_start < 0:
            return
        d = self._duration
        if self._selection_end > self._selection_start:
            width = self._selection_end - self._selection_start
            new_start = max(0.0, min(d - width, self._selection_start + delta))
            self._selection_end = new_start + width
            self._selection_start = new_start
        else:
            self._selection_start = self._selection_end = max(
                0.0, min(d, self._selection_start + delta)
            )
        self._redraw()
        self.selection_changed.emit(self._selection_start, self._selection_end)

    def _edge_hit_test(self, px: float) -> str | None:
        """Return 'start'/'end' if px is on a draggable selection edge."""
        if self._selection_start < 0 or self._selection_end <= self._selection_start:
            return None  # single-click markers have no adjustable edges
        vr = self._view_rect()
        x1 = self._time_to_x(self._selection_start)
        x2 = self._time_to_x(self._selection_end)
        if abs(px - x1) <= self._EDGE_PX:
            return "start"
        if abs(px - x2) <= self._EDGE_PX:
            return "end"
        return None

    def _time_at_px(self, px: float) -> float:
        vr = self._view_rect()
        frac = max(0.0, min(1.0, (px - vr.left()) / vr.width()))
        return max(0.0, min(self._duration, self._view_offset + frac * (self._duration / self._zoom)))

    def _hover_info(self, event):
        """Show time / frequency / note name tooltip while idling over the plot."""
        if self._times is None or self._duration <= 0:
            return
        if self._selecting or self._dragging_zoom or self._edge_drag:
            return
        vr = self._view_rect()
        px = event.position().x()
        py = event.position().y()
        if not (vr.left() <= px <= vr.right() and vr.top() <= py <= vr.bottom()):
            return
        t = self._time_at_px(px)
        idx = int(np.searchsorted(self._times, t))
        idx = min(idx, len(self._times) - 1)
        f0 = float(self._f0[idx]) if self._f0 is not None and idx < len(self._f0) else float("nan")
        conf = float(self._confidence[idx]) if self._confidence is not None and idx < len(self._confidence) else 0.0
        if math.isfinite(f0) and conf > 0.05:
            midi = round(_freq_to_midi(f0))
            text = f"{t:.2f}s · {f0:.1f}Hz · {_note_name(midi)}"
        else:
            text = f"{t:.2f}s · 无声"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self._parent is not None:
            for url in event.mimeData().urls():
                self._parent._load_file(url.toLocalFile())


def frame_dt(times: np.ndarray) -> float:
    if len(times) < 2:
        return 0.01
    return float(np.median(np.diff(times)))

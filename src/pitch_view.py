from __future__ import annotations
import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QMenu, QGraphicsLineItem, QGraphicsPathItem, QToolTip,
)
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, Slot, QTimer
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
# Break the voiced pitch polyline when consecutive voiced samples are farther
# apart than this — multi-second silences must not get a diagonal bridge line.
VOICED_GAP_BREAK_S = 0.3
# Default visible time window when a clip is longer than this (seconds).
DEFAULT_VIEW_SECONDS = 20.0
# Preferred zoom ladder (matches main-window slider).
ZOOM_STEPS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)


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
        # Scene coordinates map 1:1 to viewport pixels; pan/zoom is custom.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._last_viewport_size = None

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
        self._pan_drag = False
        self._pan_grab_frac = 0.0  # click position within the scrub thumb (0-1)
        self._scrub_rect = QRectF()
        self._data_fmin = 50.0
        self._data_fmax = 500.0
        self._defer_pitch_range = False
        self._pitch_range_timer = QTimer(self)
        self._pitch_range_timer.setSingleShot(True)
        self._pitch_range_timer.setInterval(120)
        self._pitch_range_timer.timeout.connect(self._on_pitch_range_settled)

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
        self._data_fmin = fmin
        self._data_fmax = fmax
        self._fmin = fmin
        self._fmax = fmax
        self._duration = times[-1] if len(times) > 0 and times[-1] > 0 else len(f0) * (frame_dt(times))
        # Selection is stored in seconds, so it is meaningless across datasets.
        self._selection_start = -1.0
        self._selection_end = -1.0
        self._selection_rect.setRect(0, 0, 0, 0)
        self._view_offset = 0.0
        self._defer_pitch_range = False
        self._pitch_range_timer.stop()
        self._redraw()

    def default_zoom_index(self) -> int:
        """Zoom ladder index that shows a readable slice, not the full clip."""
        if self._duration <= DEFAULT_VIEW_SECONDS:
            return ZOOM_STEPS.index(1.0)
        needed = self._duration / DEFAULT_VIEW_SECONDS
        for i, z in enumerate(ZOOM_STEPS):
            if z >= needed - 1e-9:
                return i
        return len(ZOOM_STEPS) - 1

    def set_title(self, title: str):
        self._title = title
        self._title_item.setPlainText(title)

    def set_zoom(self, zoom: float):
        self._zoom = max(zoom, 0.05)
        self._clamp_offset()
        self._defer_pitch_range = False
        self._pitch_range_timer.stop()
        self._redraw()

    def set_zoom_to_range(self, t0: float, t1: float):
        """Fill the plot area with [t0, t1]; time axis follows the new window.

        Zoom becomes continuous (can exceed the 16× ladder max).
        """
        if self._duration <= 0:
            return
        t0 = max(0.0, min(float(t0), self._duration))
        t1 = max(0.0, min(float(t1), self._duration))
        if t1 < t0:
            t0, t1 = t1, t0
        span = t1 - t0
        if span < 1e-3:
            # Marker click: show a short window around the point.
            half = 0.5
            t0 = max(0.0, t0 - half)
            t1 = min(self._duration, t0 + 2 * half)
            span = max(t1 - t0, 0.05)
        pad = span * 0.02
        t0 = max(0.0, t0 - pad)
        t1 = min(self._duration, t0 + span + 2 * pad)
        span = max(t1 - t0, 0.05)
        self._view_offset = t0
        self._zoom = self._duration / span
        self._clamp_offset()
        self._defer_pitch_range = False
        self._pitch_range_timer.stop()
        self._redraw()
        if self._parent is not None and hasattr(self._parent, "_on_custom_zoom"):
            self._parent._on_custom_zoom(self._zoom)

    def zoom_factor(self) -> float:
        return self._zoom

    @Slot()
    def _zoom_to_selection(self):
        if self._selection_start < 0 or self._selection_end < 0:
            return
        self.set_zoom_to_range(self._selection_start, self._selection_end)

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
        # Extra room under the time labels for the pan scrubber.
        margin_bottom = 48
        return QRectF(margin_left, margin_top, max(w - margin_left - margin_right, 1), max(h - margin_top - margin_bottom, 1))

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
        self._schedule_pitch_range_refresh()
        self._redraw()
        return True

    def _visible_window(self) -> tuple[float, float]:
        win = self._duration / self._zoom if self._zoom > 0 else self._duration
        return self._view_offset, self._view_offset + win

    def _visible_pitch_range(self) -> tuple[float, float] | None:
        """Voiced F0 min/max inside the current time window, or None if silent/empty."""
        if self._times is None or self._f0 is None or self._confidence is None:
            return None
        if len(self._times) == 0:
            return None
        t0, t1 = self._visible_window()
        # Include a small edge sample so clipped notes at the boundary still count.
        mask = (
            (self._times >= t0 - 1e-9)
            & (self._times <= t1 + 1e-9)
            & ~np.isnan(self._f0)
            & (self._confidence > 0.05)
        )
        if not mask.any():
            return None
        vals = self._f0[mask]
        vals = vals[np.isfinite(vals) & (vals > 0)]
        if vals.size == 0:
            return None
        return float(np.min(vals)), float(np.max(vals))

    def _update_display_pitch_range(self):
        """Set Y-axis bounds from the visible voiced range (with padding)."""
        rng = self._visible_pitch_range()
        if rng is None:
            self._fmin = self._data_fmin
            self._fmax = self._data_fmax
            return
        lo, hi = rng
        midi_lo = _freq_to_midi(lo)
        midi_hi = _freq_to_midi(hi)
        if midi_hi - midi_lo < 0.25:
            center = (midi_lo + midi_hi) * 0.5
            midi_lo, midi_hi = center - 1.0, center + 1.0
        else:
            pad = max(0.5, (midi_hi - midi_lo) * 0.08)
            midi_lo -= pad
            midi_hi += pad
        self._fmin = max(_midi_to_freq(midi_lo), 20.0)
        self._fmax = max(_midi_to_freq(midi_hi), self._fmin * 1.05)

    def _schedule_pitch_range_refresh(self):
        """Debounce Y-axis refit until panning settles (keeps scrub drag cheap)."""
        if self._pan_drag:
            self._defer_pitch_range = True
            return
        self._pitch_range_timer.start()

    def _on_pitch_range_settled(self):
        self._defer_pitch_range = False
        if self._times is None:
            return
        before = (self._fmin, self._fmax)
        self._update_display_pitch_range()
        if abs(before[0] - self._fmin) > 1e-6 or abs(before[1] - self._fmax) > 1e-6:
            self._redraw()

    def _freq_to_y(self, f: float) -> float:
        vr = self._view_rect()
        if self._fmax == self._fmin:
            return vr.center().y()
        ratio = (f - self._fmin) / (self._fmax - self._fmin)
        return vr.bottom() - ratio * vr.height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        size = self.viewport().size()
        if size != self._last_viewport_size:
            self._last_viewport_size = size
            self._sync_scene_to_viewport()
            if self._times is not None:
                self._redraw()

    def _sync_scene_to_viewport(self):
        """Keep scene rect equal to the widget viewport so time/pitch map 1:1 to pixels."""
        vp = self.viewport()
        w = max(vp.width(), 1)
        h = max(vp.height(), 1)
        self.setSceneRect(0, 0, w, h)

    def _redraw(self):
        self._sync_scene_to_viewport()
        for item in self._scene.items():
            if item is not self._bg_rect and item is not self._title_item:
                self._scene.removeItem(item)
        self._pitch_items.clear()

        if self._times is None:
            return

        self._clamp_offset()
        vr = self._view_rect()
        self._bg_rect.setRect(vr)

        # Y-axis follows the visible voiced range, unless a scrub is mid-drag
        # (then keep the last settled bounds and refit after the user pauses).
        if not self._defer_pitch_range:
            self._update_display_pitch_range()

        # The purge above also removed the selection rect; put it back so the
        # highlight stays visible after zoom / data changes.
        self._scene.addItem(self._selection_rect)

        win = self._duration / self._zoom
        if win < self._duration - 1e-9:
            label_text = f"显示: {self._view_offset:.1f}s - {self._view_offset + win:.1f}s / {self._duration:.1f}s"
        else:
            label_text = f"显示范围: {win:.1f}s"
        self._time_label.setPlainText(label_text)
        self._time_label.setPos(vr.right() - 160, vr.bottom() + 5)
        self._scene.addItem(self._time_label)

        self._draw_grid(vr)
        self._draw_pitch_curve(vr)
        self._draw_scrubber(vr)

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

    def _time_tick_step(self, vr: QRectF) -> float:
        """Pick a nice time step so ticks keep ~70px spacing on screen."""
        win = self._duration / self._zoom
        width = max(vr.width(), 1.0)
        if win <= 0:
            return 1.0
        raw = win * 70.0 / width
        candidates = (
            0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5,
            1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
        )
        for s in candidates:
            if s >= raw:
                return float(s)
        return float(candidates[-1])

    @staticmethod
    def _format_time_label(t: float, step: float) -> str:
        if t >= 60 and step >= 1:
            minutes = int(t // 60)
            secs = t - minutes * 60
            return f"{minutes}:{secs:04.1f}"
        if step < 1:
            return f"{t:.2f}s"
        if step < 10:
            return f"{t:.1f}s"
        return f"{t:.0f}s"

    def _draw_grid(self, vr: QRectF):
        step = self._time_tick_step(vr)

        win = self._duration / self._zoom
        t_end = self._view_offset + win
        i0 = math.floor(self._view_offset / step + 1e-9)
        i1 = math.ceil(t_end / step - 1e-9)
        for i in range(i0, i1 + 1):
            x = i * step
            px = self._time_to_x(x)
            if vr.left() <= px <= vr.right():
                line = self._scene.addLine(px, vr.top(), px, vr.bottom())
                line.setPen(QPen(QColor(220, 220, 220), 0.5))
                label = self._scene.addText(self._format_time_label(x, step))
                label.setFont(QFont("Consolas", 7))
                label.setPos(px - 15, vr.bottom() + 2)

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

    def _draw_scrubber(self, vr: QRectF):
        """Horizontal overview bar: drag to pan when only part of the clip is shown."""
        if self._duration <= 0:
            self._scrub_rect = QRectF()
            return

        bar_h = 10.0
        bar_y = float(self.viewport().height()) - 14.0
        bar = QRectF(vr.left(), bar_y, vr.width(), bar_h)
        self._scrub_rect = bar

        track = self._scene.addRect(bar)
        track.setBrush(QBrush(QColor(236, 240, 241)))
        track.setPen(QPen(QColor(189, 195, 199), 0.5))

        win = self._duration / self._zoom
        if win >= self._duration - 1e-9:
            return  # full clip visible — no thumb needed

        thumb_w = max(24.0, bar.width() * (win / self._duration))
        thumb_x = bar.left() + (bar.width() - thumb_w) * (self._view_offset / max(self._duration - win, 1e-9))
        thumb = self._scene.addRect(QRectF(thumb_x, bar_y, thumb_w, bar_h))
        thumb.setBrush(QBrush(QColor(52, 152, 219, 180)))
        thumb.setPen(QPen(QColor(41, 128, 185), 1))

    def _draw_pitch_curve(self, vr: QRectF):
        if self._f0 is None:
            return

        voiced_mask = ~np.isnan(self._f0) & (self._confidence > 0.05)
        unvoiced_mask = ~voiced_mask

        if voiced_mask.any():
            v_idx = np.flatnonzero(voiced_mask)
            t_v = self._times[v_idx]
            if v_idx.size == 1:
                segs = [v_idx]
            else:
                breaks = np.flatnonzero(np.diff(t_v) > VOICED_GAP_BREAK_S)
                segs = np.split(v_idx, breaks + 1)
            for seg in segs:
                if seg.size < 2:
                    continue
                vx = self._time_to_x(self._times[seg])
                vy = self._freq_to_y(self._f0[seg])
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
        pos = event.position()
        if event.button() == Qt.MouseButton.LeftButton and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._dragging_zoom = True
            self._zoom_start_x = pos.x()
            self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
        elif event.button() == Qt.MouseButton.LeftButton and self._scrubber_hit(pos):
            self._pan_from_scrubber(pos, start_drag=True)
            event.accept()
            return
        elif event.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_hit_test(pos.x())
            if edge:
                self._edge_drag = edge
                self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self._selecting = True
                self._select_start_x = pos.x()
                self._selection_rect.setRect(0, 0, 0, 0)
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event)
        super().mousePressEvent(event)

    def _scrubber_hit(self, pos) -> bool:
        return self._scrub_rect.isValid() and self._scrub_rect.adjusted(0, -4, 0, 4).contains(pos)

    def _pan_from_scrubber(self, pos, start_drag: bool = False) -> None:
        """Map an x-position on the scrubber to view_offset; optional drag start."""
        if self._duration <= 0 or self._scrub_rect.width() <= 0:
            return
        win = self._duration / self._zoom
        if win >= self._duration - 1e-9:
            return
        bar = self._scrub_rect
        thumb_w = max(24.0, bar.width() * (win / self._duration))
        usable = max(bar.width() - thumb_w, 1.0)
        frac = max(0.0, min(1.0, (pos.x() - bar.left() - thumb_w * 0.5) / usable))
        if start_drag:
            # Keep the grab point inside the thumb so the bar doesn't jump.
            thumb_x = bar.left() + usable * (self._view_offset / (self._duration - win))
            local = (pos.x() - thumb_x) / thumb_w
            self._pan_grab_frac = max(0.0, min(1.0, local))
            self._pan_drag = True
            frac = max(0.0, min(1.0, (pos.x() - bar.left() - thumb_w * self._pan_grab_frac) / usable))
        self._view_offset = frac * (self._duration - win)
        self._clamp_offset()
        self._schedule_pitch_range_refresh()
        self._redraw()

    def mouseMoveEvent(self, event):
        if self._pan_drag:
            self._pan_from_scrubber(event.position())
            return
        if self._dragging_zoom:
            dx = event.position().x() - self._zoom_start_x
            scale = 1.0 + dx / 300.0
            scale = max(0.5, min(2.0, scale))
            nearest = min(ZOOM_STEPS, key=lambda s: abs(s - self._zoom * scale))
            if self._parent is not None:
                self._parent._zoom_to_index(ZOOM_STEPS.index(nearest))
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
        if self._pan_drag:
            self._pan_drag = False
            # Refit Y-axis once the scrub settles.
            self._schedule_pitch_range_refresh()
            return

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
            steps = list(ZOOM_STEPS)
            cur = min(range(len(steps)), key=lambda i: abs(steps[i] - self._zoom))
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
                self._schedule_pitch_range_refresh()
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
            zoom_action = menu.addAction("缩放到选区")
            zoom_action.triggered.connect(self._zoom_to_selection)
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

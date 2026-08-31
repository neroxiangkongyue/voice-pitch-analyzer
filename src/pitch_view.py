from __future__ import annotations
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QMenu, QGraphicsLineItem, QGraphicsPolygonItem,
)
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, Slot
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QFont, QCursor, QPolygonF

pg.setConfigOption("background", "w")
pg.setConfigOption("foreground", "k")


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
        self._redraw()

    def set_title(self, title: str):
        self._title = title
        self._title_item.setPlainText(title)

    def set_zoom(self, zoom: float):
        self._zoom = zoom
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
        duration = self._duration / self._zoom
        if duration <= 0:
            return vr.left()
        return vr.left() + (t / duration) * vr.width()

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

        vr = self._view_rect()
        self._bg_rect.setRect(vr)

        # The purge above also removed the selection rect; put it back so the
        # highlight stays visible after zoom / data changes.
        self._scene.addItem(self._selection_rect)

        duration_display = self._duration / self._zoom
        self._time_label.setPlainText(f"显示范围: {duration_display:.1f}s")
        self._time_label.setPos(vr.right() - 100, vr.bottom() + 5)
        self._scene.addItem(self._time_label)

        self._draw_grid(vr)
        self._draw_pitch_curve(vr)

        if self._selection_start >= 0 and self._selection_end >= 0:
            x1 = self._time_to_x(self._selection_start)
            x2 = self._time_to_x(self._selection_end)
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

        duration_display = self._duration / self._zoom
        x = 0.0
        while x <= duration_display + step:
            px = self._time_to_x(x)
            if vr.left() <= px <= vr.right():
                line = self._scene.addLine(px, vr.top(), px, vr.bottom())
                line.setPen(QPen(QColor(220, 220, 220), 0.5))
                label = self._scene.addText(f"{x:.1f}s")
                label.setFont(QFont("Consolas", 7))
                label.setPos(px - 15, vr.bottom() + 2)
            x += step

        freq_step = 50
        if self._fmax - self._fmin < 200:
            freq_step = 25
        elif self._fmax - self._fmin > 1000:
            freq_step = 100

        f = self._fmin
        while f <= self._fmax + freq_step:
            py = self._freq_to_y(f)
            if vr.top() <= py <= vr.bottom():
                line = self._scene.addLine(vr.left(), py, vr.right(), py)
                line.setPen(QPen(QColor(220, 220, 220), 0.5))
                label = self._scene.addText(f"{int(f)}Hz")
                label.setFont(QFont("Consolas", 7))
                label.setPos(vr.left() - 45, py - 7)
            f += freq_step

    def _draw_pitch_curve(self, vr: QRectF):
        if self._f0 is None:
            return

        voiced_mask = ~np.isnan(self._f0) & (self._confidence > 0.05)
        unvoiced_mask = ~voiced_mask

        if voiced_mask.any():
            vx = self._time_to_x(self._times[voiced_mask])
            vy = self._freq_to_y(self._f0[voiced_mask])
            points = [QPointF(float(x), float(y)) for x, y in zip(vx, vy)]
            poly = QPolygonF(points)
            item = QGraphicsPolygonItem(poly)
            item.setPen(QPen(QColor(41, 128, 185), 1.5))
            item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            self._scene.addItem(item)
            self._pitch_items.append(item)

        if unvoiced_mask.any():
            ux = self._time_to_x(self._times[unvoiced_mask])
            y_const = self._freq_to_y(self._fmin)
            points = [QPointF(float(x), float(y_const)) for x in ux]
            poly = QPolygonF(points)
            item = QGraphicsPolygonItem(poly)
            pen = QPen(QColor(189, 195, 199), 0.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            item.setPen(pen)
            item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
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

        if self._selecting:
            vr = self._view_rect()
            x1 = self._select_start_x
            x2 = event.position().x()
            self._selection_rect.setRect(min(x1, x2), vr.top(), abs(x2 - x1), vr.height())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging_zoom:
            self._dragging_zoom = False
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return

        if self._selecting and event.button() == Qt.MouseButton.LeftButton:
            self._selecting = False
            vr = self._view_rect()
            duration = self._duration / self._zoom
            if duration <= 0:
                return
            x1 = self._select_start_x
            x2 = event.position().x()
            t1 = max(0, min(1, (x1 - vr.left()) / vr.width())) * duration
            t2 = max(0, min(1, (x2 - vr.left()) / vr.width())) * duration
            self._selection_start = min(t1, t2)
            self._selection_end = max(t1, t2)
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
        super().wheelEvent(event)

    def _show_context_menu(self, event):
        if self._selection_start < 0 or self._selection_end < 0:
            return
        menu = QMenu(self)
        action = menu.addAction("播放选区")
        action.triggered.connect(self._play_selection)
        menu.exec(QCursor.pos())

    @Slot()
    def _play_selection(self):
        if self._parent is not None and self._selection_start >= 0 and self._selection_end >= 0:
            self._parent._start_playback(self._selection_start, self._selection_end)

    def selection_start_time(self) -> float:
        return self._selection_start

    def selection_end_time(self) -> float:
        return self._selection_end

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

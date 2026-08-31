from __future__ import annotations
import sys
from dataclasses import dataclass

import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtCore import Qt, QTimer, Slot, QSize
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QSlider, QGroupBox, QStyleFactory,
    QMessageBox, QListWidget, QListWidgetItem, QSizePolicy,
    QToolBar, QSplitter,
)

from src.audio_loader import load_audio
from src.pitch_detector import extract_pitch, detect_frequency_range
from src.audio_recorder import AudioRecorder
from src.audio_player import AudioPlayer
from src.pitch_view import PitchView


SUPPORTED_EXTS = "Audio Files (*.wav *.mp3 *.flac *.m4a *.aac *.ogg);;All Files (*)"


@dataclass
class AudioDocument:
    """One loaded audio clip plus its cached analysis results."""

    title: str
    audio: np.ndarray
    sr: int
    times: np.ndarray
    f0: np.ndarray
    confidence: np.ndarray
    fmin: float
    fmax: float
    path: str = ""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Pitch Analyzer")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(1280, 720)

        self._doc: AudioDocument | None = None
        self._docs: list[AudioDocument] = []
        self._placeholder_active = True
        self._recording_counter = 0

        self._recorder = AudioRecorder()
        self._player = AudioPlayer()
        self._play_timer = QTimer()
        self._play_timer.timeout.connect(self._on_play_tick)

        self._current_zoom = 1.0
        self._zoom_steps = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]

        self._build_ui()

    # ── UI Build ──────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._build_toolbar(layout)
        self._build_pitch_view(layout)
        self._build_controls(layout)
        self._build_statusbar()

        self._setup_shortcuts()

        self._file_list.addItem(QListWidgetItem("— 拖拽或导入音频文件 —"))

    def _build_toolbar(self, layout: QVBoxLayout):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))

        open_action = QAction("打开文件", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._on_open_file)
        toolbar.addAction(open_action)

        toolbar.addSeparator()

        self._record_btn = QPushButton("录音")
        self._record_btn.setCheckable(True)
        self._record_btn.setFixedHeight(28)
        self._record_btn.clicked.connect(self._on_toggle_record)
        toolbar.addWidget(self._record_btn)

        toolbar.addSeparator()

        self._play_btn = QPushButton("播放")
        self._play_btn.setFixedHeight(28)
        self._play_btn.clicked.connect(self._on_toggle_play)
        toolbar.addWidget(self._play_btn)

        toolbar.addSeparator()

        self._zoom_out_btn = QPushButton("放大")
        self._zoom_out_btn.setFixedHeight(28)
        self._zoom_out_btn.clicked.connect(self._zoom_in)
        toolbar.addWidget(self._zoom_out_btn)

        self._zoom_in_btn = QPushButton("缩小")
        self._zoom_in_btn.setFixedHeight(28)
        self._zoom_in_btn.clicked.connect(self._zoom_out)
        toolbar.addWidget(self._zoom_in_btn)

        self._zoom_label = QLabel("1x")
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setFixedWidth(40)
        toolbar.addWidget(self._zoom_label)

        layout.addWidget(toolbar)

    def _build_pitch_view(self, layout: QVBoxLayout):
        splitter = QSplitter(Qt.Orientation.Horizontal)

        file_panel = QWidget()
        file_layout = QVBoxLayout(file_panel)
        file_layout.setContentsMargins(4, 4, 4, 4)
        file_label = QLabel("音频文件列表")
        file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        file_layout.addWidget(file_label)

        self._file_list = QListWidget()
        self._file_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._file_list.itemClicked.connect(self._on_file_selected)
        file_layout.addWidget(self._file_list)

        add_btn = QPushButton("添加...")
        add_btn.clicked.connect(self._on_open_file)
        file_layout.addWidget(add_btn)

        splitter.addWidget(file_panel)
        splitter.addWidget(self._pitch_view_instance())
        splitter.setSizes([200, 1000])
        layout.addWidget(splitter, stretch=1)

    def _pitch_view_instance(self) -> PitchView:
        pv = PitchView(self)
        self._pv = pv
        self._pv.selection_changed.connect(self._on_selection_changed)
        self._pv.setDragAccept(True)
        return pv

    def _build_controls(self, layout: QVBoxLayout):
        ctrl = QGroupBox("控制")
        ctrl.setFixedHeight(70)
        ctrl_layout = QHBoxLayout(ctrl)

        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(0, len(self._zoom_steps) - 1)
        self._zoom_slider.setValue(2)
        self._zoom_slider.valueChanged.connect(self._on_zoom_slider)
        ctrl_layout.addWidget(QLabel("缩放:"))
        ctrl_layout.addWidget(self._zoom_slider)
        ctrl_layout.addStretch()

        self._playback_pos = QLabel("0.000s")
        self._playback_pos.setFixedWidth(80)
        self._playback_pos.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ctrl_layout.addWidget(self._playback_pos)

        layout.addWidget(ctrl)

    def _build_statusbar(self):
        self.statusBar().showMessage("就绪")

    def _setup_shortcuts(self):
        pass

    # ── File Loading ──────────────────────────────────────────────

    @Slot()
    def _on_open_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择音频文件", "", SUPPORTED_EXTS)
        for p in paths:
            self._load_file(p)

    def _load_file(self, path: str):
        # Reload of a known path just switches to its cached document.
        for doc in self._docs:
            if doc.path == path:
                self._activate_document(doc)
                return
        self.statusBar().showMessage(f"正在加载: {path}")
        QApplication.processEvents()
        try:
            audio, sr = load_audio(path)
            times, f0, confidence = extract_pitch(audio, sr)
            fmin, fmax = detect_frequency_range(f0, len(audio) / sr)
            self._add_document(AudioDocument(
                title=path, path=path, audio=audio, sr=sr,
                times=times, f0=f0, confidence=confidence,
                fmin=fmin, fmax=fmax,
            ))
        except Exception as e:
            QMessageBox.critical(self, "加载失败", str(e))
            self.statusBar().showMessage("加载失败")

    def _add_document(self, doc: AudioDocument):
        if self._placeholder_active:
            self._file_list.clear()
            self._placeholder_active = False
        self._docs.append(doc)
        item = QListWidgetItem(doc.title)
        item.setData(Qt.ItemDataRole.UserRole, doc)
        self._file_list.addItem(item)
        self._activate_document(doc)

    def _activate_document(self, doc: AudioDocument):
        self._stop_playback()
        self._doc = doc
        self._pv.set_data(doc.times, doc.f0, doc.confidence, doc.fmin, doc.fmax)
        self._pv.set_title(doc.title)
        self._zoom_to_index(2)
        duration = len(doc.audio) / doc.sr
        self.statusBar().showMessage(f"已加载: {doc.title}  |  时长: {duration:.2f}s  |  采样率: {doc.sr}")

    @Slot(QListWidgetItem)
    def _on_file_selected(self, item: QListWidgetItem):
        doc = item.data(Qt.ItemDataRole.UserRole)
        if doc is not None and doc is not self._doc:
            self._activate_document(doc)

    # ── Recording ─────────────────────────────────────────────────

    @Slot()
    def _on_toggle_record(self):
        if not self._recorder.is_recording:
            try:
                dev = self._recorder.get_default_device()
                self._recorder.start()
                self._record_btn.setText("停止录音")
                self.statusBar().showMessage(f"录音中... 设备: {dev}")
            except Exception as e:
                QMessageBox.critical(self, "录音失败", f"无法启动录音: {e}")
        else:
            audio, sr = self._recorder.stop()
            self._record_btn.setText("录音")
            if len(audio) == 0:
                self.statusBar().showMessage("录音为空")
                return
            try:
                times, f0, confidence = extract_pitch(audio, sr)
                fmin, fmax = detect_frequency_range(f0, len(audio) / sr)
            except Exception as e:
                QMessageBox.critical(self, "分析失败", str(e))
                return
            self._recording_counter += 1
            self._add_document(AudioDocument(
                title=f"[录音 {self._recording_counter}]", audio=audio, sr=sr,
                times=times, f0=f0, confidence=confidence, fmin=fmin, fmax=fmax,
            ))
            self.statusBar().showMessage(f"录音完成: {len(audio) / sr:.2f}s")

    # ── Playback ──────────────────────────────────────────────────

    @Slot()
    def _on_toggle_play(self):
        if self._player.is_playing:
            self._stop_playback()
        elif self._doc is not None:
            self._start_playback(
                self._pv.selection_start_time(),
                self._pv.selection_end_time(),
            )

    def _start_playback(self, start: float, end: float | None = None):
        """Play [start, end) of the active document; end<=0 plays everything."""
        if self._doc is None:
            return
        self._player.play(
            self._doc.audio, self._doc.sr,
            start=max(0.0, start),
            end=end if end and end > 0 else None,
        )
        self._play_timer.start(30)
        self._play_btn.setText("停止")

    def _stop_playback(self):
        self._player.stop()
        self._reset_play_ui()

    def _reset_play_ui(self):
        self._play_timer.stop()
        self._play_btn.setText("播放")
        self._pv.set_playhead(-1)
        self._playback_pos.setText("0.000s")

    @Slot()
    def _on_play_tick(self):
        # UI-thread poll: refresh the playhead; reset UI once playback ends.
        if self._player.is_playing:
            t = self._player.current_time
            self._pv.set_playhead(t)
            self._playback_pos.setText(f"{t:.3f}s")
        else:
            self._reset_play_ui()

    # ── Zoom ──────────────────────────────────────────────────────

    def _zoom_to_index(self, idx: int):
        idx = max(0, min(idx, len(self._zoom_steps) - 1))
        self._current_zoom = self._zoom_steps[idx]
        self._zoom_slider.setValue(idx)
        self._zoom_label.setText(f"{self._current_zoom}x")
        if hasattr(self, "_pv"):
            self._pv.set_zoom(self._current_zoom)

    def _zoom_in(self):
        idx = self._zoom_slider.value()
        self._zoom_to_index(idx + 1)

    def _zoom_out(self):
        idx = self._zoom_slider.value()
        self._zoom_to_index(idx - 1)

    def _on_zoom_slider(self, val: int):
        self._zoom_to_index(val)

    def _on_selection_changed(self, start_time, end_time):
        pass

    # ── Drag & Drop ───────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            self._load_file(path)


def main():
    app = QApplication(sys.argv)
    try:
        app.setStyle(QStyleFactory.create("Fusion"))
    except Exception:
        pass

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

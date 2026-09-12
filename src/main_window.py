from __future__ import annotations
import os
import sys
import threading
from dataclasses import dataclass

import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtCore import Qt, QTimer, Slot, QSize, QThread, QObject, Signal
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QSlider, QGroupBox, QStyleFactory,
    QMessageBox, QListWidget, QListWidgetItem, QSizePolicy,
    QToolBar, QSplitter, QProgressDialog,
)

from src.audio_loader import load_audio
from src.pitch_detector import extract_pitch, extract_pitch_cached, detect_frequency_range
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


class _LoadWorker(QObject):
    """Loads audio and runs pitch analysis off the UI thread.

    Emits progress / loaded / failed per file and finished when the whole
    batch is done. Lives in a QThread owned by MainWindow.
    """

    progress = Signal(int, int, str)  # (current_index, total, display_name)
    loaded = Signal(object)           # AudioDocument
    failed = Signal(str, str)         # (path, error_message)
    finished = Signal()

    def __init__(self, paths: list[str]):
        super().__init__()
        self._paths = paths

    @Slot()
    def run(self):
        total = len(self._paths)
        for i, path in enumerate(self._paths, start=1):
            if QThread.currentThread().isInterruptionRequested():
                break
            self.progress.emit(i, total, os.path.basename(path))
            try:
                audio, sr = load_audio(path)
                times, f0, confidence = extract_pitch_cached(path, audio, sr)
                fmin, fmax = detect_frequency_range(f0, len(audio) / sr)
                self.loaded.emit(AudioDocument(
                    title=path, path=path,
                    audio=audio, sr=sr,
                    times=times, f0=f0, confidence=confidence,
                    fmin=fmin, fmax=fmax,
                ))
            except Exception as e:
                self.failed.emit(path, str(e))
        self.finished.emit()


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
        self._record_anchor = 0.0  # timeline offset where the next recording lands

        self._recorder = AudioRecorder()
        self._player = AudioPlayer()
        self._play_timer = QTimer()
        self._play_timer.timeout.connect(self._on_play_tick)

        # Async loading state.
        self._loading = False
        self._pending_paths: list[str] = []
        self._load_thread: QThread | None = None
        self._load_worker: _LoadWorker | None = None
        self._load_dialog: QProgressDialog | None = None

        self._current_zoom = 1.0
        self._zoom_steps = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]
        self._loop_enabled = False

        self._build_ui()

        # Warm up the pitch-analysis pipeline (librosa/numba JIT) in the
        # background so opening the first file is not slowed down by
        # one-time compilation costs.
        self._pitch_engine_warm = False
        QTimer.singleShot(300, self._warmup_pitch_engine)

    def _warmup_pitch_engine(self):
        """Pre-compile librosa/numba kernels once, off the UI thread."""
        if self._pitch_engine_warm:
            return
        self._pitch_engine_warm = True
        self.statusBar().showMessage("正在预热分析引擎（首次启动需要几秒）...")
        self._warmup_thread = threading.Thread(
            target=self._run_pitch_warmup, daemon=True, name="pitch-warmup"
        )
        self._warmup_thread.start()
        QTimer.singleShot(100, self._check_warmup_done)

    def _run_pitch_warmup(self):
        try:
            silence = np.zeros(16000, dtype=np.float32)  # 1 s @ 16 kHz
            extract_pitch(silence, 16000)
        except Exception:
            pass  # Warmup is best-effort only.

    def _check_warmup_done(self):
        t = getattr(self, "_warmup_thread", None)
        if t is not None and t.is_alive():
            QTimer.singleShot(100, self._check_warmup_done)
            return
        self.statusBar().showMessage("分析引擎已就绪", 3000)

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

        self._clear_overlay_btn = QPushButton("清空录音曲线")
        self._clear_overlay_btn.setFixedHeight(28)
        self._clear_overlay_btn.clicked.connect(self._on_clear_overlays)
        toolbar.addWidget(self._clear_overlay_btn)

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
        self._zoom_label.setMinimumWidth(56)
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
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._on_toggle_play)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._on_clear_selection)
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self._pv.nudge_selection(-0.01))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self._pv.nudge_selection(0.01))
        QShortcut(QKeySequence(Qt.Key_L), self, activated=lambda: self._toggle_loop())

    @Slot()
    def _on_clear_selection(self):
        self._pv.clear_selection()

    def _toggle_loop(self, checked: bool | None = None):
        """Toggle loop playback of the current selection (L key / context menu)."""
        if checked is None:
            checked = not self._loop_enabled
        self._loop_enabled = checked
        self.statusBar().showMessage(
            "循环播放: 开" if checked else "循环播放: 关", 2000
        )
        if checked and not self._player.is_playing:
            s = self._pv.selection_start_time()
            e = self._pv.selection_end_time()
            if s >= 0 and e > s:
                self._start_playback(s, e)

    # ── File Loading ──────────────────────────────────────────────

    @Slot()
    def _on_open_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择音频文件", "", SUPPORTED_EXTS)
        if paths:
            self._load_files(paths)

    def _load_files(self, paths: list[str]):
        """Load a batch of paths; already-cached ones are just activated."""
        new_paths: list[str] = []
        for p in paths:
            cached = next((d for d in self._docs if d.path == p), None)
            if cached is not None:
                self._activate_document(cached)
            else:
                new_paths.append(p)
        if not new_paths:
            return
        if self._loading:
            self._pending_paths.extend(new_paths)
        else:
            self._start_load_worker(new_paths)

    def _load_file(self, path: str):
        self._load_files([path])

    def _start_load_worker(self, paths: list[str]):
        self._loading = True
        self._load_thread = QThread(self)
        self._load_worker = _LoadWorker(paths)
        self._load_worker.moveToThread(self._load_thread)
        self._load_thread.started.connect(self._load_worker.run)
        self._load_worker.progress.connect(self._on_load_progress)
        self._load_worker.loaded.connect(self._add_document)
        self._load_worker.failed.connect(self._on_load_failed)
        self._load_worker.finished.connect(self._on_load_finished)
        self._load_thread.finished.connect(self._on_load_thread_finished)

        total = len(paths)
        dialog = QProgressDialog(self)
        dialog.setWindowTitle("加载音频")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setCancelButtonText("取消")
        dialog.setMinimumDuration(0)  # appear immediately, no 4s hidden delay
        if total == 1:
            # Indeterminate busy bar while a single file is analyzed.
            dialog.setRange(0, 0)
            dialog.setLabelText("正在加载音频并分析音高...")
        else:
            dialog.setRange(0, total)
            dialog.setValue(0)
            dialog.setLabelText("准备加载...")
        dialog.canceled.connect(self._on_load_canceled)
        dialog.show()
        self._load_dialog = dialog

        self.statusBar().showMessage(f"正在加载 {total} 个文件...")
        self._load_thread.start()

    @Slot(int, int, str)
    def _on_load_progress(self, idx: int, total: int, name: str):
        if self._load_dialog is None:
            return
        if total > 1:
            self._load_dialog.setValue(idx)
            self._load_dialog.setLabelText(f"正在加载 ({idx}/{total}): {name}")
        else:
            self._load_dialog.setLabelText(f"正在加载并分析音高: {name}")

    @Slot()
    def _on_load_canceled(self):
        if self._load_dialog is not None:
            self._load_dialog.setLabelText("正在取消...")
        if self._load_thread is not None and self._load_thread.isRunning():
            self._load_thread.requestInterruption()
        self.statusBar().showMessage("已取消加载")

    @Slot(str, str)
    def _on_load_failed(self, path: str, msg: str):
        QMessageBox.critical(self, "加载失败", f"{os.path.basename(path)}\n{msg}")

    @Slot()
    def _on_load_finished(self):
        # Worker finished the batch (or was interrupted); stop the thread.
        if self._load_dialog is not None:
            self._load_dialog.close()
            self._load_dialog = None
        if self._load_thread is not None:
            self._load_thread.quit()

    @Slot()
    def _on_load_thread_finished(self):
        thread = self._load_thread
        self._load_thread = None
        self._load_worker = None  # dropped ref -> worker is GC'd (no parent)
        if thread is not None:
            thread.deleteLater()
        if self._pending_paths:
            paths, self._pending_paths = self._pending_paths, []
            self._start_load_worker(paths)
        else:
            self._loading = False
            self.statusBar().showMessage("加载完成")

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
        # set_data clears overlays — switching files drops previous takes.
        self._pv.set_data(doc.times, doc.f0, doc.confidence, doc.fmin, doc.fmax)
        self._pv.set_title(doc.title)
        # Default to a readable partial window; full clip is still one zoom-out away.
        self._zoom_to_index(self._pv.default_zoom_index())
        # Skip leading silence once the window size is known.
        self._pv.focus_first_voiced()
        duration = len(doc.audio) / doc.sr
        self.statusBar().showMessage(f"已加载: {doc.title}  |  时长: {duration:.2f}s  |  采样率: {doc.sr}")

    @Slot(QListWidgetItem)
    def _on_file_selected(self, item: QListWidgetItem):
        doc = item.data(Qt.ItemDataRole.UserRole)
        if doc is not None and doc is not self._doc:
            self._activate_document(doc)

    # ── Recording ─────────────────────────────────────────────────

    def _marker_time(self) -> float:
        """Timeline position of the single-click marker / selection start, or 0."""
        if self._pv is None:
            return 0.0
        start = self._pv.selection_start_time()
        if start is None or start < 0:
            return 0.0
        return float(start)

    @Slot()
    def _on_toggle_record(self):
        if not self._recorder.is_recording:
            # Overlay recordings start at the clicked marker on the current curve.
            self._record_anchor = self._marker_time() if self._doc is not None else 0.0
            try:
                dev = self._recorder.get_default_device()
                self._recorder.start()
                self._record_btn.setText("停止录音")
                anchor_txt = f"{self._record_anchor:.2f}s" if self._doc is not None else "新文档"
                self.statusBar().showMessage(f"录音中... 设备: {dev} | 落点: {anchor_txt}")
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
            except Exception as e:
                QMessageBox.critical(self, "分析失败", str(e))
                return

            # With a base clip loaded: draw the take as a yellow overlay at the marker.
            if self._doc is not None:
                times = np.asarray(times, dtype=float) + self._record_anchor
                self._pv.add_overlay(times, f0, confidence)
                self._recording_counter += 1
                self.statusBar().showMessage(
                    f"录音已叠加 (黄) @ {self._record_anchor:.2f}s，时长 {len(audio) / sr:.2f}s"
                    f" | 叠加 #{self._recording_counter}"
                )
                return

            # No base document yet: keep the old standalone-recording behaviour.
            fmin, fmax = detect_frequency_range(f0, len(audio) / sr)
            self._recording_counter += 1
            self._add_document(AudioDocument(
                title=f"[录音 {self._recording_counter}]", audio=audio, sr=sr,
                times=times, f0=f0, confidence=confidence, fmin=fmin, fmax=fmax,
            ))
            self.statusBar().showMessage(f"录音完成: {len(audio) / sr:.2f}s")

    @Slot()
    def _on_clear_overlays(self):
        n = self._pv.overlay_count() if hasattr(self, "_pv") else 0
        if n <= 0:
            self.statusBar().showMessage("没有可清除的录音曲线")
            return
        self._pv.clear_overlays()
        self._recording_counter = 0
        self.statusBar().showMessage(f"已清空 {n} 条录音曲线")

    # ── Playback ──────────────────────────────────────────────────

    @Slot()
    def _on_toggle_play(self):
        if self._player.is_playing:
            self._stop_playback()
        elif self._doc is not None:
            start = self._pv.selection_start_time()
            end = self._pv.selection_end_time()
            # Single-point marker (or no selection): play through to the end.
            if end <= start:
                end = None
            self._start_playback(start, end)

    def _start_playback(self, start: float, end: float | None = None):
        """Play [start, end) of the active document; end<=0 plays everything."""
        if self._doc is None:
            return
        try:
            started = self._player.play(
                self._doc.audio, self._doc.sr,
                start=max(0.0, start),
                end=end if end and end > 0 else None,
                loop=self._loop_enabled,
            )
        except Exception as e:
            self._reset_play_ui()
            QMessageBox.critical(self, "播放失败", f"无法播放音频:\n{e}")
            return
        if not started:
            # Nothing to play (e.g. selection outside the clip); stay idle.
            self._reset_play_ui()
            return
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
            # Follow the playhead so it never leaves the visible window.
            self._pv.ensure_visible(t)
            self._playback_pos.setText(f"{t:.3f}s")
        else:
            self._reset_play_ui()

    # ── Zoom ──────────────────────────────────────────────────────

    def _zoom_to_index(self, idx: int):
        idx = max(0, min(idx, len(self._zoom_steps) - 1))
        self._current_zoom = self._zoom_steps[idx]
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(idx)
        self._zoom_slider.blockSignals(False)
        self._zoom_label.setText(f"{self._current_zoom}x")
        if hasattr(self, "_pv"):
            self._pv.set_zoom(self._current_zoom)

    def _on_custom_zoom(self, zoom: float):
        """Continuous zoom (e.g. zoom-to-selection); sync label/slider without re-applying."""
        self._current_zoom = zoom
        self._zoom_label.setText(f"{zoom:.1f}x")
        idx = min(
            range(len(self._zoom_steps)),
            key=lambda i: abs(self._zoom_steps[i] - zoom),
        )
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(idx)
        self._zoom_slider.blockSignals(False)

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

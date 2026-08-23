import threading
import time
import numpy as np
import sounddevice as sd


class AudioRecorder:
    def __init__(self, sr: int = 16000):
        self.sr = sr
        self._buffer: list[np.ndarray] = []
        self._stream = None
        self._running = False

    @property
    def is_recording(self) -> bool:
        return self._running

    def start(self) -> None:
        if self.is_recording:
            return
        self._buffer = []
        self._running = True

        def callback(indata, frames, time_info, status):
            if self._running:
                self._buffer.append(indata.copy())
                if status:
                    print(f"[Recorder] {status}")

        self._stream = sd.InputStream(
            channels=1,
            samplerate=self.sr,
            blocksize=4096,
            callback=callback,
        )
        self._stream.start()

    def stop(self) -> tuple[np.ndarray, int]:
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._buffer:
            return np.array([]), self.sr
        data = np.concatenate(self._buffer, axis=0)
        if data.ndim > 1:
            data = data.mean(axis=1)
        return data.astype(np.float32), self.sr

    @staticmethod
    def get_default_device() -> str:
        devices = sd.query_devices()
        default_idx = sd.default.device[0]
        for dev in devices:
            if dev["index"] == default_idx:
                return dev["name"]
        return "default"

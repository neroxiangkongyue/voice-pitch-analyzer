import threading
import numpy as np
import sounddevice as sd
import time as _time


class AudioPlayer:
    def __init__(self):
        self._player_instance = None
        self._playing = False

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(
        self,
        audio: np.ndarray,
        sr: int,
        start: float = 0.0,
        end: float | None = None,
        progress_callback=None,
    ) -> None:
        self.stop()
        start_idx = int(start * sr)
        end_idx = len(audio) if end is None else int(end * sr)
        segment = audio[start_idx:end_idx]
        if len(segment) == 0:
            return

        actual_start = start
        self._playing = True
        self._player_instance = sd.Player(data=segment, samplerate=sr)
        self._player_instance.start()

        t0 = _time.time()
        duration = len(segment) / sr
        while self._playing:
            elapsed = _time.time() - t0
            if elapsed >= duration:
                break
            actual_time = actual_start + elapsed
            if progress_callback:
                progress_callback(actual_time)
            _time.sleep(0.02)

        self._playing = False
        try:
            self._player_instance.stop()
            self._player_instance.close()
        except Exception:
            pass
        self._player_instance = None

    def stop(self):
        self._playing = False

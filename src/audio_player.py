import threading
import time as _time

import numpy as np
import sounddevice as sd

_BLOCKSIZE = 512  # frames per callback; bounds stop() latency (~32 ms @16 kHz)


class AudioPlayer:
    """Non-blocking segment player on a callback-driven OutputStream.

    play() returns immediately. The PortAudio callback feeds samples and
    advances current_time; a daemon helper thread waits for the stream to
    finish and releases it. The UI polls current_time / is_playing from a
    timer — no Qt objects are touched outside the UI thread.
    stop() joins that thread, so on return resources are released and
    is_playing is False.
    """

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._playing = False
        self._current_time = 0.0
        self._pos = 0

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def current_time(self) -> float:
        """Playback position in seconds on the audio timeline (absolute)."""
        return self._current_time

    def play(
        self,
        audio: np.ndarray,
        sr: int,
        start: float = 0.0,
        end: float | None = None,
    ) -> bool:
        """Play audio[start*sr : end*sr] (end=None plays to the end).

        Stops any ongoing playback first; empty segments return False without
        touching audio hardware. Returns True when a stream was started.
        current_time is absolute on the audio timeline (start + elapsed).
        """
        self.stop()
        start_idx = int(max(0.0, start) * sr)
        end_idx = len(audio) if end is None else int(min(float(end), len(audio) / sr) * sr)
        if end_idx <= start_idx:
            return False
        segment = np.asarray(audio[start_idx:end_idx], dtype=np.float32)
        if segment.size == 0:
            return False

        self._playing = True
        self._pos = 0
        self._current_time = start

        def callback(outdata, frames, time_info, status):
            if not self._playing:
                raise sd.CallbackStop
            i = self._pos
            n = min(segment.size - i, frames)
            if n <= 0:
                outdata[:] = 0
                raise sd.CallbackStop
            outdata[:n, 0] = segment[i:i + n]
            outdata[n:] = 0
            self._pos += n
            self._current_time = start + self._pos / sr
            if n < frames:
                raise sd.CallbackStop

        try:
            stream = sd.OutputStream(
                samplerate=sr,
                channels=1,
                dtype="float32",
                blocksize=_BLOCKSIZE,
                callback=callback,
            )
            stream.start()
        except Exception:
            self._playing = False
            raise

        def wait_done():
            while self._playing and stream.active:
                _time.sleep(0.02)
            self._playing = False
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

        self._thread = threading.Thread(target=wait_done, daemon=True, name="AudioPlayer")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._playing = False
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        self._thread = None

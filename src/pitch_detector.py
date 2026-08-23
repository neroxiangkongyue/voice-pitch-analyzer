import numpy as np
import librosa


def extract_pitch(
    audio: np.ndarray,
    sr: int,
    frame_length: int = 2048,
    hop_length: int | None = None,
    fmin: float = 50.0,
    fmax: float = 500.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if hop_length is None:
        hop_length = frame_length // 4

    result = librosa.pyin(
        audio,
        sr=sr,
        fmin=fmin,
        fmax=fmax,
        frame_length=frame_length,
        hop_length=hop_length,
    )
    f0, voiced_confidence = result[0], result[1]

    times = librosa.times_like(
        f0, sr=sr, hop_length=hop_length, n_fft=frame_length
    )

    return times, f0, voiced_confidence


def detect_frequency_range(f0: np.ndarray, _audio_length: float) -> tuple[float, float]:
    valid = f0[~np.isnan(f0)]
    if valid.size == 0:
        return 50.0, 500.0
    low = max(50.0, float(np.percentile(valid, 5)))
    high = min(2000.0, float(np.percentile(valid, 95)))
    margin = (high - low) * 0.1
    return max(50.0, low - margin), min(2000.0, high + margin)

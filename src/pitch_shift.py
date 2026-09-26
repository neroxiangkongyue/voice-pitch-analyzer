"""Pitch-shift helpers: semitone ratio, F0 display shift, audio resynthesis."""
from __future__ import annotations

import numpy as np


def semitone_ratio(semitones: float) -> float:
    """Frequency ratio for a semitone offset: 2^(s/12)."""
    return 2.0 ** (float(semitones) / 12.0)


def shift_f0(f0: np.ndarray, semitones: float) -> np.ndarray:
    """Scale F0 by the pitch-shift ratio; NaNs stay NaN."""
    arr = np.asarray(f0, dtype=float)
    if semitones == 0.0:
        return arr.copy()
    out = arr * semitone_ratio(semitones)
    return out


def shift_audio(audio: np.ndarray, sr: int, semitones: float) -> np.ndarray:
    """Pitch-shift samples without changing duration (librosa effects).

    Returns a copy when semitones == 0 so callers can cache safely.
    """
    y = np.asarray(audio, dtype=np.float32).reshape(-1)
    if float(semitones) == 0.0:
        return y.copy()
    import librosa

    return librosa.effects.pitch_shift(y, sr=sr, n_steps=float(semitones))

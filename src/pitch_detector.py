import hashlib
import os

import numpy as np
import librosa

# Pitch analysis does not need the full sample rate: human F0 is 50-500 Hz,
# so 16 kHz is plenty. Downsampling here makes pyin ~15x faster with no
# practical accuracy loss. Playback still uses the original audio data.
ANALYSIS_SR = 16000
ANALYSIS_HOP = 256  # ~16 ms hop @ 16 kHz


def extract_pitch(
    audio: np.ndarray,
    sr: int,
    frame_length: int = 2048,
    hop_length: int | None = None,
    fmin: float = 50.0,
    fmax: float = 500.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if sr > ANALYSIS_SR:
        # Explicit soxr_hq: librosa's default res_type takes ~1s per few
        # seconds of audio, while soxr does the same job in milliseconds.
        audio = librosa.resample(
            audio, orig_sr=sr, target_sr=ANALYSIS_SR, res_type="soxr_hq"
        )
        if hop_length is not None:
            hop_length = max(1, round(hop_length * ANALYSIS_SR / sr))
        else:
            hop_length = ANALYSIS_HOP
        sr = ANALYSIS_SR

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


def _cache_file_for(filepath: str, sr: int) -> str:
    """Cache file path keyed on path + mtime + size + analysis parameters."""
    st = os.stat(filepath)
    key = (
        f"{os.path.abspath(filepath).lower()}|{st.st_mtime_ns}|{st.st_size}"
        f"|{sr}|sr{ANALYSIS_SR}|hop{ANALYSIS_HOP}|v1"
    )
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    cache_dir = os.path.join(base, "VoicePitchAnalyzer", "analysis_cache")
    return os.path.join(cache_dir, digest + ".npz")


def extract_pitch_cached(
    filepath: str, audio: np.ndarray, sr: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """extract_pitch with a per-file disk cache keyed on path+mtime+size.

    Re-opening the same file (even after restarting the app) skips the pyin
    computation entirely. Cache write failures are silently ignored.
    """
    try:
        cache_file = _cache_file_for(filepath, sr)
    except OSError:
        cache_file = None

    if cache_file and os.path.exists(cache_file):
        try:
            with np.load(cache_file) as data:
                return data["times"], data["f0"], data["confidence"]
        except Exception:
            pass  # Corrupt cache: fall through to full analysis.

    times, f0, confidence = extract_pitch(audio, sr)

    if cache_file:
        try:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            np.savez_compressed(
                cache_file, times=times, f0=f0, confidence=confidence
            )
        except Exception:
            pass  # Best effort only.

    return times, f0, confidence

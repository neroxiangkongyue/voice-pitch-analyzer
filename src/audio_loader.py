import os
import subprocess
import numpy as np


def load_audio(filepath: str) -> tuple[np.ndarray, float]:
    """Load audio file and return (samples, sample_rate).

    Supports wav, flac natively via soundfile.
    Supports mp3, m4a via ffmpeg conversion to wav.
    """
    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".wav", ".flac"):
        import soundfile as sf
        data, sr = sf.read(filepath, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        return data, sr

    if ext in (".mp3", ".m4a", ".aac"):
        tmp_wav = filepath + ".tmp.wav"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", filepath,
                    "-ac", "1", "-ar", "16000",
                    "-f", "wav", tmp_wav,
                ],
                capture_output=True, check=True, timeout=60,
            )
            import soundfile as sf
            data, sr = sf.read(tmp_wav, dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)
            return data, sr
        finally:
            if os.path.exists(tmp_wav):
                os.remove(tmp_wav)

    raise ValueError(f"Unsupported audio format: {ext}")

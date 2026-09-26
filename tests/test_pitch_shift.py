import numpy as np

from src.pitch_shift import semitone_ratio, shift_audio, shift_f0


def test_semitone_ratio():
    assert abs(semitone_ratio(0) - 1.0) < 1e-12
    assert abs(semitone_ratio(12) - 2.0) < 1e-12
    assert abs(semitone_ratio(-12) - 0.5) < 1e-12


def test_shift_f0():
    f0 = np.array([np.nan, 220.0, 440.0])
    out = shift_f0(f0, 12)
    assert np.isnan(out[0])
    assert abs(out[1] - 440.0) < 1e-9
    assert abs(out[2] - 880.0) < 1e-9
    same = shift_f0(f0, 0)
    assert np.allclose(same[1:], f0[1:], equal_nan=True)


def test_shift_audio_duration_and_energy():
    sr = 16000
    t = np.arange(0, 0.5, 1 / sr)
    y = (0.4 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    out = shift_audio(y, sr, 12)
    assert out.shape == y.shape
    # Dominant peak should move up ~1 octave
    mag = np.abs(np.fft.rfft(out * np.hanning(out.size)))
    freqs = np.fft.rfftfreq(out.size, 1 / sr)
    peak = freqs[int(np.argmax(mag))]
    assert abs(peak - 440.0) < 15.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(fns)} passed")

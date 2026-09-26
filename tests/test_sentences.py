"""Unit tests for sentence segmentation."""
import numpy as np

from src.sentences import sentence_index_at, segment_sentences


def _track(spans, dt=0.01, f0=220.0, total=None):
    """Build times/f0/conf with voiced spans [(a,b), ...]."""
    if total is None:
        total = max((b for _, b in spans), default=1.0) + 0.5
    times = np.arange(0.0, total, dt)
    f0_arr = np.full_like(times, np.nan)
    conf = np.zeros_like(times)
    for a, b in spans:
        m = (times >= a) & (times < b)
        f0_arr[m] = f0
        conf[m] = 0.9
    return times, f0_arr, conf


def test_empty():
    assert segment_sentences([], [], []) == []


def test_all_silent():
    times = np.arange(0, 2, 0.01)
    f0 = np.full_like(times, np.nan)
    conf = np.zeros_like(times)
    assert segment_sentences(times, f0, conf) == []


def test_single_phrase():
    times, f0, conf = _track([(1.0, 2.0)])
    segs = segment_sentences(times, f0, conf, lead_in_s=0.08, tail_s=0.12)
    assert len(segs) == 1
    s, e = segs[0]
    assert s == 1.0 - 0.08
    assert e == 2.0 - 0.01 + 0.12  # last voiced sample ~ 1.99


def test_gap_splits_sentences():
    # 0-1s voice, 1.6s silence gap, 2.6-3.6s voice  (gap ~1.6s > 0.45)
    times, f0, conf = _track([(0.0, 1.0), (2.6, 3.6)])
    segs = segment_sentences(times, f0, conf, lead_in_s=0.0, tail_s=0.0)
    assert len(segs) == 2
    assert abs(segs[0][1] - 1.0) < 0.05
    assert abs(segs[1][0] - 2.6) < 0.05


def test_short_gap_stays_one_sentence():
    times, f0, conf = _track([(0.0, 1.0), (1.2, 2.0)])  # 0.2s gap
    segs = segment_sentences(times, f0, conf, lead_in_s=0.0, tail_s=0.0, gap_s=0.45)
    assert len(segs) == 1


def test_tiny_fragment_merges():
    # Long A, 0.5s gap, 0.1s blip, 0.5s gap, long B → blip merges into nearer side
    times, f0, conf = _track([(0.0, 1.0), (1.5, 1.6), (2.1, 3.1)])
    segs = segment_sentences(times, f0, conf, lead_in_s=0.0, tail_s=0.0, min_sentence_s=0.25)
    assert len(segs) == 2
    # blip (1.5-1.6) should not become its own sentence
    assert not any(abs(a - 1.5) < 0.05 and abs(b - 1.6) < 0.05 for a, b in segs)


def test_index_at():
    segs = [(0.0, 1.0), (2.0, 3.0)]
    assert sentence_index_at(segs, 0.5) == 0
    assert sentence_index_at(segs, 2.5) == 1
    assert sentence_index_at(segs, 1.5) == -1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(fns)} passed")

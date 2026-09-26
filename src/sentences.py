"""Sentence segmentation over pitch tracks.

A sentence is a stretch of continuous voiced frames, split on long gaps and
merged when a fragment is too short to stand alone. Pure functions only.
"""
from __future__ import annotations

import numpy as np

# Gap between consecutive voiced frames that starts a new sentence.
GAP_S = 0.45
# Padding around voiced span when reporting [start, end].
LEAD_IN_S = 0.08
TAIL_S = 0.12
# Voiced spans shorter than this are merged into a neighbour.
MIN_SENTENCE_S = 0.25
# Safety bound for the merge loop.
_MERGE_ROUNDS = 8


def segment_sentences(
    times: np.ndarray,
    f0: np.ndarray,
    confidence: np.ndarray,
    *,
    gap_s: float = GAP_S,
    lead_in_s: float = LEAD_IN_S,
    tail_s: float = TAIL_S,
    min_sentence_s: float = MIN_SENTENCE_S,
) -> list[tuple[float, float]]:
    """Return inclusive [start, end] ranges (seconds) for each sentence.

    Empty list when there is no voiced material.
    """
    times = np.asarray(times, dtype=float)
    f0 = np.asarray(f0, dtype=float)
    confidence = np.asarray(confidence, dtype=float)
    if times.size == 0 or f0.size == 0 or confidence.size == 0:
        return []
    n = min(times.size, f0.size, confidence.size)
    times, f0, confidence = times[:n], f0[:n], confidence[:n]

    duration = float(times[-1]) if times[-1] > 0 else 0.0
    voiced = ~np.isnan(f0) & (confidence > 0.05) & (f0 > 0)
    if not voiced.any():
        return []

    idx = np.flatnonzero(voiced)
    t_v = times[idx]
    if idx.size == 1:
        runs = [idx]
    else:
        breaks = np.flatnonzero(np.diff(t_v) > gap_s)
        runs = np.split(idx, breaks + 1)

    # Raw voiced spans as (start, end) using frame times.
    spans: list[list[float]] = []
    for run in runs:
        if run.size == 0:
            continue
        spans.append([float(times[run[0]]), float(times[run[-1]])])
    if not spans:
        return []

    # Merge short fragments into the closer neighbour.
    for _ in range(_MERGE_ROUNDS):
        changed = False
        out: list[list[float]] = []
        i = 0
        while i < len(spans):
            start, end = spans[i]
            length = end - start
            if length >= min_sentence_s or len(spans) == 1:
                out.append([start, end])
                i += 1
                continue
            # Candidate neighbours: previous in `out` and next raw span.
            prev = out[-1] if out else None
            nxt = spans[i + 1] if i + 1 < len(spans) else None
            gap_prev = (start - prev[1]) if prev else float("inf")
            gap_next = (nxt[0] - end) if nxt else float("inf")
            if prev is not None and gap_prev <= gap_next:
                prev[1] = max(prev[1], end)
            elif nxt is not None:
                nxt[0] = min(nxt[0], start)
                out.append(nxt)  # will be reconsidered if still short? keep and advance
                # Remove the merged next from the scan by skipping it.
                i += 2
                changed = True
                continue
            elif prev is not None:
                prev[1] = max(prev[1], end)
            changed = True
            i += 1
        spans = out
        if not changed:
            break

    # Collapse any residual short single span into a padded one.
    result: list[tuple[float, float]] = []
    for start, end in spans:
        s = max(0.0, start - lead_in_s)
        e = min(duration, end + tail_s) if duration > 0 else end + tail_s
        if e <= s:
            continue
        result.append((s, e))
    return result


def sentence_index_at(sentences: list[tuple[float, float]], t: float) -> int:
    """Index of the sentence containing t, or -1."""
    for i, (a, b) in enumerate(sentences):
        if a <= t <= b:
            return i
    return -1

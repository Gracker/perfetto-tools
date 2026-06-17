import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fps-test'))
from compute_fps import Gesture, _gestures_from_swipe_log, _dedup_action_timestamps


PATTERN = os.path.join(os.path.dirname(__file__), '..', 'fps-test', 'swipe_pattern.txt')


def test_swipe_pattern_has_six_lines():
    lines = [l for l in open(PATTERN)
             if l.strip() and not l.strip().startswith("#")]
    assert len(lines) == 6, "swipe_pattern.txt should have 6 swipes (3 up + 3 down)"


def test_swipe_pattern_three_up_three_down():
    lines = [l for l in open(PATTERN)
             if l.strip() and not l.strip().startswith("#")]
    dirs = [l.split()[0] for l in lines]
    assert dirs.count("up") == 3
    assert dirs.count("down") == 3
    assert dirs == ["up"] * 3 + ["down"] * 3


def test_swipe_pattern_fields_well_formed():
    # Each non-comment line: direction x1 y1 x2 y2 duration_ms gap_ms (7 fields).
    lines = [l for l in open(PATTERN)
             if l.strip() and not l.strip().startswith("#")]
    for l in lines:
        parts = l.split()
        assert len(parts) == 7, f"malformed swipe line: {l!r}"
        assert parts[0] in ("up", "down")
        for field in parts[1:]:
            int(field)  # all numeric


def test_gestures_from_swipe_log():
    # tier-3 swipe-log pairs → Gestures with no UP marker (down==up).
    log = [(1000, 2000), (3000, 4000)]
    gs = _gestures_from_swipe_log(log)
    assert len(gs) == 2
    assert isinstance(gs[0], Gesture)
    assert gs[0].down_ns == 1000 and gs[0].up_ns == 1000  # no UP marker
    assert gs[0].end_ns == 2000
    assert gs[1].end_ns == 4000


# --- _dedup_action_timestamps: collapses per-channel ACTION bursts ---

def test_dedup_collapses_same_kind_burst():
    # Three DOWNs within 50ms (one gesture dispatched through multiple input
    # channels) collapse to one; same for the UP burst.
    events = [(1_000_000_000, False), (1_000_010_000, False), (1_000_020_000, False),
              (1_500_000_000, True),  (1_500_010_000, True),   # one UP burst
              (2_000_000_000, False)]                          # next gesture DOWN
    out = _dedup_action_timestamps(events)
    assert out == [(1_000_000_000, False), (1_500_000_000, True), (2_000_000_000, False)]


def test_dedup_keeps_events_across_threshold():
    # Two DOWNs 100ms apart (100_000_000 ns > 50ms threshold) are two separate
    # gestures -> both kept.
    events = [(0, False), (100_000_000, False)]
    out = _dedup_action_timestamps(events)
    assert out == events


def test_dedup_kind_switch_not_collapsed():
    # A DOWN immediately followed by an UP (same burst window, different kind)
    # must NOT collapse — they're a real gesture transition.
    events = [(1000, False), (1010, True)]
    out = _dedup_action_timestamps(events)
    assert out == events


def test_dedup_empty():
    assert _dedup_action_timestamps([]) == []

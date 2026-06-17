"""Tests for gesture extraction + three-phase FPS (press / fling / overall).

These cover the trace-integration logic that previously had ZERO unit coverage
(the bugs found when running compute_fps.py on the SmartPerfetto/test-traces/
real-device traces). The pure-math layer (compute_fps_from_frames etc.) stays
covered by test_compute_fps_math.py and is NOT duplicated here.

We test the extraction as pure functions:
  - _dedup_action_timestamps  (burst collapse)
  - _pair_actions_to_gestures  (down/up/end pairing → Gesture)
  - gesture → three-phase FlingWindow derivation
so no trace_processor / real trace is needed — fast and deterministic.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fps-test'))
from compute_fps import (
    Gesture, FlingWindow, _dedup_action_timestamps, _pair_actions_to_gestures,
    gesture_windows, THREE_PHASES,
)


# --- _dedup_action_timestamps: collapse per-channel ACTION bursts into one ---

def test_dedup_collapses_same_kind_burst():
    # One physical gesture fires ACTION_DOWN on 4 input channels within <50ms.
    # They must collapse to a single DOWN.
    events = [
        (1000, False),   # DOWN channel 1
        (1010, False),   # DOWN channel 2 (same burst)
        (1030, False),   # DOWN channel 3
        (1050, False),   # DOWN channel 4
        (200_000, True),  # UP (later)
    ]
    out = _dedup_action_timestamps(events)
    downs = [t for t, up in out if not up]
    ups = [t for t, up in out if up]
    assert downs == [1000]   # first of the burst kept
    assert ups == [200_000]


def test_dedup_keeps_separated_events():
    # Two gestures far apart in time must NOT collapse.
    events = [
        (0, False),       # gesture 1 DOWN
        (100, True),      # gesture 1 UP
        (3_000_000_000, False),  # gesture 2 DOWN (3s later, > 50ms gap)
        (3_000_000_100, True),   # gesture 2 UP
    ]
    out = _dedup_action_timestamps(events)
    assert out == events   # nothing collapsed


# --- _pair_actions_to_gestures: DOWN → UP → next DOWN / trace end ---

def test_single_gesture_produces_one_window():
    # THE bug: a single DOWN...UP trace previously produced ZERO windows
    # (old logic paired UP→next DOWN, which never exists for one gesture).
    # Now it must yield one Gesture whose end_ns = trace_end_ns.
    events = [(1000, False), (130_000_000, True)]   # 130ms press
    gestures = _pair_actions_to_gestures(events, trace_end_ns=2_000_000_000)
    assert len(gestures) == 1
    g = gestures[0]
    assert g.down_ns == 1000
    assert g.up_ns == 130_000_000
    assert g.end_ns == 2_000_000_000   # fling tail extends to trace end


def test_multi_gesture_pairing():
    # Four gestures (matches scroll-demo-customer-scroll.pftrace shape).
    # Each gesture's end_ns = next gesture's DOWN; last one's = trace_end.
    events = [
        (0,            False),  # g0 DOWN
        (163_000_000,  True),   # g0 UP
        (2_900_000_000, False), # g1 DOWN
        (3_050_000_000, True),  # g1 UP
        (5_800_000_000, False), # g2 DOWN
        (5_960_000_000, True),  # g2 UP
        (8_700_000_000, False), # g3 DOWN
        (8_830_000_000, True),  # g3 UP
    ]
    trace_end = 10_000_000_000
    gestures = _pair_actions_to_gestures(events, trace_end_ns=trace_end)
    assert len(gestures) == 4
    # each gesture's down/up preserved
    assert gestures[0].down_ns == 0
    assert gestures[0].up_ns == 163_000_000
    assert gestures[0].end_ns == 2_900_000_000   # next DOWN
    # last gesture's fling tail → trace end
    assert gestures[3].down_ns == 8_700_000_000
    assert gestures[3].end_ns == trace_end


def test_dangling_down_without_up_skipped():
    # A DOWN with no following UP (truncated trace) can't form a press phase.
    # It is skipped rather than producing a bogus zero-width press window.
    events = [(1000, False), (130_000, True), (999_999, False)]  # last DOWN no UP
    gestures = _pair_actions_to_gestures(events, trace_end_ns=2_000_000)
    assert len(gestures) == 1   # only the complete gesture
    assert gestures[0].down_ns == 1000


# --- three-phase derivation: overall / press / fling windows ---

def test_three_phase_derivation():
    # One gesture: press [0,100M), fling [100M,1B).
    g = Gesture(down_ns=0, up_ns=100_000_000, end_ns=1_000_000_000)
    overall, press, fling = gesture_windows([g])
    assert overall == [FlingWindow(0, 1_000_000_000)]      # down..end
    assert press == [FlingWindow(0, 100_000_000)]          # down..up
    assert fling == [FlingWindow(100_000_000, 1_000_000_000)]  # up..end


def test_three_phase_phases_constant():
    # The three phase keys are stable identifiers used in the report.
    assert THREE_PHASES == ("overall", "press", "fling")


def test_press_phase_empty_when_swipe_log_only():
    # tier-3 (device-clock swipe markers) gives only (start,end) pairs with no
    # mid UP marker → the press phase is degenerate (down==up), and we must not
    # emit a zero-width window that would divide by zero. Gesture built from a
    # swipe-log pair has down_ns==up_ns, so press window is empty.
    g = Gesture(down_ns=0, up_ns=0, end_ns=1_000_000_000)   # synthetic: no UP
    overall, press, fling = gesture_windows([g])
    assert overall == [FlingWindow(0, 1_000_000_000)]
    assert press == []                   # zero-width → dropped
    assert fling == [FlingWindow(0, 1_000_000_000)]   # whole span treated as fling

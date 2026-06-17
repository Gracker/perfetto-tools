import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fps-test'))
from compute_fps import (
    Frame, FlingWindow, BufferEvent, compute_fps_from_frames, summarize_windows,
    summarize_by_source, detect_overwrite_drops, buffer_events_to_frames, FpsReport,
)


def frame(ts_ns, dur_ns, dropped=False):
    # Default source is "display" (SurfaceFlinger output = screen refreshes),
    # matching how analyze_trace tags layer_name=NULL FrameTimeline rows. Tests
    # that want a different source pass source= explicitly.
    return Frame(ts=ts_ns, dur=dur_ns, dropped=dropped, source="display")


def test_sixty_fps_one_second_window():
    # 60 frames of 16.67ms each within a 1s fling window, none dropped.
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = [frame(i * 16_666_666, 16_666_666) for i in range(60)]
    rep = compute_fps_from_frames(frames, [w])
    assert rep.overall_fps == pytest.approx(60.0, abs=0.5)
    assert rep.total_frames == 60
    assert rep.dropped_frames == 0


def test_dropped_frames_counted():
    # 60 nominal slots; 5 dropped. FPS computed over presented frames only
    # (standard: dropped frames don't contribute to on-screen refresh rate).
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = [frame(i * 16_666_666, 16_666_666, dropped=(i % 12 == 0))
              for i in range(60)]
    rep = compute_fps_from_frames(frames, [w])
    assert rep.total_frames == 60
    assert rep.dropped_frames == 5
    assert rep.presented_frames == 55


def test_frames_outside_window_excluded():
    # Two windows; frames between them excluded.
    w1 = FlingWindow(start_ns=0,       end_ns=500_000_000)
    w2 = FlingWindow(start_ns=800_000_000, end_ns=1_300_000_000)
    # 10 frames in w1, 0 in gap, 10 in w2
    frames = ([frame(i * 50_000_000, 16_666_666) for i in range(10)] +
              [frame(650_000_000, 16_666_666)] +   # in the gap → excluded
              [frame(800_000_000 + i * 50_000_000, 16_666_666) for i in range(10)])
    rep = compute_fps_from_frames(frames, [w1, w2])
    assert rep.total_frames == 20  # gap frame excluded


def test_empty_window_zero_fps():
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    rep = compute_fps_from_frames([], [w])
    assert rep.overall_fps == 0.0
    assert rep.total_frames == 0


def test_drop_rate_percent():
    # 100 frames all within a 1s window, 10 of them dropped → 10% drop rate.
    # (Use a 10ms spacing so all 100 frames fit inside [0, 1e9).)
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = [frame(i * 10_000_000, 8_000_000, dropped=(i < 10))
              for i in range(100)]
    rep = compute_fps_from_frames(frames, [w])
    assert rep.drop_rate == pytest.approx(10.0, abs=0.1)


def test_per_window_breakdown():
    w1 = FlingWindow(start_ns=0,           end_ns=500_000_000)
    w2 = FlingWindow(start_ns=600_000_000, end_ns=1_100_000_000)
    frames = ([frame(i * 50_000_000, 16_666_666) for i in range(10)] +
              [frame(600_000_000 + i * 50_000_000, 16_666_666) for i in range(10)])
    rep = compute_fps_from_frames(frames, [w1, w2])
    assert len(rep.windows) == 2
    assert rep.windows[0].frame_count == 10
    assert rep.windows[1].frame_count == 10


def test_summarize_windows_handles_drops():
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = [frame(i * 16_666_666, 16_666_666, dropped=(i % 10 == 0))
              for i in range(60)]
    per_window = summarize_windows(frames, [w])
    assert len(per_window) == 1
    assert per_window[0].dropped == 6


def test_janky_frames_count_toward_fps():
    # A janky frame WAS presented (just late) → it still counts toward FPS, but is
    # reported separately. Dropped frames do not. This guards the jank/drop split.
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = [Frame(i * 16_666_666, 16_666_666, janky=(i % 4 == 0), source="display")
              for i in range(60)]
    rep = compute_fps_from_frames(frames, [w])
    assert rep.janky_frames == 15
    assert rep.dropped_frames == 0
    assert rep.presented_frames == 60
    assert rep.overall_fps == pytest.approx(60.0, abs=0.5)  # jank does NOT lower FPS


def test_janky_not_double_counted_when_dropped():
    # A frame that is BOTH dropped AND has a jank tag must count as dropped only,
    # not also as janky. (Mirrors FrameTimeline: present_type='Dropped Frame' with
    # jank_type='Dropped Frame'.)
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = [Frame(0, 16_666_666, dropped=True, janky=True),
              Frame(16_666_666, 16_666_666)]
    rep = compute_fps_from_frames(frames, [w])
    assert rep.dropped_frames == 1
    assert rep.janky_frames == 0  # the dropped+tagged frame is NOT also janky


# --- Multiple frame sources (SurfaceView / TextureView / video / ...) ---

def test_per_source_breakdown_not_merged():
    # Two sources active in the same window must be reported separately.
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    pipeline = [Frame(i * 16_666_666, 16_666_666, source="app-pipeline")
                for i in range(60)]
    video = [Frame(i * 33_333_333, 33_333_333, source="SurfaceView[video]")
             for i in range(30)]
    rep = compute_fps_from_frames(pipeline + video, [w])
    by = {s.source: s for s in rep.by_source}
    assert set(by) == {"app-pipeline", "SurfaceView[video]"}
    assert by["app-pipeline"].fps == pytest.approx(60.0, abs=0.5)
    assert by["SurfaceView[video]"].fps == pytest.approx(30.0, abs=0.5)
    # Aggregate still counts both.
    assert rep.total_frames == 90


def test_display_source_bucketed():
    # FrameTimeline rows with layer_name=NULL are bucketed as "display"
    # (SF output refreshes), distinct from per-surface sources.
    w = FlingWindow(start_ns=0, end_ns=1_000_000_000)
    frames = ([Frame(i * 16_666_666, 16_666_666, source="display")
               for i in range(60)] +
              [Frame(i * 16_666_666, 16_666_666, source="TX - App#1")
               for i in range(30)])
    rep = compute_fps_from_frames(frames, [w])
    by = {s.source: s for s in rep.by_source}
    assert "display" in by
    assert "TX - App#1" in by


# --- TextureView single-buffer overwrite = dropped (pure math, kept as library) ---

def test_overwrite_drop_detected():
    # queue, queue (overwrite!), acquire, queue, acquire → exactly 1 overwrite.
    ev = [
        BufferEvent(10, "TextureView[x]", "queue"),
        BufferEvent(20, "TextureView[x]", "queue"),    # overwrites the ts=10 buffer
        BufferEvent(25, "TextureView[x]", "acquire"),
        BufferEvent(40, "TextureView[x]", "queue"),
        BufferEvent(45, "TextureView[x]", "acquire"),
    ]
    drops = detect_overwrite_drops(ev, "TextureView[x]")
    assert len(drops) == 1
    assert drops[0].ts == 10
    assert drops[0].dropped is True
    assert drops[0].source == "TextureView[x]"


def test_overwrite_none_when_each_consumed():
    ev = [
        BufferEvent(10, "L", "queue"), BufferEvent(15, "L", "latch"),
        BufferEvent(20, "L", "queue"), BufferEvent(25, "L", "latch"),
    ]
    assert detect_overwrite_drops(ev, "L") == []


def test_buffer_events_to_frames_counts_presented_and_dropped():
    ev = [
        BufferEvent(10, "L", "queue"),
        BufferEvent(20, "L", "queue"),   # overwrite → ts=10 dropped
        BufferEvent(25, "L", "acquire"), # ts=20 presented
        BufferEvent(40, "L", "queue"),
        BufferEvent(45, "L", "latch"),   # ts=40 presented
    ]
    frames = buffer_events_to_frames(ev, "L")
    presented = [f for f in frames if not f.dropped]
    dropped = [f for f in frames if f.dropped]
    assert len(presented) == 2
    assert len(dropped) == 1
    assert all(f.source == "L" for f in frames)

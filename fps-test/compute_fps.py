#!/usr/bin/env python3
"""Compute FPS and dropped-frame stats from a Perfetto trace.

Two layers:
  1. Pure math: Frame/FlingWindow/BufferEvent dataclasses, compute_fps_from_frames(),
     per-source aggregation, and single-buffer overwrite detection. Fully
     unit-tested with synthetic data (no trace_processor needed).
  2. trace_processor integration: load a real .perfetto-trace, derive fling windows,
     gather frames from FrameTimeline (actual_frame_timeline_slice, grouped by
     layer_name → one source per surface), call the math.

Architecture decisions (see docs/spike-notes.md, validated on API 36):
  - fling windows: 3-tier fallback.
      (1) structured android_input_events.event_action (non-NULL; debuggable builds)
      (2) atrace 'input' slices dispatchInputEvent ACTION_UP → next ACTION_DOWN
          (works on user builds; confirmed present)
      (3) device-clock swipe markers from run_fps_test.sh (coarse, last resort)
  - multi-source FPS = FrameTimeline grouped by layer_name. layer_name IS NULL →
    bucketed as source "display" (SF output refreshes). frame_slice is empty on
    this device, so there is no separate BufferQueue path.
  - drops = present_type='Dropped Frame'. jank = jank_type != None and not dropped.
    (BufferQueue slices lack layer attribution here, so single-buffer overwrite
    detection is NOT wired into analyze_trace; detect_overwrite_drops /
    buffer_events_to_frames remain as a tested library for traces that expose
    layer-attributed buffer events.)
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Frame:
    """One produced frame from ONE frame source (layer/surface).

    ts  : presentation timestamp in nanoseconds.
    dur : frame duration in nanoseconds.
    dropped : True if the frame was NEVER presented on screen — a FrameTimeline
              'Dropped Frame' present_type. Dropped frames do NOT count toward FPS.
    janky : True if the frame WAS presented but missed its deadline (FrameTimeline
            jank_type != 'None'). A janky frame still refreshed the screen, so it
            counts toward FPS — jank is a separate quality signal from drop.
    source : the frame-production source this frame belongs to (one per layer).
             FrameTimeline covers each surface layer; layer_name IS NULL rows are
             bucketed as "display" (SF composited refreshes).
    """
    ts: int
    dur: int
    dropped: bool = False
    janky: bool = False
    source: str = "app-pipeline"


@dataclass
class BufferEvent:
    """One BufferQueue lifecycle event on a single layer. Used by the
    single-buffer overwrite detection library (not wired into analyze_trace on
    devices where buffer slices lack layer attribution)."""
    ts: int
    layer: str
    kind: str  # 'queue' | 'acquire' | 'latch'


@dataclass
class FlingWindow:
    """A time range [start_ns, end_ns) corresponding to one fling gesture
    (finger-up ACTION_UP to the next finger-down ACTION_DOWN)."""
    start_ns: int
    end_ns: int


@dataclass
class WindowStat:
    index: int
    start_ns: int
    end_ns: int
    frame_count: int
    dropped: int
    janky: int
    fps: float


@dataclass
class SourceStat:
    source: str
    frame_count: int
    presented: int
    dropped: int
    janky: int
    fps: float


@dataclass
class FpsReport:
    # overall_fps is the SCREEN refresh rate: presented 'display' (SurfaceFlinger
    # output) frames over the union of fling windows. This is the FPS the user
    # actually saw. The per-source breakdown (by_source) gives each producing
    # surface's own rate separately — do NOT sum them for a screen FPS.
    overall_fps: float
    total_frames: int
    presented_frames: int
    dropped_frames: int
    janky_frames: int
    drop_rate: float  # percent
    windows: list  # list[WindowStat]
    by_source: list = field(default_factory=list)  # list[SourceStat]


def _frame_in_window(f: Frame, w: FlingWindow) -> bool:
    return w.start_ns <= f.ts < w.end_ns


def summarize_windows(frames, windows):
    """Per-window frame/drop counts.

    `fps` is the SCREEN refresh rate of the window, computed from the 'display'
    source (SurfaceFlinger's composited output = actual on-screen refreshes), NOT
    the sum of all sources. The per-source breakdown (summarize_by_source) gives
    each producer's own rate; summing them would over-count (a 120Hz screen with
    a 60fps list + a 60fps video is still 120Hz, not 240).
    """
    out = []
    for i, w in enumerate(windows):
        in_w = [f for f in frames if _frame_in_window(f, w)]
        presented = [f for f in in_w if not f.dropped]
        dropped = sum(1 for f in in_w if f.dropped)
        janky = sum(1 for f in in_w if f.janky and not f.dropped)
        span_s = (w.end_ns - w.start_ns) / 1e9
        # Screen FPS from the display source only.
        display_presented = [f for f in in_w
                             if not f.dropped and f.source == "display"]
        fps = (len(display_presented) / span_s) if span_s > 0 else 0.0
        out.append(WindowStat(
            index=i, start_ns=w.start_ns, end_ns=w.end_ns,
            frame_count=len(in_w), dropped=dropped, janky=janky, fps=fps,
        ))
    return out


def summarize_by_source(frames, windows):
    """Per-frame-source breakdown over the union of windows. Each source
    (layer/surface) is reported separately so multiple producers are never merged
    into one misleading number."""
    total_span_s = sum((w.end_ns - w.start_ns) for w in windows) / 1e9
    in_any = [f for f in frames if any(_frame_in_window(f, w) for w in windows)]
    sources = {}
    for f in in_any:
        sources.setdefault(f.source, []).append(f)
    out = []
    for source in sorted(sources):
        fs = sources[source]
        dropped = sum(1 for f in fs if f.dropped)
        janky = sum(1 for f in fs if f.janky and not f.dropped)
        presented = len(fs) - dropped
        fps = (presented / total_span_s) if total_span_s > 0 else 0.0
        out.append(SourceStat(
            source=source, frame_count=len(fs),
            presented=presented, dropped=dropped, janky=janky, fps=fps,
        ))
    return out


def detect_overwrite_drops(events, layer):
    """Single-buffer overwrite detection for ONE confirmed single-buffer layer
    (e.g. a TextureView/SurfaceTexture). A producer 'queue' whose buffer is
    overwritten by the NEXT 'queue' without any 'acquire'/'latch' (consume) in
    between was never displayed → a dropped frame.

    Pure sequence logic — unit-tested, no device. NOT wired into analyze_trace on
    devices where BufferQueue slices lack layer attribution (see module docstring).
    """
    seq = sorted((e for e in events if e.layer == layer), key=lambda e: e.ts)
    drops = []
    pending_queue_ts = None
    for e in seq:
        if e.kind == "queue":
            if pending_queue_ts is not None:
                drops.append(Frame(ts=pending_queue_ts, dur=0,
                                   dropped=True, source=layer))
            pending_queue_ts = e.ts
        elif e.kind in ("acquire", "latch"):
            pending_queue_ts = None  # consumed; safe
    return drops


def buffer_events_to_frames(events, layer):
    """Turn a layer's BufferQueue events into Frames: each consumed buffer
    (queue → acquire/latch) is one presented frame; each overwrite is one
    dropped frame. Pure library; see detect_overwrite_drops note."""
    seq = sorted((e for e in events if e.layer == layer), key=lambda e: e.ts)
    frames = []
    pending = None
    for e in seq:
        if e.kind == "queue":
            if pending is not None:
                frames.append(Frame(ts=pending, dur=0, dropped=True, source=layer))
            pending = e.ts
        elif e.kind in ("acquire", "latch"):
            if pending is not None:
                frames.append(Frame(ts=pending, dur=0, dropped=False, source=layer))
                pending = None
    return frames


def compute_fps_from_frames(frames, windows) -> FpsReport:
    """Compute FPS over the union of fling windows.

    `overall_fps` is the SCREEN refresh rate: presented 'display' (SurfaceFlinger
    output) frames over the union-of-window-seconds. That is the meaningful
    "what FPS did the user see" number. (Summing every source would over-count —
    see summarize_windows.) Per-source rates are in `by_source`; total/dropped/
    janky counts aggregate across ALL sources.
    """
    in_any = [f for f in frames if any(_frame_in_window(f, w) for w in windows)]
    total = len(in_any)
    dropped = sum(1 for f in in_any if f.dropped)
    janky = sum(1 for f in in_any if f.janky and not f.dropped)
    presented = total - dropped

    total_span_s = sum((w.end_ns - w.start_ns) for w in windows) / 1e9
    display_presented = sum(1 for f in in_any
                            if not f.dropped and f.source == "display")
    overall_fps = (display_presented / total_span_s) if total_span_s > 0 else 0.0
    drop_rate = (100.0 * dropped / total) if total > 0 else 0.0

    return FpsReport(
        overall_fps=overall_fps,
        total_frames=total,
        presented_frames=presented,
        dropped_frames=dropped,
        janky_frames=janky,
        drop_rate=drop_rate,
        windows=summarize_windows(frames, windows),
        by_source=summarize_by_source(frames, windows),
    )


# ---------------------------------------------------------------------------
# trace_processor integration (only imported when analyzing a real trace)
# ---------------------------------------------------------------------------

def _fallback_windows_from_log(swipe_log):
    """Fallback windows from run_fps_test.sh's swipe log.

    The log holds DEVICE REALTIME nanoseconds (recorded via `adb shell date
    +%s%N`), NOT host time — so they share a clock family with the trace and can
    be compared after converting frame ts to realtime (see analyze_trace).
    Each entry is an (start_ns, end_ns) device-realtime tuple."""
    return [FlingWindow(s, e) for s, e in swipe_log]


def analyze_trace(trace_path, swipe_log=None):
    """Load a trace and return an FpsReport.

    Fling windows: tier-1 structured input → tier-2 atrace ACTION slices →
    tier-3 device-clock swipe markers. Frames always come from FrameTimeline's
    actual_frame_timeline_slice, grouped by layer_name.

    swipe_log: optional list of (start_ns, end_ns) device-realtime tuples (tier-3).
    """
    from perfetto.trace_processor import TraceProcessor

    tp = TraceProcessor(trace=str(trace_path))

    # Try tier 1 (structured) then tier 2 (atrace slices); both in trace time.
    windows = _extract_fling_windows_structured(tp)
    source = "structured input"
    realtime = False
    if not windows:
        windows = _extract_fling_windows_atrace(tp)
        source = "atrace ACTION slices"
    if not windows:
        # tier 3: device-clock markers → must compare in realtime.
        if not swipe_log:
            raise RuntimeError(
                "No fling windows found. Structured input and atrace ACTION slices "
                "are both absent (is the 'input' atrace category in the config?), "
                "and no --swipe-log fallback was provided."
            )
        print("[fps] WARNING: no input events in trace; using device-clock swipe "
              "markers (tier-3, less precise).", flush=True)
        windows = _fallback_windows_from_log(swipe_log)
        realtime = True
        source = "device-clock swipe markers"
    else:
        print(f"[fps] fling windows from {source} ({len(windows)} windows).", flush=True)

    frames = _query_frames(tp, realtime=realtime)
    if not frames:
        raise RuntimeError(
            "No FrameTimeline frames in trace. Needs Android 12+ and the "
            "android.surfaceflinger.frametimeline data source in 02_jank_frame.pbtx."
        )
    return compute_fps_from_frames(frames, windows)


def _extract_fling_windows_structured(tp):
    """Tier 1: ACTION_UP → next ACTION_DOWN from structured android_input_events.
    Only populated on debuggable/userdebug/eng builds (event_action is NULL on
    user builds). The time column is `event_time` (the MotionEvent's own clock),
    not `ts`. Returns [] otherwise → caller falls through to tier 2.
    """
    windows = []
    try:
        qr = tp.query("""
            INCLUDE PERFETTO MODULE android.input;
            SELECT event_time, event_action
            FROM android_input_events
            WHERE event_action IS NOT NULL
            ORDER BY event_time
        """)
        events = []
        for row in qr:
            action = (row.event_action or "").upper()
            if "UP" in action:
                events.append((row.event_time, True))
            elif "DOWN" in action:
                events.append((row.event_time, False))
        events = _dedup_action_timestamps(events)
        for i, (ts, is_up) in enumerate(events):
            if not is_up:
                continue
            for j in range(i + 1, len(events)):
                tts, tis_up = events[j]
                if not tis_up:
                    windows.append(FlingWindow(ts, tts))
                    break
    except Exception as e:
        print(f"[fps] structured input query failed ({e}); trying atrace tier.", flush=True)
    return windows


def _dedup_action_timestamps(events, gap_ns=50_000_000):
    """Collapse ACTION events fired in a tight burst into one per gesture.

    A single `adb input swipe` produces several ACTION_DOWN slices (and several
    ACTION_UP) because the same MotionEvent is dispatched through multiple input
    channels (GestureMonitor, PointerEventDispatcher, app channel). They land
    within tens of ms of each other. Without de-dup, each UP would pair to the
    next DOWN and produce N overlapping windows per gesture.

    `events`: sorted list of (ts, is_up). Adjacent same-kind events within
    `gap_ns` (default 50ms) collapse to the FIRST of the burst. Returns a cleaned
    list of (ts, is_up).
    """
    if not events:
        return []
    out = [events[0]]
    for ts, is_up in events[1:]:
        last_ts, last_kind = out[-1]
        if is_up == last_kind and (ts - last_ts) < gap_ns:
            continue  # same kind, same burst → drop
        out.append((ts, is_up))
    return out


def _extract_fling_windows_atrace(tp):
    """Tier 2: ACTION_UP → next ACTION_DOWN from the 'input' atrace category's
    dispatchInputEvent slices. Works on user builds (confirmed on API 36 user).

    A fling window starts when the finger lifts (ACTION_UP) and ends at the next
    finger press (ACTION_DOWN). One gesture fires multiple slices (one per input
    channel), so we de-dup bursts first, then pair each UP with the next DOWN.

    Returns [] on any failure → caller falls through to tier 3.
    """
    try:
        qr = tp.query("""
            SELECT ts, name
            FROM slice
            WHERE name LIKE 'dispatchInputEvent MotionEvent ACTION_DOWN%'
                 OR name LIKE 'dispatchInputEvent MotionEvent ACTION_UP%'
            ORDER BY ts
        """)
        rows = list(qr)
        if not rows:
            qr = tp.query("""
                SELECT ts, name
                FROM slice
                WHERE name LIKE 'publishMotionEvent%action=UP%'
                      OR name LIKE 'publishMotionEvent%action=DOWN%'
                ORDER BY ts
            """)
            rows = list(qr)
        events = []
        for row in rows:
            n = (row.name or "").upper()
            if "ACTION_UP" in n or "ACTION=UP" in n:
                events.append((row.ts, True))
            elif "ACTION_DOWN" in n or "ACTION=DOWN" in n:
                events.append((row.ts, False))
        events.sort()
        events = _dedup_action_timestamps(events)
        # Pair each UP with the next DOWN after it.
        windows = []
        for i, (ts, is_up) in enumerate(events):
            if not is_up:
                continue
            for j in range(i + 1, len(events)):
                tts, tis_up = events[j]
                if not tis_up:  # next DOWN
                    windows.append(FlingWindow(ts, tts))
                    break
        return windows
    except Exception as e:
        print(f"[fps] atrace ACTION slice query failed ({e}).", flush=True)
        return []


# present_type values that mean a frame never reached the screen.
_DROPPED_PRESENT_TYPES = {"Dropped Frame"}


def _query_frames(tp, realtime=False):
    """Return per-SURFACE Frame list from actual_frame_timeline_slice.

    Each layer is its own source; layer_name IS NULL → "display" (SF refreshes).
    Two distinct signals:
      - dropped : present_type in _DROPPED_PRESENT_TYPES → never on screen → not FPS.
      - janky   : jank_type IS NOT NULL AND != 'None' AND not dropped → late but shown.
    realtime=True maps ts via TO_REALTIME() so frames line up with device-clock
    swipe markers (tier-3 fallback). Schema confirmed on API 36 (see spike-notes).
    """
    ts_expr = "TO_REALTIME(ts)" if realtime else "ts"
    qr = tp.query(f"""
        SELECT
            {ts_expr} AS ts,
            dur,
            CASE WHEN layer_name IS NULL OR layer_name = '' THEN 'display'
                 ELSE layer_name END AS source,
            CASE WHEN present_type = 'Dropped Frame' THEN 1 ELSE 0 END AS dropped,
            CASE WHEN jank_type IS NOT NULL
                      AND jank_type != 'None'
                      AND present_type != 'Dropped Frame'
                 THEN 1 ELSE 0 END AS janky
        FROM actual_frame_timeline_slice
        WHERE dur > 0
        ORDER BY ts
    """)
    frames = []
    for row in qr:
        frames.append(Frame(ts=row.ts, dur=row.dur,
                            dropped=bool(row.dropped), janky=bool(row.janky),
                            source=row.source))
    return frames


def format_report(report: FpsReport, trace_path: str) -> str:
    lines = []
    lines.append(f"=== FPS Report: {trace_path} ===")
    lines.append(f"Screen FPS (display/SF output over fling windows): {report.overall_fps:.1f}")
    lines.append(f"Total frames (all sources): {report.total_frames}")
    lines.append(f"  presented       : {report.presented_frames}")
    lines.append(f"  dropped         : {report.dropped_frames}  (never on screen)")
    lines.append(f"  janky           : {report.janky_frames}  (presented late)")
    lines.append(f"Drop rate         : {report.drop_rate:.2f}%")
    lines.append("")
    lines.append("Per-frame-source breakdown (this is the FPS that matters):")
    if report.by_source:
        for s in report.by_source:
            lines.append(
                f"  {s.source:<40} fps={s.fps:6.1f} frames={s.frame_count} "
                f"presented={s.presented} dropped={s.dropped} janky={s.janky}"
            )
    else:
        lines.append("  (single source)")
    lines.append("")
    lines.append("Per-fling-window breakdown:")
    for w in report.windows:
        lines.append(
            f"  window {w.index}: frames={w.frame_count} "
            f"dropped={w.dropped} janky={w.janky} fps={w.fps:.1f}"
        )
    return "\n".join(lines)


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Compute FPS/jank from a Perfetto trace.")
    p.add_argument("trace", help="Path to .perfetto-trace")
    p.add_argument("--swipe-log", help="Optional file of 'start_ns end_ns' per line")
    args = p.parse_args(argv)

    swipe_log = None
    if args.swipe_log:
        with open(args.swipe_log) as f:
            swipe_log = [
                tuple(int(x) for x in line.split())
                for line in f if line.strip()
            ]

    report = analyze_trace(args.trace, swipe_log=swipe_log)
    out = format_report(report, args.trace)
    print(out)
    report_path = args.trace + ".fps_report.txt"
    with open(report_path, "w") as f:
        f.write(out + "\n")
    print(f"\nSaved: {report_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Compute FPS and dropped-frame stats from a Perfetto trace.

Three layers:
  1. Pure math: Frame/FlingWindow/BufferEvent dataclasses, compute_fps_from_frames(),
     per-source aggregation, and single-buffer overwrite detection. Fully
     unit-tested with synthetic data (no trace_processor needed).
  2. Gesture extraction: pair touch DOWN/UP events into Gestures, each split into
     three measurement phases — overall (DOWN→next DOWN), press (DOWN→UP), fling
     (UP→next DOWN) — and compute a full FpsReport per phase (ThreePhaseReport).
  3. trace_processor integration: load a real .perfetto-trace, derive gestures,
     gather frames from FrameTimeline (actual_frame_timeline_slice, grouped by
     layer_name → one source per surface), call the three-phase math.

Gesture / window model (revised from the original fling-only definition):
  - A Gesture is one physical touch: DOWN (press start) → UP (press end / fling
    start) → end (next DOWN, or trace end for the last gesture).
  - Each gesture yields THREE FPS numbers by phase:
      overall = [down, end)  the whole interaction (press + fling combined)
      press   = [down, up)   finger-contact period (drag/press responsiveness)
      fling   = [up,   end)  finger-up inertia period (fling smoothness)
    Reporting all three lets a single run diagnose drag perf, fling perf, and
    overall smoothness separately.
  - Gesture sources, 3-tier fallback (first non-empty wins):
      (1) structured android_input_events.event_action with COALESCE(event_time,
          dispatch_ts) — event_time is NULL on API 36 user builds, so dispatch_ts
          is the reliable timestamp.
      (2) atrace 'input' slices dispatchInputEvent/publishMotionEvent ACTION_DOWN/UP
          (works on user builds; confirmed present on 6 real-device traces).
      (3) device-clock swipe markers from run_fps_test.sh (coarse; no UP marker →
          press phase unavailable, only overall/fling reported).

Frame sources & quality signals:
  - multi-source FPS = FrameTimeline grouped by layer_name. layer_name IS NULL →
    bucketed as source "display" (SF output refreshes). frame_slice is empty on
    every tested device, so there is no separate BufferQueue path.
  - drops = present_type='Dropped Frame'. jank = jank_type != None and not dropped.
    (BufferQueue slices lack layer attribution here, so single-buffer overwrite
    detection is NOT wired into analyze_trace; detect_overwrite_drops /
    buffer_events_to_frames remain as a tested library for traces that expose
    layer-attributed buffer events.)
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 9):
    sys.exit(
        "compute_fps.py requires Python 3.9+ "
        f"(running {sys.version.split()[0]}). "
        "Uses str.removesuffix() and dataclass features added in 3.9."
    )

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
    """A time range [start_ns, end_ns) over which to measure FPS. The pure-math
    layer only cares about the half-open interval; the semantic meaning
    (press / fling / overall) is decided by how the window was derived, not
    stored here. See Gesture for the structured gesture → three-phase mapping."""
    start_ns: int
    end_ns: int


# The three measurement phases reported per gesture. Stable identifiers used
# both internally (compute_fps_three_phase) and in the human-readable report.
THREE_PHASES = ("overall", "press", "fling")


@dataclass
class Gesture:
    """One touch gesture's three timestamps, defining three FPS phases:

      press   = [down_ns, up_ns)    finger down → finger up (drag/press period)
      fling   = [up_ns,   end_ns)   finger up → next press (inertia period)
      overall = [down_ns, end_ns)   the whole interaction (press + fling)

    end_ns is the NEXT gesture's DOWN, or the trace end for the last gesture.
    A Gesture built from a device-clock swipe-log pair (tier-3) has no UP
    marker → down_ns == up_ns, so the press phase degenerates and is dropped
    (see gesture_windows).
    """
    down_ns: int
    up_ns: int
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


@dataclass
class ThreePhaseReport:
    """FPS for the three measurement phases of the captured gestures.

    Each phase is a full FpsReport (its own screen FPS, frame/drop/jank counts,
    per-source + per-window breakdown):
      - overall : [DOWN→next DOWN] the whole interaction (press + fling)
      - press   : [DOWN→UP] finger-contact period (drag/press responsiveness)
      - fling   : [UP→next DOWN] finger-up inertia period (fling smoothness)

    `press` may be None when there is no UP marker in the source (tier-3
    device-clock swipe markers carry only start/end, no UP). `source` records
    which tier produced the gestures, for the report header.
    """
    overall: FpsReport
    press: FpsReport          # None when source has no UP marker (tier-3)
    fling: FpsReport
    source: str = ""
    n_gestures: int = 0


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


def compute_fps_three_phase(gestures, frames, source="", has_press=True):
    """Three-phase FPS over a list of Gestures.

    Splits each gesture into overall / press / fling windows (gesture_windows)
    and computes a full FpsReport per phase. `has_press=False` (tier-3 device-
    clock markers have no UP) skips the press phase → press is None.

    This is the layer above compute_fps_from_frames: it only decides WHICH
    windows to feed (the three phases), leaving the math unchanged.
    """
    overall_w, press_w, fling_w = gesture_windows(gestures)
    return ThreePhaseReport(
        overall=compute_fps_from_frames(frames, overall_w),
        press=compute_fps_from_frames(frames, press_w) if has_press else None,
        fling=compute_fps_from_frames(frames, fling_w),
        source=source,
        n_gestures=len(gestures),
    )


# ---------------------------------------------------------------------------
# trace_processor integration (only imported when analyzing a real trace)
# ---------------------------------------------------------------------------

def _trace_end_ns(tp):
    """Last timestamp in the trace, in trace-internal clock (boottime ns).

    Used as the fling tail of the final gesture (whose UP has no following
    DOWN). Sourced from actual_frame_timeline_slice — the same data the FPS
    math consumes — so the boundary matches the frames under analysis.
    Falls back to the generic `slice` table if FrameTimeline is empty.
    """
    for table in ("actual_frame_timeline_slice", "slice"):
        try:
            r = tp.query(f"SELECT MAX(ts) AS m FROM {table}").as_pandas_dataframe()
            m = int(r['m'][0])
            if m > 0:
                return m
        except Exception:
            continue
    return 0


def _gestures_from_swipe_log(swipe_log):
    """Tier-3: device-clock swipe markers → Gestures with no UP marker.

    Each log entry is a (start_ns, end_ns) device-REALTIME pair recorded by
    run_fps_test.sh. There is no mid-window UP, so down_ns==up_ns and the press
    phase is degenerate (dropped by gesture_windows); the whole span is treated
    as the fling/overall phase. Frames must be queried in realtime (see
    analyze_trace) so they share this clock.
    """
    return [Gesture(down_ns=s, up_ns=s, end_ns=e) for s, e in swipe_log]


def extract_gestures(tp, swipe_log=None):
    """Three-tier gesture extraction. Returns (gestures, source, realtime).

    Tiers (first non-empty wins):
      1. structured android_input_events (COALESCE(event_time, ts))
      2. atrace dispatchInputEvent / publishMotionEvent ACTION slices
      3. device-clock swipe_log markers (realtime, no UP → press phase absent)
    Tiers 1–2 use trace-internal boottime ns; tier 3 uses device realtime, so
    `realtime` tells the caller to convert frame ts via TO_REALTIME().

    Raises RuntimeError if all three tiers are empty.
    """
    trace_end_ns = _trace_end_ns(tp)
    # tier 1
    gestures = _extract_fling_windows_structured(tp, trace_end_ns)
    if gestures:
        return gestures, "structured input", False
    # tier 2
    gestures = _extract_fling_windows_atrace(tp, trace_end_ns)
    if gestures:
        return gestures, "atrace ACTION slices", False
    # tier 3
    if swipe_log:
        print("[fps] WARNING: no input events in trace; using device-clock swipe "
              "markers (tier-3, less precise; press-phase unavailable).", flush=True)
        return _gestures_from_swipe_log(swipe_log), "device-clock swipe markers", True
    raise RuntimeError(
        "No fling windows found. Structured input and atrace ACTION slices are "
        "both absent (is the 'input' atrace category in the config?), and no "
        "--swipe-log fallback was provided."
    )


def analyze_trace(trace_path, swipe_log=None):
    """Load a trace and return a ThreePhaseReport.

    Gestures: tier-1 structured input → tier-2 atrace ACTION slices → tier-3
    device-clock swipe markers (see extract_gestures). Each gesture is split
    into overall / press / fling phases; FPS is computed per phase. Frames
    always come from FrameTimeline's actual_frame_timeline_slice, grouped by
    layer_name.

    swipe_log: optional list of (start_ns, end_ns) device-realtime tuples (tier-3).
    """
    from perfetto.trace_processor import TraceProcessor

    tp = TraceProcessor(trace=str(trace_path))
    gestures, source, realtime = extract_gestures(tp, swipe_log=swipe_log)
    print(f"[fps] gestures from {source} ({len(gestures)} gesture(s)).", flush=True)

    frames = _query_frames(tp, realtime=realtime)
    if not frames:
        raise RuntimeError(
            "No FrameTimeline frames in trace. Needs Android 12+ and the "
            "android.surfaceflinger.frametimeline data source in 02_jank_frame.pbtx."
        )
    # tier-3 has no UP marker → press phase is unavailable.
    has_press = source != "device-clock swipe markers"
    return compute_fps_three_phase(gestures, frames, source=source, has_press=has_press)


def _extract_fling_windows_structured(tp, trace_end_ns):
    """Tier 1: gestures from structured android_input_events.

    Returns list[Gesture], or [] to fall through to tier 2.

    Uses `COALESCE(event_time, dispatch_ts)` for the timestamp: on API 36 (and
    other builds) `event_time` is NULL for every row, so we fall back to
    `dispatch_ts` (when the event was dispatched to the app — always populated).
    The old code used `event_time` directly → all-NULL → empty windows even
    when input data was present. (`ts` is not a column of this view.)

    Only DOWN/UP actions participate (MOVE is ignored). events_action may be
    NULL on some builds; those rows are filtered out.
    """
    try:
        qr = tp.query("""
            INCLUDE PERFETTO MODULE android.input;
            SELECT COALESCE(event_time, dispatch_ts) AS t, event_action
            FROM android_input_events
            WHERE event_action IS NOT NULL
            ORDER BY t
        """)
        events = []
        for row in qr:
            action = (row.event_action or "").upper()
            if "UP" in action:
                events.append((row.t, True))
            elif "DOWN" in action:
                events.append((row.t, False))
        events = _dedup_action_timestamps(events)
        return _pair_actions_to_gestures(events, trace_end_ns)
    except Exception as e:
        print(f"[fps] structured input query failed ({e}); trying atrace tier.", flush=True)
        return []


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


def _pair_actions_to_gestures(events, trace_end_ns):
    """Pair de-duped ACTION events into Gestures.

    `events`: sorted list of (ts, is_up) already passed through
    _dedup_action_timestamps (one DOWN/UP per gesture).
    `trace_end_ns`: the timestamp of the last frame / end of trace, used as the
    fling tail of the final gesture (whose UP has no following DOWN).

    Each DOWN is paired with the next UP after it to form the press phase; the
    fling phase runs from that UP to the next gesture's DOWN (or trace_end_ns
    for the last gesture). A DOWN with no following UP is truncated and skipped.
    A Gesture's press phase is [down, up); its fling phase is [up, end).

    This replaces the old UP→next-DOWN-only pairing, which produced ZERO windows
    for a single-gesture trace (the UP had no following DOWN) and dropped the
    last gesture's inertia tail on multi-gesture traces.
    """
    # Collect (down, up) pairs in order: a DOWN followed by the next UP.
    pairs = []
    i = 0
    n = len(events)
    while i < n:
        if events[i][1]:                # orphan UP → skip
            i += 1
            continue
        down = events[i][0]
        # find the matching UP: first UP strictly after this DOWN.
        up = None
        for k in range(i + 1, n):
            if events[k][1]:
                up = events[k][0]
                break
        if up is None:                  # DOWN with no UP (truncated) → stop
            break
        pairs.append((down, up))
        i = k + 1
    # Each pair's end = next pair's DOWN, or trace_end for the last.
    gestures = []
    for idx, (down, up) in enumerate(pairs):
        end = pairs[idx + 1][0] if idx + 1 < len(pairs) else trace_end_ns
        gestures.append(Gesture(down_ns=down, up_ns=up, end_ns=end))
    return gestures


def gesture_windows(gestures):
    """Derive the three measurement-phase FlingWindow lists from Gestures.

    Returns (overall_windows, press_windows, fling_windows):
      - overall: [down, end) for every gesture
      - press  : [down, up)  for every gesture with a real UP marker
      - fling  : [up,   end) for every gesture; for swipe-log Gestures with no
                 UP marker (down==up), the whole [down, end) is treated as the
                 fling phase instead of being lost.
    Zero-width windows (start == end) are dropped to avoid divide-by-zero.
    """
    overall, press, fling = [], [], []
    for g in gestures:
        if g.end_ns > g.down_ns:
            overall.append(FlingWindow(g.down_ns, g.end_ns))
        if g.up_ns > g.down_ns:
            press.append(FlingWindow(g.down_ns, g.up_ns))
            if g.end_ns > g.up_ns:
                fling.append(FlingWindow(g.up_ns, g.end_ns))
        else:
            # no UP marker (tier-3 swipe log) → whole span is fling
            if g.end_ns > g.down_ns:
                fling.append(FlingWindow(g.down_ns, g.end_ns))
    return overall, press, fling


def _extract_fling_windows_atrace(tp, trace_end_ns):
    """Tier 2: gestures from the 'input' atrace category's slices.

    Returns list[Gesture], or [] to fall through to tier 3.

    Primary source: `dispatchInputEvent MotionEvent ACTION_DOWN/UP`. Fallback
    (if absent): `publishMotionEvent ... action=DOWN/UP`. Both carry the same
    gesture timing (verified identical across 6 real-device traces), so either
    delimits gestures correctly.

    One physical gesture fires several slices (one per input channel), so we
    de-dup bursts first, then pair DOWN→UP→next-DOWN into Gestures (see
    _pair_actions_to_gestures for why this supersedes the old UP→next-DOWN-only
    pairing that dropped single-gesture traces).
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
        return _pair_actions_to_gestures(events, trace_end_ns)
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


def _format_phase(report: FpsReport, indent="") -> list:
    """Format one phase's FpsReport as report lines (no header)."""
    L = []
    L.append(f"{indent}Screen FPS (display/SF output): {report.overall_fps:.1f}")
    L.append(f"{indent}Total frames (all sources): {report.total_frames}")
    L.append(f"{indent}  presented       : {report.presented_frames}")
    L.append(f"{indent}  dropped         : {report.dropped_frames}  (never on screen)")
    L.append(f"{indent}  janky           : {report.janky_frames}  (presented late)")
    L.append(f"{indent}Drop rate         : {report.drop_rate:.2f}%")
    L.append(f"{indent}Per-frame-source breakdown:")
    if report.by_source:
        for s in report.by_source:
            L.append(
                f"{indent}  {s.source:<40} fps={s.fps:6.1f} frames={s.frame_count} "
                f"presented={s.presented} dropped={s.dropped} janky={s.janky}"
            )
    else:
        L.append(f"{indent}  (single source)")
    return L


def format_report(report, trace_path: str) -> str:
    """Format a ThreePhaseReport (or a legacy single FpsReport) as text."""
    # Legacy single-phase report (e.g. from compute_fps_from_frames directly).
    if isinstance(report, FpsReport):
        return "\n".join(_format_phase(report))

    lines = []
    lines.append(f"=== FPS Report: {trace_path} ===")
    lines.append(f"[window source: {report.source}, {report.n_gestures} gesture(s)]")
    lines.append("")

    lines.append("-- Overall (press + fling: DOWN -> next DOWN) --")
    lines.extend(_format_phase(report.overall))
    lines.append("")

    if report.press is not None:
        lines.append("-- Press phase (finger down -> up) --")
        lines.extend(_format_phase(report.press))
        lines.append("")
    else:
        lines.append("-- Press phase: N/A (source has no UP marker, e.g. tier-3 swipe log) --")
        lines.append("")

    lines.append("-- Fling phase (finger up -> next press) --")
    lines.extend(_format_phase(report.fling))
    lines.append("")

    lines.append("Per-gesture Screen-FPS (overall / press / fling):")
    for i in range(len(report.overall.windows)):
        o = report.overall.windows[i].fps
        p = report.press.windows[i].fps if (report.press and i < len(report.press.windows)) else float("nan")
        f = report.fling.windows[i].fps if i < len(report.fling.windows) else float("nan")
        lines.append(f"  gesture {i}: overall={o:.1f}  press={p:.1f}  fling={f:.1f}")
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

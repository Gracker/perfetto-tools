#!/usr/bin/env bash
# Auxiliary FPS cross-check via dumpsys. Independent of the Perfetto trace.
#
# Usage:
#   dump_gfxinfo.sh reset <package>             # before the test
#   dump_gfxinfo.sh dump  <package> [out_dir]   # after the test
#
# 'reset' zeroes gfxinfo + SurfaceFlinger latency counters.
# 'dump'  writes gfxinfo framestats and per-layer SurfaceFlinger latency to files.
# These corroborate (not replace) the trace's per-source FPS: gfxinfo is whole-
# process, SurfaceFlinger --latency is per-layer, the trace is per-source.
#
# Note: on Android 14+ (API 34+) SurfaceFlinger --latency no longer emits per-frame
# rows (only a single vsync-interval line). On those versions the sflatency file
# carries a notice and points to the trace's FrameTimeline for per-layer timing.
set -euo pipefail

MODE="${1:?Usage: $0 reset|dump <package> [out_dir]}"
PKG="${2:?package name required}"
OUT_DIR="${3:-./traces}"

# Locate adb via tools/resolve.sh (env override > .bin/ > PATH).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ADB="$("${REPO_ROOT}/tools/resolve.sh" adb)"

case "${MODE}" in
  reset)
    # gfxinfo per-app reset; SF latency is global clear.
    "$ADB" shell dumpsys gfxinfo "${PKG}" reset >/dev/null 2>&1 || true
    "$ADB" shell dumpsys SurfaceFlinger --latency-clear >/dev/null 2>&1 || true
    echo "[gfxinfo] reset counters for ${PKG}"
    ;;
  dump)
    mkdir -p "${OUT_DIR}"
    TS="$(date +%Y%m%d_%H%M%S)"
    GFX="${OUT_DIR}/gfxinfo_${PKG}_${TS}.txt"
    SF="${OUT_DIR}/sflatency_${PKG}_${TS}.txt"

    # 1. Whole-process frame stats (Total frames, Janky frames, percentiles, CSV).
    #    Works on all Android versions with a gfxinfo-supporting app.
    "$ADB" shell dumpsys gfxinfo "${PKG}" framestats > "${GFX}"
    echo "[gfxinfo] framestats -> ${GFX}"

    # 2. Per-layer present timestamps via SurfaceFlinger --latency.
    #    On older Android this emits 3-column (desired, actual-present,
    #    frame-ready) ns rows per layer. On API 34+ it only prints a single
    #    vsync-interval number, so we detect that and write a notice instead of a
    #    misleading near-empty file.
    LAYERS="$("$ADB" shell dumpsys SurfaceFlinger --list </dev/null | tr -d '\r' | grep -F "${PKG}" || true)"
    : > "${SF}"
    if [[ -z "${LAYERS}" ]]; then
      echo "[gfxinfo] no SurfaceFlinger layers matched ${PKG}; skipping --latency" >&2
      echo "# No SurfaceFlinger layers matched ${PKG} at dump time." >> "${SF}"
    else
      while IFS= read -r layer; do
        [[ -z "${layer}" ]] && continue
        # Capture this layer's latency output, then decide if it's meaningful.
        LAT_OUT="$("$ADB" shell dumpsys SurfaceFlinger --latency "${layer}" </dev/null | tr -d '\r')"
        # Count non-empty rows. A single vsync line (just a number) means the new
        # API shape with no per-frame data.
        ROW_COUNT=$(printf '%s\n' "${LAT_OUT}" | grep -c '[0-9]')
        {
          echo "=== layer: ${layer} ==="
          if [[ "${ROW_COUNT}" -le 1 ]]; then
            echo "# (no per-frame rows on this Android version; --latency only"
            echo "#  returns a vsync-interval here. For per-layer present timing,"
            echo "#  read actual_frame_timeline_slice in the Perfetto trace.)"
            echo "${LAT_OUT}"
          else
            echo "${LAT_OUT}"
          fi
          echo ""
        } >> "${SF}"
      done <<< "${LAYERS}"
      echo "[gfxinfo] SurfaceFlinger latency -> ${SF}"
    fi
    ;;
  *)
    echo "ERROR: unknown mode '${MODE}' (use reset|dump)" >&2
    exit 2
    ;;
esac

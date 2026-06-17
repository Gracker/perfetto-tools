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
    "$ADB" shell dumpsys gfxinfo "${PKG}" framestats > "${GFX}"
    echo "[gfxinfo] framestats -> ${GFX}"

    # 2. Per-layer present timestamps. Layer names look like
    #    'SurfaceView[pkg/Activity]#0' or 'pkg/Activity#0'. dumpsys SurfaceFlinger
    #    --list enumerates them; --latency <layer> prints 3-column
    #    (desired, actual-present, frame-ready) ns rows for that layer.
    LAYERS="$("$ADB" shell dumpsys SurfaceFlinger --list | tr -d '\r' | grep -F "${PKG}" || true)"
    if [[ -z "${LAYERS}" ]]; then
      echo "[gfxinfo] no SurfaceFlinger layers matched ${PKG}; skipping --latency" >&2
    else
      : > "${SF}"
      while IFS= read -r layer; do
        [[ -z "${layer}" ]] && continue
        {
          echo "=== layer: ${layer} ==="
          "$ADB" shell dumpsys SurfaceFlinger --latency "${layer}"
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

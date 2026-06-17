#!/usr/bin/env bash
# Resolve the path to a host tool (currently: adb) without assuming it's on PATH.
#
# Lookup order:
#   1. $PERFETTO_TOOLS_ADB  (explicit override)
#   2. .bin/adb             (created by tools/setup.sh)
#   3. `adb` on PATH        (user's own install)
#
# Usage from sibling scripts:
#   ADB="$(dirname "$0")/../tools/resolve.sh adb)"
#   "$ADB" shell ...
#
# Exits 1 (with guidance) if nothing is found.
set -euo pipefail

TOOL="${1:?usage: resolve.sh <tool>}"

case "${TOOL}" in
  adb)
    # 1. Explicit override.
    if [[ -n "${PERFETTO_TOOLS_ADB:-}" ]] && [[ -x "${PERFETTO_TOOLS_ADB}" ]]; then
      echo "${PERFETTO_TOOLS_ADB}"; exit 0
    fi
    # 2. setup.sh-managed copy.
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    BIN_ADB="${SCRIPT_DIR}/../.bin/adb"
    if [[ -x "${BIN_ADB}" ]]; then
      echo "${BIN_ADB}"; exit 0
    fi
    # 3. PATH.
    if command -v adb >/dev/null 2>&1; then
      command -v adb; exit 0
    fi
    echo "ERROR: adb not found. Run './tools/setup.sh' to install it," >&2
    echo "       set PERFETTO_TOOLS_ADB=/path/to/adb, or put adb on PATH." >&2
    exit 1
    ;;
  *)
    echo "ERROR: resolve.sh only knows 'adb' (got '${TOOL}')." >&2
    exit 2
    ;;
esac

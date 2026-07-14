#!/usr/bin/env bash
# Resolve the path to a host tool without assuming the first PATH entry works.
#
# Lookup order:
#   1. $PERFETTO_TOOLS_ADB  (explicit override)
#   2. .bin/adb             (created by tools/setup.sh)
#   3. `adb` on PATH        (user's own install)
#
# Usage from sibling scripts:
#   ADB="$(dirname "$0")/../tools/resolve.sh adb)"
#   PYTHON="$(dirname "$0")/../tools/resolve.sh python)"
#   "$ADB" shell ...
#
# Exits 1 (with guidance) if nothing is found.
set -euo pipefail

TOOL="${1:?usage: resolve.sh <tool>}"

python_is_usable() {
  local candidate="$1"
  [[ -x "${candidate}" ]] && "${candidate}" -c \
    'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 15) else 1)' \
    >/dev/null 2>&1
}

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
  python)
    # An explicit override is authoritative: fail instead of silently using a
    # different interpreter when it is missing, broken, or too old.
    if [[ -n "${PERFETTO_TOOLS_PYTHON:-}" ]]; then
      if python_is_usable "${PERFETTO_TOOLS_PYTHON}"; then
        echo "${PERFETTO_TOOLS_PYTHON}"
        exit 0
      fi
      echo "ERROR: PERFETTO_TOOLS_PYTHON must run Python 3.10-3.14:" >&2
      echo "       ${PERFETTO_TOOLS_PYTHON}" >&2
      exit 1
    fi

    # Prefer a project-local environment when present.
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    VENV_PYTHON="${SCRIPT_DIR}/../.venv/bin/python"
    if python_is_usable "${VENV_PYTHON}"; then
      echo "${VENV_PYTHON}"
      exit 0
    fi

    # `command -v python3` only returns the first match. Probe every PATH match
    # so a broken installation cannot hide a later healthy interpreter.
    seen=":"
    while IFS= read -r candidate; do
      [[ -z "${candidate}" ]] && continue
      case "${seen}" in
        *":${candidate}:"*) continue ;;
      esac
      seen="${seen}${candidate}:"
      if python_is_usable "${candidate}"; then
        echo "${candidate}"
        exit 0
      fi
    done < <(type -aP python3 2>/dev/null; type -aP python 2>/dev/null)

    echo "ERROR: no working Python 3.10-3.14 interpreter found." >&2
    echo "       Run './tools/setup.sh', or set" >&2
    echo "       PERFETTO_TOOLS_PYTHON=/path/to/python." >&2
    exit 1
    ;;
  *)
    echo "ERROR: resolve.sh only knows 'adb' and 'python' (got '${TOOL}')." >&2
    exit 2
    ;;
esac

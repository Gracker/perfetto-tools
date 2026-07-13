#!/usr/bin/env bash
# Mac / Linux entry: forwards all args to perfetto_capture.py.
# Resolve repo root relative to this script so it works from any CWD.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="$("${REPO_ROOT}/tools/resolve.sh" python)"
exec "${PYTHON}" "${SCRIPT_DIR}/perfetto_capture.py" "$@"

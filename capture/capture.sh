#!/usr/bin/env bash
# Mac / Linux entry: forwards all args to perfetto_capture.py.
# Resolve repo root relative to this script so it works from any CWD.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/perfetto_capture.py" "$@"

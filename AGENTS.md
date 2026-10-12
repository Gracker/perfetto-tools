# Perfetto Tools maintainer guide

- Read the current checkout, relevant source and tests before editing. Preserve
  unrelated work; scope commits to the requested change. Do not use brainstorming.
- Keep capture/config parsing, FPS math, host bootstrap and upstream artifacts in
  their existing modules. Avoid unrelated refactors and duplicated manifests.
- Official helper/binary bytes and versions are pinned by `official/VERSION`,
  `tools/tool-versions.env`, `tools/sha256.txt`, `pyproject.toml` and `uv.lock`.
  Do not change pins or regenerate/download artifacts for ordinary source work.
- For nontrivial work, state the files, order and risks; implement, review the diff
  for simplification, run relevant repository checks, then repair confirmed issues.
- Once setup exists, run `.venv/bin/python tools/verify.py` before landing source
  changes (Windows: `.\.venv\Scripts\python.exe tools\verify.py`). It runs the full
  Python suite and Unix tracked-shell checks. Do not add unrelated project gates.
- Setup/doctor are separate for runtime or pin changes; connected-device smoke is
  separate for capture changes. Run upstream update queries only for maintenance.
  See `tools/README.md`; missing prerequisites are `NOT AVAILABLE`, never a pass.
- Keep device authorization, API/ABI support and offline verified-tool selection
  explicit. Preserve FPS per-source/press/fling meanings in `README.md` and the
  support boundary in `docs/compatibility.md`.
- Distinguish edits, local tests, device evidence, commits and pushes. Review
  public capture/runtime contract changes independently before landing.

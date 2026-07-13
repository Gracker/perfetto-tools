# Perfetto Tools Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update Perfetto Tools to the current verified toolchain and provide a supported Perfetto replacement for legacy Systrace capture commands.

**Architecture:** Keep preset pbtxt capture and add a separate lightweight category mode in the same Python core. Align all Perfetto components at v57.2, fail closed on Platform-Tools integrity errors, and make version/resolver contracts executable through tests.

**Tech Stack:** Python 3.9+, Bash, Windows batch, Perfetto 0.57.2/v57.2, Android Platform-Tools 37.0.0, pytest, ShellCheck, GitHub Actions.

## Global Constraints

- Preserve all existing preset configs and FPS calculation semantics.
- Do not ship or call obsolete `systrace.py`; keep supported `atrace` categories inside Perfetto.
- Normal capture and analysis must continue to use pinned local artifacts without runtime binary downloads.
- A downloaded archive with a wrong checksum must never be installed.
- Work only in files owned by this repository and keep the final commit scoped to this modernization.

---

### Task 1: Capture-mode regression tests

**Files:**
- Create: `tests/test_capture_modes.py`
- Modify: `capture/perfetto_capture.py`

**Interfaces:**
- Consumes: existing `build_parser()`, `ConfigError`, and official-helper path.
- Produces: `normalize_lightweight_duration(value: str) -> str` and `build_official_command(args, output: str, config_path: str | None = None) -> list[str]`.

- [x] **Step 1: Write failing tests** for numeric/unit duration normalization,
  invalid duration rejection, mutually exclusive modes, repeated `--app`, buffer
  forwarding, category forwarding, and preset-config command preservation.
- [x] **Step 2: Run** `python -m pytest tests/test_capture_modes.py -v` and confirm
  collection/import fails because the new helpers do not exist.
- [x] **Step 3: Implement the minimal capture split** so config mode builds
  `record_android_trace -c <config> -o <out>`, while category mode builds
  `record_android_trace -o <out> -t <duration> -b <buffer> -a <app> <categories>`.
- [x] **Step 4: Run the focused test** and confirm it passes.

### Task 2: Working Python resolver

**Files:**
- Create: `tests/test_python_resolver.py`
- Modify: `tools/resolve.sh`
- Modify: `capture/capture.sh`
- Modify: `capture/capture.bat`
- Modify: `fps-test/run_fps_test.sh`

**Interfaces:**
- Consumes: `PERFETTO_TOOLS_PYTHON`, repository `.venv`, and `PATH` candidates.
- Produces: `tools/resolve.sh python`, printing one executable Python 3.9+ path.

- [x] **Step 1: Write failing subprocess tests** with fake PATH candidates that
  prove an invalid explicit override fails and a broken first `python3` falls
  through to a healthy later candidate.
- [x] **Step 2: Run** `python -m pytest tests/test_python_resolver.py -v` and
  confirm `resolve.sh` rejects the unknown `python` tool.
- [x] **Step 3: Implement resolver probing** and wire all Unix Python calls to the
  resolved interpreter; add equivalent Windows fallback ordering.
- [x] **Step 4: Run the focused resolver tests and ShellCheck** on modified scripts.

### Task 3: Verified upstream tool refresh

**Files:**
- Modify: `official/record_android_trace`
- Modify: `official/VERSION`
- Modify: `tools/trace_processor_shell/*`
- Modify: `tools/sha256.txt`
- Modify: `tools/setup.sh`
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `tests/test_tool_pins.py`

**Interfaces:**
- Consumes: pinned Perfetto `main` commit, v57.2 artifact manifests, and official
  Google Platform-Tools 37.0.0 archives.
- Produces: internally aligned v57.2 tools and fail-closed Platform-Tools setup.

- [x] **Step 1: Write failing pin-integrity tests** for metadata, embedded
  `record_android_trace` version, requirements pin, and all shipped SHA256 values.
- [x] **Step 2: Run** `python -m pytest tests/test_tool_pins.py -v` and confirm the
  old v49.0 artifacts fail the v57.2 contract.
- [x] **Step 3: Fetch the official script at inspected commit `4f2c163…`**, download all five
  v57.2 trace processors from Google artifacts, and update metadata/checksums.
- [x] **Step 4: Update Platform-Tools to 37.0.0** with verified Darwin/Linux
  hashes and make mismatches fatal.
- [x] **Step 5: Run pin tests, `./tools/setup.sh`, and host `--version` smoke checks.**

### Task 4: Systrace migration and current documentation

**Files:**
- Create: `docs/systrace-migration.md`
- Modify: `README.md`
- Modify: `capture/README.md`
- Modify: `official/README.md`
- Modify: `tools/README.md`
- Modify: `fps-test/README.md`

**Interfaces:**
- Consumes: the two capture modes and pinned tool versions.
- Produces: one authoritative migration path from obsolete Systrace terminology
  and commands to Perfetto UI, preset capture, or lightweight category capture.

- [x] **Step 1: Document command mappings**, Android-version boundaries, the
  distinction between obsolete Systrace and supported atrace, and modern UI/CLI
  alternatives.
- [x] **Step 2: Update every version/install reference** to requirements files,
  Perfetto v57.2, Platform-Tools 37.0.0, and the `main` branch update source.
- [x] **Step 3: Scan** with `rg -n "master|v49\\.0|35\\.0\\.2|systrace"` and ensure
  remaining historical terms are intentional and linked to the migration guide.

### Task 5: Continuous verification and release

**Files:**
- Create: `.github/workflows/verify.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `requirements-dev.txt`, Python tests, shell entrypoints, and tool-pin checks.
- Produces: repeatable pull/push verification on GitHub and a verified release commit.

- [x] **Step 1: Add CI** that installs the pinned development requirements, runs
  the full test suite, verifies shell syntax, and runs ShellCheck.
- [x] **Step 2: Run the repository-defined verification locally:** full pytest,
  `bash -n`, ShellCheck, setup/checksum smoke, CLI help/list smoke, and
  `git diff --check`.
- [x] **Step 3: Perform the required simplification review.** If `/simplify`, a
  repository simplifier, and `code-simplifier` are unavailable, manually review
  only changed code and record that fallback.
- [x] **Step 4: Review the complete diff against the design**, stage explicit
paths, commit tersely, push `main`, and verify `origin/main` equals local HEAD.

## Execution Record

- Regression development followed red/green TDD for capture modes, Python
  resolution, tool pins, and the official-helper ADB environment boundary.
- Final local verification passed with 71 tests on Python 3.9.6, `bash -n`,
  ShellCheck, all five bundled binary checksums, `tools/setup.sh`, host
  `trace_processor_shell --version`, CLI help/config listing, upstream pin/hash
  checks, workflow YAML parsing, and `git diff --check`.
- `/simplify`, a repository simplifier, and `code-simplifier` were unavailable;
  a manual behavior-preserving simplification and architecture review was used.
- A physical-device capture was not available because no Android device was
  connected; no-device and pre-device validation paths were exercised instead.

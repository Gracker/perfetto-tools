# Perfetto Tools Runtime Compatibility Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Perfetto Tools reproducible on supported hosts, enforce Android/device compatibility before capture, remove normal run-time downloads, and prove the contract with cross-platform automation.

**Architecture:** Minimal shell/PowerShell bootstraps download a verified uv binary and use it to create a repository-owned Python 3.13.14 environment from `uv.lock`. A shared `perfetto_tools.runtime` module owns ADB/device/platform behavior for capture and doctor commands; bundled Android tracebox artifacts cover legacy devices. CI validates the same boundary on Linux, macOS, and Windows, while mutable upstream drift is checked separately.

**Tech Stack:** CPython 3.13.14 managed by uv 0.11.28, Python standard library, Bash, PowerShell, Perfetto v57.2, Platform-Tools 37.0.0, pytest 9.1.1, GitHub Actions.

## Global Constraints

- Do not reintroduce or emulate `systrace.py`; Perfetto lightweight categories remain the migration path.
- Managed setup must not modify global Python packages, shell profiles, or SDK installations.
- Fully supported self-contained hosts are macOS arm64/x86_64, Linux glibc x86_64, and Windows x86_64.
- Linux glibc arm64 analysis is supported, but capture requires an explicit external ADB because Google does not ship a Linux ARM64 Platform-Tools archive.
- External Python overrides must be CPython 3.10-3.14; managed setup uses exactly CPython 3.13.14.
- Android API 23-28 uses bundled tracebox, API 29 uses tracebox only when system tracing services are unavailable, and FrameTimeline/FPS requires API 31+.
- Downloaded uv and Platform-Tools archives must match pinned SHA256 values before extraction.
- Normal capture and analysis after setup must not download ADB, Python packages, trace processor, or tracebox.
- Physical-device verification must report `NOT AVAILABLE` when no authorized device exists; absence is never a pass.

---

### Task 1: Hermetic bootstrap and pinned dependency graph

**Files:**
- Create: `tools/tool-versions.env`
- Create: `pyproject.toml`
- Create: `uv.lock`
- Create: `tools/setup.ps1`
- Create: `tools/setup_runtime.py`
- Create: `tests/test_bootstrap.py`
- Modify: `tools/setup.sh`
- Modify: `tools/resolve.sh`
- Modify: `capture/capture.bat`
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: the host OS/architecture, OS-native HTTPS/archive tools, and `tools/tool-versions.env`.
- Produces: verified `.bin/uv/`, `.bin/python/`, `.bin/platform-tools/`, `.venv/`, and `setup_runtime.main(argv: list[str] | None) -> int`.

- [x] **Step 1: Write failing bootstrap contract tests**

```python
def test_bootstrap_manifest_pins_managed_runtime():
    values = read_env_manifest(REPO_ROOT / "tools/tool-versions.env")
    assert values["PYTHON_VERSION"] == "3.13.14"
    assert values["UV_VERSION"] == "0.11.28"
    assert values["PLATFORM_TOOLS_VERSION"] == "37.0.0"

def test_windows_setup_uses_versioned_win_archive():
    values = read_env_manifest(REPO_ROOT / "tools/tool-versions.env")
    assert values["PT_WINDOWS_URL"].endswith("platform-tools_r37.0.0-win.zip")
    assert values["PT_WINDOWS_SHA256"] == "4fe305812db074cea32903a489d061eb4454cbc90a49e8fea677f4b7af764918"

def test_default_setup_does_not_accept_path_adb():
    setup = (REPO_ROOT / "tools/setup_runtime.py").read_text()
    assert "shutil.which(\"adb\")" not in setup
```

- [x] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_bootstrap.py -v`

Expected: FAIL because the manifest, PowerShell bootstrap, and shared runtime setup do not exist.

- [x] **Step 3: Add the canonical bootstrap pins**

`tools/tool-versions.env` contains Python/uv/Platform-Tools versions, archive names, URLs, and the reviewed SHA256 values for macOS arm64/x86_64, Linux glibc arm64/x86_64, and Windows x86_64. Both bootstrap scripts read this file; neither duplicates hashes.

- [x] **Step 4: Create the locked Python project**

```toml
[project]
name = "perfetto-tools-local"
version = "0.1.0"
requires-python = ">=3.10,<3.15"
dependencies = [
  "perfetto==0.57.2",
  "protobuf==7.35.1",
]

[dependency-groups]
dev = ["pytest==9.1.1"]
```

Generate `uv.lock` with the pinned uv executable. Keep requirements files as exact pip-compatible projections of the same direct versions.

- [x] **Step 5: Implement minimal verified uv bootstraps**

`tools/setup.sh` detects the supported Unix host, downloads the matching uv archive, verifies it, extracts into `.bin/uv/`, sets repository-local uv cache/Python variables, and runs `uv sync --frozen --python 3.13.14`.

`tools/setup.ps1` performs the equivalent steps with `Invoke-WebRequest`, `Get-FileHash`, and `Expand-Archive` on Windows x86_64.

- [x] **Step 6: Implement shared post-bootstrap setup**

```python
def ensure_platform_tools(repo_root: Path, versions: dict[str, str]) -> Path:
    """Return explicit override or an exact repo-local Platform-Tools 37.0.0 adb."""

def verify_bundled_artifacts(repo_root: Path) -> list[str]:
    """Raise SetupError on any missing or mismatched checksum entry."""

def verify_python_environment(expected: dict[str, str]) -> None:
    """Require the managed Python and exact runtime package versions."""
```

Extraction occurs in a temporary sibling directory and replaces the managed directory only after checksum, archive layout, source revision, and `adb version` checks succeed.

- [x] **Step 7: Make runtime entrypoints prefer the managed environment**

Unix resolver order becomes explicit override → `.venv/bin/python` → healthy PATH fallback. Windows capture order becomes explicit override → `.venv\Scripts\python.exe` → `py -3` → `python`. A missing setup is diagnosed, not silently hidden.

- [x] **Step 8: Run focused tests and bootstrap smokes**

Run:

```bash
python -m pytest tests/test_bootstrap.py tests/test_python_resolver.py -v
bash -n tools/setup.sh tools/resolve.sh
shellcheck tools/setup.sh tools/resolve.sh
./tools/setup.sh
./.bin/uv/uv pip check --python .venv/bin/python
```

Expected: all tests/checks pass; setup reports Python 3.13.14, Perfetto 0.57.2, and repo-local ADB 37.0.0 on a fully supported host.

### Task 2: Shared ADB, device-state, and compatibility boundary

**Files:**
- Create: `perfetto_tools/__init__.py`
- Create: `perfetto_tools/runtime.py`
- Create: `tests/test_runtime.py`
- Modify: `capture/perfetto_capture.py`

**Interfaces:**
- Consumes: an ADB path, optional serial, command timeout, selected config/mode, and device command output.
- Produces: `AdbDevice`, `DeviceInfo`, `RuntimeFailure`, `list_adb_devices()`, `select_device()`, `probe_device()`, `tracebox_for_device()`, and `check_feature_compatibility()`.

- [x] **Step 1: Write failing device parser and failure tests**

```python
def test_parse_adb_devices_preserves_unauthorized_state():
    devices = parse_adb_devices("List of devices attached\nABC\tunauthorized usb:1-1\n")
    assert devices == [AdbDevice(serial="ABC", state="unauthorized", details="usb:1-1")]

def test_select_device_explains_offline_device():
    with pytest.raises(RuntimeFailure, match="offline.*reconnect") as exc:
        select_device([AdbDevice("ABC", "offline", "")])
    assert exc.value.exit_code == 4

def test_run_adb_converts_timeout_to_device_failure():
    with pytest.raises(RuntimeFailure, match="timed out") as exc:
        run_adb(["devices", "-l"], runner=timeout_runner, timeout=5)
    assert exc.value.exit_code == 4
```

- [x] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_runtime.py -v`

Expected: import failure because `perfetto_tools.runtime` does not exist.

- [x] **Step 3: Implement typed runtime errors and exact device parsing**

```python
@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    details: str

@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    api_level: int
    abi: str
    build_type: str
    traced_state: str

class RuntimeFailure(Exception):
    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code
```

Handle missing executable, permission failure, non-zero daemon result, malformed output, timeout, unauthorized, offline, no-permissions, zero devices, and multiple devices with separate messages.

- [x] **Step 4: Write failing Android compatibility tests**

```python
@pytest.mark.parametrize(("api", "abi", "expected"), [
    (23, "armeabi-v7a", "android-arm"),
    (28, "arm64-v8a", "android-arm64"),
    (29, "x86_64", None),
    (31, "arm64-v8a", None),
])
def test_tracebox_selection(api, abi, expected):
    info = DeviceInfo("A", api, abi, "user", "running")
    selected = tracebox_for_device(info, TRACEBOX_DIR)
    assert (selected.name if selected else None) == expected

def test_jank_rejected_before_android_12():
    with pytest.raises(RuntimeFailure, match="Android 12.*API 31") as exc:
        check_feature_compatibility("jank", DeviceInfo("A", 30, "arm64-v8a", "user", "running"))
    assert exc.value.exit_code == 5
```

- [x] **Step 5: Implement probe, tracebox selection, and feature gates**

Probe API, ABI, build type, and `init.svc.traced` in one bounded ADB shell call. API 23-28 selects bundled tracebox; API 29 selects it only when traced is not running. Unsupported ABI is an exit-5 failure. Jank/FPS below API 31 fails; full below API 31 returns a warning.

- [x] **Step 6: Delegate capture's ADB boundary to the shared module**

Remove the local `_resolve_adb` and substring-based device parser. Preserve `build_official_environment()` only as the bridge that exposes the selected ADB to the unmodified upstream helper.

- [x] **Step 7: Run focused and existing capture tests**

Run: `python -m pytest tests/test_runtime.py tests/test_capture_modes.py tests/test_config_resolver.py -v`

Expected: all pass with no uncaught subprocess exceptions.

### Task 3: Capture input hardening and bundled legacy tracebox

**Files:**
- Create: `tools/tracebox/android-arm`
- Create: `tools/tracebox/android-arm64`
- Create: `tools/tracebox/android-x86`
- Create: `tools/tracebox/android-x64`
- Modify: `capture/perfetto_capture.py`
- Modify: `tests/test_capture_modes.py`
- Modify: `tests/test_tool_pins.py`
- Modify: `tools/sha256.txt`
- Modify: `tools/.gitattributes`

**Interfaces:**
- Consumes: validated CLI arguments and `DeviceInfo` from Task 2.
- Produces: `normalize_buffer_size()`, `validate_category()`, `validate_app()`, `validate_output_path()`, and an upstream command containing `--sideload-path` when required.

- [x] **Step 1: Write failing argument-boundary tests**

```python
@pytest.mark.parametrize("value", ["", "32", "-1mb", "32kb", "nanmb"])
def test_buffer_rejects_invalid_values(value):
    with pytest.raises(ConfigError, match="positive.*mb or gb"):
        normalize_buffer_size(value)

def test_list_mode_rejects_capture_only_flags():
    result = main(["--list-configs", "--time", "10"])
    assert result == 2

def test_output_parent_failure_is_user_facing(tmp_path):
    non_directory = tmp_path / "not-a-directory"
    non_directory.write_text("occupied")
    with pytest.raises(ConfigError, match="output parent"):
        validate_output_path(non_directory / "trace.perfetto-trace")
```

- [x] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_capture_modes.py -v`

Expected: FAIL because the new validators and list-mode rejection are absent.

- [x] **Step 3: Implement minimal pre-device validation**

Accept only positive `mb`/`gb` buffer values, non-empty app identifiers or `*`, category/ftrace tokens without whitespace/control characters, and writable output parents. Reject capture-only arguments in listing modes before resolving ADB.

- [x] **Step 4: Add failing bundled-tracebox integrity tests**

```python
def test_every_android_tracebox_matches_upstream_manifest_and_checksum_file():
    assert tracebox_names == {"android-arm", "android-arm64", "android-x86", "android-x64"}
    assert all(local_sha[name] == upstream_manifest_sha[name] for name in tracebox_names)
```

- [x] **Step 5: Verify RED, then add the four official v57.2 artifacts**

Run: `python -m pytest tests/test_tool_pins.py -v`

Expected RED: missing local tracebox files/checksum entries.

Download from the exact v57.2 URLs embedded in `official/record_android_trace`, verify the embedded SHA256 values, mark binaries in `.gitattributes`, and record them in `tools/sha256.txt`.

- [x] **Step 6: Add local sideload command behavior**

`build_official_command(args, output, config_path=None, sideload_path=None)`
inserts `--sideload-path` followed by the selected repository file before the
config/categories. Tests cover API 23 and stopped-service API 29 selection
without running ADB.

- [x] **Step 7: Run focused tests and checksum verification**

Run:

```bash
python -m pytest tests/test_capture_modes.py tests/test_tool_pins.py -v
awk '!/^#/ && NF' tools/sha256.txt | shasum -a 256 -c -
```

Expected: all capture tests and all nine artifact checks pass.

### Task 4: Doctor, dependency preflight, and deterministic FPS startup

**Files:**
- Create: `tools/doctor.py`
- Create: `tests/test_doctor.py`
- Modify: `fps-test/run_fps_test.sh`
- Modify: `fps-test/_tp_shell_patch.py`
- Modify: `fps-test/sitecustomize.py`
- Create: `tests/test_tp_shell_patch.py`

**Interfaces:**
- Consumes: shared runtime checks, managed package metadata, artifact hashes, optional device requirement.
- Produces: `doctor.collect_checks(require_device: bool) -> list[Check]`, human and JSON output, and deterministic FPS preflight/readiness behavior.

- [x] **Step 1: Write failing doctor result tests**

```python
def test_doctor_reports_wrong_perfetto_version_as_failure():
    checks = collect_checks(package_versions={"perfetto": "0.56.0", "protobuf": "7.35.1"})
    assert any(c.name == "perfetto package" and c.status == "FAIL" for c in checks)

def test_doctor_without_device_reports_not_available_not_pass():
    checks = collect_checks(devices=[], require_device=False)
    assert device_check(checks).status == "NOT AVAILABLE"
```

- [x] **Step 2: Run focused doctor tests and verify RED**

Run: `python -m pytest tests/test_doctor.py -v`

Expected: import failure because `tools/doctor.py` does not exist.

- [x] **Step 3: Implement doctor checks and exit semantics**

Host readiness checks managed Python/package versions, artifact hashes, host trace processor execution, ADB path/version/source, and writable traces directory. `--device` additionally requires one authorized supported device. Human output uses `PASS`, `WARN`, `FAIL`, and `NOT AVAILABLE`; `--json` emits the same structured results. Any FAIL returns 3 or 4 according to host/device class.

- [x] **Step 4: Write failing local trace-processor delegate tests**

Prove the platform-to-binary mapping, prove missing supported-host binary raises a clear local error, and prove `sitecustomize.py` imports the single implementation from `_tp_shell_patch.py` rather than duplicating it.

- [x] **Step 5: Replace silent network fallback with fail-closed local analysis**

`_LocalShellDelegate.get_shell_path()` returns a verified local binary or raises an actionable error directing the user to `tools/setup`. `sitecustomize.py` becomes a minimal import of `_tp_shell_patch`; no normal analysis path downloads a trace processor.

- [x] **Step 6: Harden FPS startup and cleanup**

Before starting capture, check exact Perfetto package version, validate numeric duration/package syntax, and run doctor device compatibility. Trap `EXIT`, `INT`, and `TERM` so every failure reaps the tracer. Poll the capture log/process for the upstream `Trace started` marker with a bounded timeout instead of sleeping a fixed two seconds; fail before swipes if capture exits.

- [x] **Step 7: Run doctor, patch, and shell verification**

Run:

```bash
python -m pytest tests/test_doctor.py tests/test_tp_shell_patch.py -v
bash -n fps-test/run_fps_test.sh
shellcheck fps-test/run_fps_test.sh
.venv/bin/python tools/doctor.py
```

Expected: all tests pass; doctor reports the host ready and the absent physical device as `NOT AVAILABLE`.

### Task 5: Cross-platform verification and upstream drift automation

**Files:**
- Create: `tools/check_updates.py`
- Create: `tools/device_smoke.py`
- Create: `tests/test_update_checker.py`
- Create: `tests/test_device_smoke.py`
- Create: `.github/workflows/tool-drift.yml`
- Modify: `.github/workflows/verify.yml`
- Modify: `tests/test_tool_pins.py`

**Interfaces:**
- Consumes: authoritative upstream JSON/XML/raw content and the local manifests; optional authorized device.
- Produces: deterministic `UpdateStatus` records, scheduled/manual drift results, a device-aware smoke plan, and OS-matrix CI evidence.

- [x] **Step 1: Write failing pure update-comparison tests**

```python
def test_update_checker_distinguishes_stable_platform_tools_from_canary():
    status = compare_platform_tools(local="37.0.0", stable="37.0.0", canary="37.0.1")
    assert status.current is True
    assert status.note == "newer canary available: 37.0.1"

def test_record_helper_content_drift_fails_even_when_main_commit_only_moves():
    assert compare_record_helper(local_sha="a", remote_sha="b").current is False
```

- [x] **Step 2: Verify RED, then implement endpoint adapters and pure comparisons**

Run: `python -m pytest tests/test_update_checker.py -v`

Expected RED: checker module absent.

Network adapters use PyPI JSON, GitHub release/commit APIs, Android repository/release metadata, and the raw record helper. `--check` returns non-zero on stable/content drift; canary availability is informational.

- [x] **Step 3: Write failing device-smoke plan tests**

```python
@pytest.mark.parametrize(("api", "configs"), [
    (23, ["general"]),
    (29, ["general", "cpu"]),
    (31, ["general", "cpu", "jank"]),
])
def test_device_smoke_plan_matches_android_capabilities(api, configs):
    assert [item.config for item in build_smoke_plan(api)] == configs
```

- [x] **Step 4: Implement physical-device smoke command**

Each planned config captures one second with `--no-open` into a temporary
directory, requires a non-empty trace, reports per-config PASS/FAIL, and removes
temporary output. No device prints `NOT AVAILABLE` and exits 4 when
`--require-device` is supplied.

- [x] **Step 5: Expand Verify to an OS matrix**

Use Ubuntu, macOS, and Windows jobs. Every job installs through the native setup,
runs full pytest, doctor, the host trace-processor version smoke, and the native
capture list command. Ubuntu additionally runs `bash -n`, ShellCheck, and all
artifact hashes.

- [x] **Step 6: Add isolated weekly/manual drift workflow**

The workflow runs `python tools/check_updates.py --check` on a schedule and
`workflow_dispatch`. It is separate from push/PR verification so mutable
upstream state cannot make a source commit nondeterministic.

- [x] **Step 7: Run focused automation tests and parse both workflows**

Run:

```bash
python -m pytest tests/test_update_checker.py tests/test_device_smoke.py tests/test_tool_pins.py -v
ruby -e 'require "yaml"; Dir[".github/workflows/*.yml"].each { |f| YAML.load_file(f) }'
```

Expected: all tests pass and both workflows parse.

### Task 6: Compatibility documentation and historical truth

**Files:**
- Create: `docs/compatibility.md`
- Modify: `README.md`
- Modify: `capture/README.md`
- Modify: `configs/README.md`
- Modify: `tools/README.md`
- Modify: `fps-test/README.md`
- Modify: `simpleperf/README.md`
- Modify: `docs/systrace-migration.md`
- Modify: `official/VERSION`
- Modify: `official/README.md`

**Interfaces:**
- Consumes: the implemented host/Android contract and current upstream evidence.
- Produces: one authoritative compatibility matrix and setup/troubleshooting flow with no stronger claim than executable evidence supports.

- [x] **Step 1: Write the authoritative host/Android capability matrix**

Document self-contained, external-ADB, analysis-only, Unix-only, best-effort,
and unsupported cells. Include API 23-28 tracebox, API 29 service fallback,
API 31 FrameTimeline, Android 14 SurfaceFlinger latency, user-build input fallback,
and OEM ftrace limitations.

- [x] **Step 2: Replace manual environment setup with native one-command setup**

README starts with `./tools/setup.sh` on Unix and
`powershell -ExecutionPolicy Bypass -File tools\setup.ps1` on Windows, followed
by `tools/doctor.py`. Explain that first setup needs network and a physical
device/USB authorization can never be bundled.

- [x] **Step 3: Correct offline and platform claims**

State that runtime is offline after setup on fully supported hosts, legacy
Android tracebox is bundled, Linux ARM64 capture needs external ADB, Windows
does not support Bash FPS/Simpleperf orchestration, and explicit overrides are
non-hermetic escape hatches.

- [x] **Step 4: Refresh upstream metadata without pretending unrelated commits changed the script**

Record the latest inspected `main` commit and the unchanged record-helper SHA in
`official/VERSION`; keep Perfetto tool version v57.2 and snapshot date
2026-07-14. Tests verify both commit format and content hash.

- [x] **Step 5: Scan for stale or contradictory instructions**

Run:

```bash
rg -n "pip install|Python 3\.9|nothing is downloaded|Windows: manual|v49|35\.0\.2|systrace\.py|_tp_shell_patch" README.md capture configs tools fps-test simpleperf docs official
```

Expected: remaining historical terms are labeled, setup instructions point to
the managed path, and `_tp_shell_patch` references describe the actual import.

### Task 7: Full verification, simplification, and release

**Files:**
- Modify: `docs/superpowers/plans/2026-07-14-runtime-compatibility-hardening.md`

**Interfaces:**
- Consumes: all prior task deliverables and the repository-defined verification commands.
- Produces: a clean, pushed `main` whose remote CI and local HEAD agree.

- [x] **Step 1: Run the complete repository verification from the managed runtime**

Run:

```bash
./tools/setup.sh
.venv/bin/python -m pytest tests/ -v
git ls-files -z '*.sh' | xargs -0 bash -n
git ls-files -z '*.sh' | xargs -0 shellcheck
awk '!/^#/ && NF' tools/sha256.txt | shasum -a 256 -c -
.venv/bin/python tools/doctor.py
./capture/capture.sh --list-configs
.venv/bin/python official/record_android_trace --help
ruby -e 'require "yaml"; Dir[".github/workflows/*.yml"].each { |f| YAML.load_file(f) }'
git diff --check
```

Execution note: the managed setup completed with CPython 3.13.14, Perfetto
0.57.2, protobuf 7.35.1, Platform-Tools 37.0.0, nine verified native artifacts,
and the pinned official helper. All 175 tests, Bash syntax, ShellCheck, checksums,
doctor, config/help smoke checks, Ruby YAML parsing, upstream drift checks, and
`git diff --check` passed on macOS arm64.

- [x] **Step 2: Run physical-device automation or record exact unavailability**

Run: `.venv/bin/python tools/device_smoke.py --require-device`

Expected on the current host: exit 4 with `NOT AVAILABLE` if no authorized device
is attached. Do not report Android capture as verified in that case.

Execution note: the command exited 4 with `[NOT AVAILABLE] Android device: none
connected/authorized`; no physical-device capture is claimed.

- [x] **Step 3: Perform the required behavior-preserving simplification review**

Try `/simplify`, a repository-defined simplifier, then `code-simplifier`. If none
is available, manually review only changed code for duplicate version parsing,
duplicated platform mapping, dead fallbacks, and avoidable exception swallowing;
run `git diff --check` and record the fallback.

Execution note: `/simplify`, a repository simplifier, and `code-simplifier` were
not available. Manual review removed duplicated output validation and patch
implementations, centralized artifact/platform mapping, and added regression
coverage for rollback, malformed manifests/ADB/XML, relative overrides, process
cleanup, Python bounds, and numeric/app input edges. `git diff --check` passed.

- [x] **Step 4: Audit every design requirement against direct evidence**

Map host setup, platform matrix, Android matrix, automation, exception behavior,
and environment independence to tests/commands. Any missing or indirect evidence
keeps the task open.

Evidence map: bootstrap/install boundaries are covered by `test_bootstrap` plus
real setup; device/API/errors by `test_runtime` and capture tests; offline native
selection by artifact/tracebox/trace-processor tests; FPS readiness by runner
tests; upstream/device automation by update/device-smoke tests; the host matrix
and workflow isolation by workflow contract tests and Ruby YAML parsing. Physical
capture remains explicitly `NOT AVAILABLE` on the current host until a device is
attached; cross-host execution passed the remote three-OS Verify matrix recorded
below.

- [x] **Step 5: Stage explicit paths, commit, and push**

```bash
git add -- .github/workflows/tool-drift.yml .github/workflows/verify.yml \
  .gitignore README.md capture/README.md capture/capture.bat \
  capture/perfetto_capture.py configs/README.md docs/compatibility.md \
  docs/systrace-migration.md \
  docs/superpowers/plans/2026-07-14-runtime-compatibility-hardening.md \
  fps-test/README.md fps-test/_tp_shell_patch.py fps-test/run_fps_test.sh \
  fps-test/sitecustomize.py official/README.md official/VERSION \
  perfetto_tools/__init__.py perfetto_tools/runtime.py pyproject.toml \
  requirements-dev.txt requirements.txt simpleperf/README.md \
  tests/test_bootstrap.py tests/test_capture_modes.py tests/test_device_smoke.py \
  tests/test_doctor.py tests/test_runtime.py tests/test_tool_pins.py \
  tests/test_tp_shell_patch.py tests/test_update_checker.py tools/.gitattributes \
  tools/check_updates.py tools/device_smoke.py tools/doctor.py tools/README.md \
  tools/resolve.sh tools/setup.ps1 tools/setup.sh tools/setup_runtime.py \
  tools/sha256.txt tools/tool-versions.env tools/tracebox uv.lock
git diff --cached --check
git commit -m "feat: harden Perfetto runtime compatibility"
git push origin main
```

Execution note: all task paths were staged explicitly and pushed to `main`.
Concurrent remote documentation commit `2e2d250` was preserved by rebasing the
final portability-test fix instead of forcing the branch.

- [x] **Step 6: Verify remote closure**

Confirm `git ls-remote origin refs/heads/main` equals local HEAD, the worktree is
clean, and the Verify workflow for that exact SHA succeeds. If CI fails, diagnose
the root cause, add a regression test where applicable, fix, and repeat until
green.

Execution note: remote run `29313826659` passed on macOS, Ubuntu, and Windows for
implementation SHA `3394d1ad0e34e83027be6d3e9424ea5b0f19583e`. The two Windows
findings were fixed at their source: the pinned helper now retains LF bytes on
checkout, and host-matrix tests use platform-native path contracts. The final
closure-only plan commit is also required to pass Verify before completion.

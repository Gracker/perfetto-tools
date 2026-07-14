#!/usr/bin/env python3
"""Check pinned tools against authoritative stable upstream metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PYPI_PERFETTO = "https://pypi.org/pypi/perfetto/json"
UV_LATEST = "https://api.github.com/repos/astral-sh/uv/releases/latest"
ANDROID_REPOSITORY = "https://dl.google.com/android/repository/repository2-1.xml"
PERFETTO_COMMIT = "https://api.github.com/repos/google/perfetto/commits/main"
PERFETTO_RECORD = "https://raw.githubusercontent.com/google/perfetto/main/tools/record_android_trace"


@dataclass(frozen=True)
class UpdateStatus:
    name: str
    local: str
    latest: str
    current: bool
    note: str


class UpdateCheckFailure(RuntimeError):
    pass


def _version_key(value: str) -> tuple[int, ...]:
    numeric = value.lstrip("v").split("-", 1)[0]
    try:
        return tuple(int(part) for part in numeric.split("."))
    except ValueError as exc:
        raise UpdateCheckFailure(f"Could not parse version {value!r}") from exc


def compare_version(name: str, local: str, latest: str) -> UpdateStatus:
    current = local.lstrip("v") == latest.lstrip("v")
    note = "current" if current else f"stable update available: {latest}"
    return UpdateStatus(name, local, latest, current, note)


def compare_platform_tools(local: str, stable: str, canary: str) -> UpdateStatus:
    current = local == stable
    if not current:
        note = f"stable update available: {stable}"
    elif _version_key(canary) > _version_key(stable):
        note = f"newer canary available: {canary}"
    else:
        note = "current"
    return UpdateStatus("Android Platform-Tools", local, stable, current, note)


def compare_record_helper(
    *,
    local_sha: str,
    remote_sha: str,
    local_commit: str,
    remote_commit: str,
) -> UpdateStatus:
    current = local_sha == remote_sha
    if not current:
        note = f"content changed on main at {remote_commit}"
    elif local_commit != remote_commit:
        note = f"main moved to {remote_commit}; helper content unchanged"
    else:
        note = "current"
    return UpdateStatus(
        "record_android_trace",
        local_commit,
        remote_commit,
        current,
        note,
    )


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in element if _tag(child) == name), None)


def parse_platform_tools_versions(repository_xml: str) -> tuple[str, str]:
    root = ET.fromstring(repository_xml)
    channels = {
        element.attrib.get("id", ""): (element.text or "").strip().lower()
        for element in root.iter()
        if _tag(element) == "channel"
    }
    stable: list[str] = []
    preview: list[str] = []
    for package in root.iter():
        if _tag(package) != "remotePackage" or package.attrib.get("path") != "platform-tools":
            continue
        revision = _child(package, "revision")
        if revision is None:
            continue
        parts = []
        for name in ("major", "minor", "micro"):
            field = _child(revision, name)
            parts.append(int((field.text if field is not None else "0") or "0"))
        version = ".".join(str(part) for part in parts)
        channel_ref = _child(package, "channelRef")
        channel_id = channel_ref.attrib.get("ref", "channel-0") if channel_ref is not None else "channel-0"
        channel_name = channels.get(channel_id, channel_id)
        if channel_id == "channel-0" or channel_name == "stable":
            stable.append(version)
        else:
            preview.append(version)
    if not stable:
        raise UpdateCheckFailure("Android repository has no stable platform-tools package")
    stable_version = max(stable, key=_version_key)
    canary_version = max([stable_version, *preview], key=_version_key)
    return stable_version, canary_version


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "perfetto-tools-update-check"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except OSError as exc:
        raise UpdateCheckFailure(f"Could not fetch {url}: {exc}") from exc


def _json(url: str, fetch: Callable[[str], bytes]) -> dict:
    try:
        return json.loads(fetch(url))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UpdateCheckFailure(f"Invalid JSON from {url}: {exc}") from exc


def _env_manifest(path: Path) -> dict[str, str]:
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _official_metadata(path: Path) -> dict[str, str]:
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if ":" in raw_line:
            key, value = raw_line.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def _project_pin(path: Path, package: str) -> str:
    project = tomllib.loads(path.read_text(encoding="utf-8"))
    prefix = f"{package}=="
    dependency = next(
        (item for item in project["project"]["dependencies"] if item.startswith(prefix)),
        None,
    )
    if dependency is None:
        raise UpdateCheckFailure(f"pyproject.toml has no exact {package} pin")
    return dependency.removeprefix(prefix)


def collect_update_statuses(
    repo_root: Path = REPO_ROOT,
    *,
    fetch: Callable[[str], bytes] = _fetch,
) -> list[UpdateStatus]:
    versions = _env_manifest(repo_root / "tools" / "tool-versions.env")
    official = _official_metadata(repo_root / "official" / "VERSION")
    perfetto = _json(PYPI_PERFETTO, fetch)["info"]["version"]
    uv = _json(UV_LATEST, fetch)["tag_name"].lstrip("v")
    repository_xml = fetch(ANDROID_REPOSITORY).decode("utf-8")
    stable_pt, canary_pt = parse_platform_tools_versions(repository_xml)
    remote_record = fetch(PERFETTO_RECORD)
    remote_commit = _json(PERFETTO_COMMIT, fetch)["sha"]
    local_record = (repo_root / "official" / "record_android_trace").read_bytes()
    local_perfetto = _project_pin(repo_root / "pyproject.toml", "perfetto")

    return [
        compare_version("Perfetto Python package", local_perfetto, perfetto),
        compare_version("uv", versions["UV_VERSION"], uv),
        compare_platform_tools(versions["PLATFORM_TOOLS_VERSION"], stable_pt, canary_pt),
        compare_record_helper(
            local_sha=hashlib.sha256(local_record).hexdigest(),
            remote_sha=hashlib.sha256(remote_record).hexdigest(),
            local_commit=official["commit"],
            remote_commit=remote_commit,
        ),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on stable/content drift")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        statuses = collect_update_statuses()
    except (UpdateCheckFailure, KeyError, OSError) as exc:
        print(f"ERROR: update check failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([asdict(status) for status in statuses], indent=2))
    else:
        for status in statuses:
            marker = "CURRENT" if status.current else "UPDATE"
            print(f"[{marker}] {status.name}: {status.local} -> {status.latest}; {status.note}")
    return 1 if args.check and any(not status.current for status in statuses) else 0


if __name__ == "__main__":
    raise SystemExit(main())

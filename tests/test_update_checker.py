import pytest

from tools.check_updates import (
    UpdateCheckFailure,
    compare_platform_tools,
    compare_record_helper,
    compare_version,
    parse_platform_tools_versions,
)


def test_update_checker_distinguishes_stable_platform_tools_from_canary():
    status = compare_platform_tools(local="37.0.0", stable="37.0.0", canary="37.0.1")

    assert status.current is True
    assert status.note == "newer canary available: 37.0.1"
    assert status.latest == "37.0.0"


def test_update_checker_flags_stable_platform_tools_drift():
    status = compare_platform_tools(local="36.0.0", stable="37.0.0", canary="37.0.1")

    assert status.current is False
    assert status.latest == "37.0.0"


def test_record_helper_content_drift_fails_even_when_main_commit_only_moves():
    status = compare_record_helper(
        local_sha="a", remote_sha="b", local_commit="old", remote_commit="new"
    )

    assert status.current is False
    assert "content changed" in status.note


def test_record_helper_commit_movement_is_informational_when_content_is_same():
    status = compare_record_helper(
        local_sha="same", remote_sha="same", local_commit="old", remote_commit="new"
    )

    assert status.current is True
    assert status.note == "main moved to new; helper content unchanged"


def test_generic_version_comparison_is_exact():
    assert compare_version("perfetto package", "0.57.2", "0.57.2").current is True
    assert compare_version("uv", "0.11.27", "0.11.28").current is False


def test_platform_tools_repository_parser_separates_channels():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <sdk-repository>
      <channel id="channel-0">stable</channel>
      <channel id="channel-3">canary</channel>
      <remotePackage path="platform-tools">
        <revision><major>37</major><minor>0</minor><micro>0</micro></revision>
        <channelRef ref="channel-0"/>
      </remotePackage>
      <remotePackage path="platform-tools">
        <revision><major>37</major><minor>0</minor><micro>1</micro></revision>
        <channelRef ref="channel-3"/>
      </remotePackage>
    </sdk-repository>
    """

    assert parse_platform_tools_versions(xml) == ("37.0.0", "37.0.1")


def test_platform_tools_repository_parser_wraps_malformed_xml():
    with pytest.raises(UpdateCheckFailure, match="Android repository XML"):
        parse_platform_tools_versions("<not-closed>")

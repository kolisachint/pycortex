"""The ``CHANGELOG.md`` parser behind ``/changelog``."""

from __future__ import annotations

import os

import pytest
from cortex.code.interactive import ChangelogEntry, compare_versions, get_new_entries
from cortex.code.interactive.changelog import parse_changelog

SAMPLE = """# Changelog

## [1.2.0] - 2024-05-01

- Added the thing
- Fixed the other thing

## 1.1.0

- The first thing

## Unreleased

- Nothing here should survive

## [1.0.0]

- It began
"""


@pytest.fixture
def changelog(tmp_path: object) -> str:
    path = os.path.join(str(tmp_path), "CHANGELOG.md")
    with open(path, "w") as handle:
        handle.write(SAMPLE)
    return path


class TestParseChangelog:
    def test_a_missing_file_yields_no_entries(self, tmp_path: object):
        assert parse_changelog(os.path.join(str(tmp_path), "nope.md")) == []

    def test_a_directory_yields_no_entries(self, tmp_path: object):
        # `existsSync` is true for a directory, so the read is what has to fail
        # gracefully — the TS catches and returns []; so does this.
        assert parse_changelog(str(tmp_path)) == []

    def test_reads_bracketed_and_bare_versions(self, changelog: str):
        entries = parse_changelog(changelog)
        assert [(e.major, e.minor, e.patch) for e in entries] == [(1, 2, 0), (1, 1, 0), (1, 0, 0)]

    def test_keeps_the_heading_in_the_content(self, changelog: str):
        first = parse_changelog(changelog)[0]
        assert first.content.startswith("## [1.2.0] - 2024-05-01")
        assert "- Added the thing" in first.content

    def test_an_unparseable_heading_drops_only_its_own_section(self, changelog: str):
        entries = parse_changelog(changelog)
        assert not any("Nothing here should survive" in e.content for e in entries)
        # ...and the section after it is still read.
        assert entries[-1].content.endswith("- It began")

    def test_content_is_stripped(self, changelog: str):
        assert all(e.content == e.content.strip() for e in parse_changelog(changelog))

    def test_a_heading_with_no_body_is_dropped(self, tmp_path: object):
        # `currentLines` holds the heading itself, so a section is only skipped
        # when the *file* ends before it — matching the TS's `length > 0` guard
        # on a version that was never opened.
        path = os.path.join(str(tmp_path), "CHANGELOG.md")
        with open(path, "w") as handle:
            handle.write("Nothing but prose.\n")
        assert parse_changelog(path) == []

    def test_the_last_section_is_saved(self, tmp_path: object):
        path = os.path.join(str(tmp_path), "CHANGELOG.md")
        with open(path, "w") as handle:
            handle.write("## 2.0.0\n\n- Ends here")
        assert [e.content for e in parse_changelog(path)] == ["## 2.0.0\n\n- Ends here"]


class TestCompareVersions:
    @pytest.mark.parametrize(
        ("left", "right", "sign"),
        [
            ((1, 0, 0), (2, 0, 0), -1),
            ((2, 0, 0), (1, 9, 9), 1),
            ((1, 2, 0), (1, 3, 0), -1),
            ((1, 2, 3), (1, 2, 4), -1),
            ((1, 2, 3), (1, 2, 3), 0),
        ],
    )
    def test_orders_by_major_then_minor_then_patch(
        self, left: tuple[int, int, int], right: tuple[int, int, int], sign: int
    ):
        result = compare_versions(ChangelogEntry(*left, ""), ChangelogEntry(*right, ""))
        assert (result > 0) - (result < 0) == sign

    def test_content_is_not_part_of_the_ordering(self):
        a = ChangelogEntry(1, 0, 0, "zebra")
        b = ChangelogEntry(1, 0, 0, "aardvark")
        assert compare_versions(a, b) == 0


class TestGetNewEntries:
    ENTRIES = [
        ChangelogEntry(1, 0, 0, "one"),
        ChangelogEntry(1, 1, 0, "two"),
        ChangelogEntry(2, 0, 0, "three"),
    ]

    def test_returns_only_strictly_newer_entries(self):
        assert [e.content for e in get_new_entries(self.ENTRIES, "1.1.0")] == ["three"]

    def test_a_version_above_everything_returns_nothing(self):
        assert get_new_entries(self.ENTRIES, "9.0.0") == []

    def test_a_short_version_pads_with_zeros(self):
        assert [e.content for e in get_new_entries(self.ENTRIES, "1")] == ["two", "three"]

    def test_a_malformed_component_reads_as_zero(self):
        # `Number("x") || 0` in the TS: the whole changelog, not none of it.
        assert len(get_new_entries(self.ENTRIES, "x.y.z")) == 3

"""
tests/test_diff_processor.py

Unit tests for the diff filtering, prioritization, and truncation logic.
"""

import pytest

from app.services.diff_processor import (
    TRUNCATION_NOTICE,
    extract_reviewable_new_lines,
    filter_files,
    prepare_diffs,
    prioritize_files,
    truncate_diff,
)

# ── Helper factories ─────────────────────────────────────────────────────────


def _file(name: str, changes: int = 10, patch: str = "diff content") -> dict:
    """Create a minimal GitHub PR file dict."""
    return {"filename": name, "changes": changes, "patch": patch}


# ── filter_files (FR-05) ─────────────────────────────────────────────────────


class TestFilterFiles:
    def test_skips_matching_extensions(self) -> None:
        files = [_file("app.py"), _file("logo.svg"), _file("data.json")]
        result = filter_files(files, [".svg", ".json"])
        assert len(result) == 1
        assert result[0]["filename"] == "app.py"

    def test_case_insensitive_extensions(self) -> None:
        files = [_file("README.MD"), _file("app.py")]
        result = filter_files(files, [".md"])
        assert len(result) == 1
        assert result[0]["filename"] == "app.py"

    def test_no_skip_extensions_keeps_all(self) -> None:
        files = [_file("a.py"), _file("b.js")]
        result = filter_files(files, [])
        assert len(result) == 2

    def test_empty_files_returns_empty(self) -> None:
        assert filter_files([], [".py"]) == []

    def test_all_files_skipped_returns_empty(self) -> None:
        files = [_file("a.lock"), _file("b.svg")]
        result = filter_files(files, [".lock", ".svg"])
        assert result == []

    def test_preserves_order(self) -> None:
        files = [_file("c.py"), _file("a.lock"), _file("b.py")]
        result = filter_files(files, [".lock"])
        assert [f["filename"] for f in result] == ["c.py", "b.py"]


# ── prioritize_files (FR-06) ─────────────────────────────────────────────────


class TestPrioritizeFiles:
    def test_sorts_by_changes_descending(self) -> None:
        files = [_file("a.py", 5), _file("b.py", 20), _file("c.py", 10)]
        result = prioritize_files(files, max_files=10)
        assert [f["filename"] for f in result] == ["b.py", "c.py", "a.py"]

    def test_caps_at_max_files(self) -> None:
        files = [_file(f"f{i}.py", changes=i) for i in range(20)]
        result = prioritize_files(files, max_files=5)
        assert len(result) == 5
        # Should be the top 5 by changes (19, 18, 17, 16, 15).
        assert result[0]["changes"] == 19

    def test_fewer_files_than_max_returns_all(self) -> None:
        files = [_file("a.py", 1), _file("b.py", 2)]
        result = prioritize_files(files, max_files=10)
        assert len(result) == 2

    def test_missing_changes_defaults_to_zero(self) -> None:
        files = [{"filename": "a.py"}, _file("b.py", 5)]
        result = prioritize_files(files, max_files=10)
        assert result[0]["filename"] == "b.py"

    def test_empty_input(self) -> None:
        assert prioritize_files([], max_files=10) == []


# ── truncate_diff (FR-07) ────────────────────────────────────────────────────


class TestTruncateDiff:
    def test_short_diff_unchanged(self) -> None:
        assert truncate_diff("short", 1000) == "short"

    def test_exact_limit_unchanged(self) -> None:
        text = "x" * 100
        assert truncate_diff(text, 100) == text

    def test_long_diff_truncated_with_notice(self) -> None:
        text = "x" * 200
        result = truncate_diff(text, 100)
        assert len(result) > 100  # notice adds characters
        assert result.startswith("x" * 100)
        assert TRUNCATION_NOTICE in result

    def test_none_patch_returns_empty_string(self) -> None:
        assert truncate_diff(None, 100) == ""

    def test_empty_string_unchanged(self) -> None:
        assert truncate_diff("", 100) == ""


class TestExtractReviewableNewLines:
    def test_extracts_added_and_context_lines_from_new_file(self) -> None:
        patch = (
            "@@ -10,5 +20,6 @@\n"
            " context\n"
            "-old line\n"
            "+new line\n"
            " another context\n"
            "\\ No newline at end of file\n"
        )
        assert extract_reviewable_new_lines(patch) == {20, 21, 22}

    def test_empty_patch_has_no_reviewable_lines(self) -> None:
        assert extract_reviewable_new_lines(None) == set()
        assert extract_reviewable_new_lines("") == set()


# ── prepare_diffs (full pipeline) ────────────────────────────────────────────


class TestPrepareDiffs:
    def test_full_pipeline(self) -> None:
        files = [
            _file("app.py", 50, "a" * 200),
            _file("logo.svg", 10, "svg data"),
            _file("utils.py", 30, "b" * 50),
            _file("readme.md", 5, "docs"),
        ]
        result = prepare_diffs(
            files,
            skip_extensions=[".svg", ".md"],
            max_files=10,
            max_diff_chars=100,
        )
        # Only .py files survive filtering.
        assert len(result) == 2
        # Sorted by changes descending: app.py (50) then utils.py (30).
        assert result[0]["filename"] == "app.py"
        assert result[1]["filename"] == "utils.py"
        # app.py patch was 200 chars → truncated to 100 + notice.
        assert TRUNCATION_NOTICE in result[0]["patch"]
        # utils.py was 50 chars → not truncated.
        assert TRUNCATION_NOTICE not in result[1]["patch"]

    def test_all_filtered_returns_empty(self) -> None:
        files = [_file("a.lock"), _file("b.svg")]
        result = prepare_diffs(files, [".lock", ".svg"], max_files=10, max_diff_chars=100)
        assert result == []

    def test_empty_input(self) -> None:
        assert prepare_diffs([], [], 10, 100) == []

    def test_max_files_applied_after_filtering(self) -> None:
        """Ensure cap is applied to the filtered set, not the raw input."""
        files = [
            _file("a.py", 100),
            _file("b.py", 90),
            _file("c.py", 80),
            _file("d.lock", 200),  # filtered out
        ]
        result = prepare_diffs(files, [".lock"], max_files=2, max_diff_chars=10000)
        assert len(result) == 2
        assert result[0]["filename"] == "a.py"
        assert result[1]["filename"] == "b.py"

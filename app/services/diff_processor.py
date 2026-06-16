"""
app/services/diff_processor.py

Pure-logic module for diff filtering, prioritization, and truncation.

No I/O — fully unit-testable.  Implements:
  FR-05  Skip files matching configurable extensions.
  FR-06  Rank by changed lines (descending), cap at MAX_FILES_PER_PR.
  FR-07  Truncate diffs exceeding MAX_DIFF_CHARS with a notice.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

TRUNCATION_NOTICE = "\n\n... [truncated — diff exceeded maximum character limit] ..."
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


# ── FR-05 ────────────────────────────────────────────────────────────────────


def filter_files(
    files: List[Dict[str, Any]],
    skip_extensions: List[str],
) -> List[Dict[str, Any]]:
    """Remove files whose extension is in the skip list.

    Args:
        files: List of file dicts from the GitHub PR files API.
                Each dict must have a ``filename`` key.
        skip_extensions: Lowercase extensions to skip, e.g. [".lock", ".svg"].

    Returns:
        Filtered list (order preserved, skipped files removed).
    """
    kept: List[Dict[str, Any]] = []
    for f in files:
        _, ext = os.path.splitext(f["filename"])
        if ext.lower() in skip_extensions:
            logger.debug("Skipping %s (extension %s)", f["filename"], ext)
            continue
        kept.append(f)
    return kept


# ── FR-06 ────────────────────────────────────────────────────────────────────


def prioritize_files(
    files: List[Dict[str, Any]],
    max_files: int,
) -> List[Dict[str, Any]]:
    """Sort files by number of changes (descending) and cap at *max_files*.

    Args:
        files: List of file dicts; each should have a ``changes`` key
               (defaults to 0 if missing).
        max_files: Maximum number of files to keep.

    Returns:
        Top *max_files* files sorted by changes descending.
    """
    sorted_files = sorted(
        files,
        key=lambda f: f.get("changes", 0),
        reverse=True,
    )
    if len(sorted_files) > max_files:
        logger.info(
            "Capping review to %d / %d files (MAX_FILES_PER_PR).",
            max_files,
            len(sorted_files),
        )
    return sorted_files[:max_files]


# ── FR-07 ────────────────────────────────────────────────────────────────────


def truncate_diff(patch: str | None, max_chars: int) -> str:
    """Truncate a diff string that exceeds *max_chars*.

    Args:
        patch: The raw patch/diff text.  May be ``None`` for binary files.
        max_chars: Character budget per file.

    Returns:
        The (possibly truncated) diff string.
    """
    if patch is None:
        return ""
    if len(patch) <= max_chars:
        return patch
    logger.debug(
        "Truncating diff from %d to %d chars.", len(patch), max_chars,
    )
    return patch[:max_chars] + TRUNCATION_NOTICE


def extract_reviewable_new_lines(patch: str | None) -> Set[int]:
    """Return new-file line numbers that can receive GitHub review comments."""
    if not patch:
        return set()

    reviewable_lines: Set[int] = set()
    current_line: int | None = None

    for raw_line in patch.splitlines():
        header_match = _HUNK_HEADER_RE.match(raw_line)
        if header_match:
            current_line = int(header_match.group(1))
            continue

        if current_line is None:
            continue

        if raw_line.startswith("+") or raw_line.startswith(" "):
            reviewable_lines.add(current_line)
            current_line += 1
        elif raw_line.startswith("-"):
            continue
        elif raw_line.startswith("\\"):
            continue
        else:
            current_line += 1

    return reviewable_lines


# ── Pipeline ─────────────────────────────────────────────────────────────────


def prepare_diffs(
    files: List[Dict[str, Any]],
    skip_extensions: List[str],
    max_files: int,
    max_diff_chars: int,
) -> List[Dict[str, str]]:
    """Run the full filter → prioritize → truncate pipeline.

    Args:
        files: Raw file list from the GitHub ``/pulls/{n}/files`` endpoint.
        skip_extensions: Extensions to exclude (FR-05).
        max_files: File cap (FR-06).
        max_diff_chars: Per-file character cap (FR-07).

    Returns:
        List of ``{"filename": ..., "patch": ...}`` dicts ready for review.
    """
    filtered = filter_files(files, skip_extensions)
    prioritized = prioritize_files(filtered, max_files)

    results: List[Dict[str, str]] = []
    for f in prioritized:
        results.append({
            "filename": f["filename"],
            "patch": truncate_diff(f.get("patch"), max_diff_chars),
        })
    return results

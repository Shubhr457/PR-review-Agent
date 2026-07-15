"""
app/services/token_manager.py

Diff chunking + token budget logic for managing OpenAI token usage and costs.
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a string of text.

    Uses a standard heuristic: 1 token ~= 4 characters for English / source code.
    """
    if not text:
        return 0
    return len(text) // 4


def calculate_total_tokens(prepared_diffs: List[Dict[str, str]]) -> int:
    """Calculate the total estimated tokens across all prepared file patches."""
    total = 0
    for diff in prepared_diffs:
        total += estimate_tokens(diff.get("patch", ""))
    return total


def filter_by_token_budget(
    prepared_diffs: List[Dict[str, str]],
    token_budget: int,
) -> List[Dict[str, str]]:
    """Filter files to fit within a token budget while preserving priority.

    Prepared diffs are expected to be ordered by priority (descending changes).
    If a file patch would push the total estimated tokens over the budget, it is skipped.
    """
    if token_budget <= 0:
        return prepared_diffs

    allocated_diffs: List[Dict[str, str]] = []
    current_tokens = 0

    for diff in prepared_diffs:
        estimated = estimate_tokens(diff.get("patch", ""))
        if current_tokens + estimated <= token_budget:
            allocated_diffs.append(diff)
            current_tokens += estimated
        else:
            logger.warning(
                "Skipping a file (estimated %d tokens) — exceeds remaining token budget (%d left).",
                estimated,
                token_budget - current_tokens,
            )

    return allocated_diffs

"""
tests/test_token_manager.py

Unit tests for diff token estimation and budget calculations.
"""

from app.services.token_manager import (
    calculate_total_tokens,
    estimate_tokens,
    filter_by_token_budget,
)


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0
    # standard 4 chars per token rule
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("a" * 100) == 25


def test_calculate_total_tokens() -> None:
    diffs = [
        {"filename": "a.py", "patch": "a" * 40},  # 10 tokens
        {"filename": "b.py", "patch": "b" * 20},  # 5 tokens
        {"filename": "c.py", "patch": ""},        # 0 tokens
    ]
    assert calculate_total_tokens(diffs) == 15


def test_filter_by_token_budget_zero_or_negative_budget() -> None:
    diffs = [
        {"filename": "a.py", "patch": "a" * 40},
        {"filename": "b.py", "patch": "b" * 20},
    ]
    # zero budget should bypass filtering and return all
    assert len(filter_by_token_budget(diffs, 0)) == 2
    assert len(filter_by_token_budget(diffs, -5)) == 2


def test_filter_by_token_budget_limits_files() -> None:
    diffs = [
        {"filename": "a.py", "patch": "a" * 40},  # 10 tokens
        {"filename": "b.py", "patch": "b" * 20},  # 5 tokens
        {"filename": "c.py", "patch": "c" * 8},   # 2 tokens
    ]
    # budget = 12: should fit a.py (10) and c.py (2), or a.py (10) then skip b.py (5) and fit c.py (2).
    # current_tokens = 0.
    # a.py: 10 tokens. 0+10 <= 12 -> keep (current=10)
    # b.py: 5 tokens. 10+5 > 12 -> skip
    # c.py: 2 tokens. 10+2 <= 12 -> keep (current=12)
    filtered = filter_by_token_budget(diffs, 12)
    assert len(filtered) == 2
    assert filtered[0]["filename"] == "a.py"
    assert filtered[1]["filename"] == "c.py"


def test_filter_by_token_budget_strict_cap() -> None:
    diffs = [
        {"filename": "a.py", "patch": "a" * 40},  # 10 tokens
        {"filename": "b.py", "patch": "b" * 20},  # 5 tokens
    ]
    # budget = 8.
    # a.py: 10 tokens > 8 -> skip
    # b.py: 5 tokens <= 8 -> keep (current=5)
    filtered = filter_by_token_budget(diffs, 8)
    assert len(filtered) == 1
    assert filtered[0]["filename"] == "b.py"

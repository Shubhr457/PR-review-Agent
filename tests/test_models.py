"""
tests/test_models.py

Unit tests for Pydantic models (Milestone 1).

No HTTP calls — pure model validation logic.
"""

from typing import Any, Dict

import pytest
from pydantic import ValidationError

from app.models.github_schemas import RepoInfo, UserInfo, WebhookPayload
from app.models.review_schemas import ReviewComment, ReviewResult, Severity

# ── github_schemas ────────────────────────────────────────────────────────────


class TestUserInfo:
    def test_valid(self) -> None:
        u = UserInfo(login="octocat", id=1)
        assert u.login == "octocat"

    def test_extra_fields_ignored(self) -> None:
        u = UserInfo(login="octocat", id=1, avatar_url="https://example.com/img.png")  # type: ignore[call-arg]
        assert not hasattr(u, "avatar_url")

    def test_missing_login_raises(self) -> None:
        with pytest.raises(ValidationError):
            UserInfo(id=1)  # type: ignore[call-arg]


class TestRepoInfo:
    def test_valid(self) -> None:
        r = RepoInfo(
            id=1,
            name="Hello-World",
            full_name="octocat/Hello-World",
            clone_url="https://github.com/octocat/Hello-World.git",
            default_branch="main",
        )
        assert r.full_name == "octocat/Hello-World"


class TestWebhookPayload:
    def _base(self, action: str = "opened") -> Dict[str, Any]:
        return {
            "action": action,
            "number": 1,
            "pull_request": {
                "number": 1,
                "title": "Test PR",
                "state": "open",
                "user": {"login": "dev", "id": 2},
                "head": {
                    "ref": "feature",
                    "sha": "abc",
                    "repo": {
                        "id": 1,
                        "name": "repo",
                        "full_name": "org/repo",
                        "clone_url": "https://github.com/org/repo.git",
                        "default_branch": "main",
                    },
                },
                "base": {
                    "ref": "main",
                    "sha": "def",
                    "repo": {
                        "id": 1,
                        "name": "repo",
                        "full_name": "org/repo",
                        "clone_url": "https://github.com/org/repo.git",
                        "default_branch": "main",
                    },
                },
                "url": "https://api.github.com/repos/org/repo/pulls/1",
                "html_url": "https://github.com/org/repo/pull/1",
                "diff_url": "https://github.com/org/repo/pull/1.diff",
            },
            "repository": {
                "id": 1,
                "name": "repo",
                "full_name": "org/repo",
                "clone_url": "https://github.com/org/repo.git",
                "default_branch": "main",
            },
            "sender": {"login": "dev", "id": 2},
        }

    def test_valid_opened(self) -> None:
        p = WebhookPayload.model_validate(self._base("opened"))
        assert p.action == "opened"
        assert p.number == 1
        assert p.pull_request.changed_files == 0  # default

    def test_missing_pull_request_raises(self) -> None:
        data = self._base()
        del data["pull_request"]
        with pytest.raises(ValidationError):
            WebhookPayload.model_validate(data)

    def test_changed_files_defaults_to_zero(self) -> None:
        p = WebhookPayload.model_validate(self._base())
        assert p.pull_request.changed_files == 0

    def test_pr_body_can_be_none(self) -> None:
        data = self._base()
        data["pull_request"]["body"] = None
        p = WebhookPayload.model_validate(data)
        assert p.pull_request.body is None


# ── review_schemas ────────────────────────────────────────────────────────────


class TestSeverity:
    def test_enum_values(self) -> None:
        assert Severity.BUG == "bug"
        assert Severity.WARNING == "warning"
        assert Severity.SUGGESTION == "suggestion"

    def test_emojis(self) -> None:
        assert Severity.BUG.emoji == "🔴"
        assert Severity.WARNING.emoji == "🟡"
        assert Severity.SUGGESTION.emoji == "🔵"


class TestReviewComment:
    def test_valid(self) -> None:
        c = ReviewComment(
            file="src/main.py",
            line=10,
            severity=Severity.BUG,
            comment="Null pointer risk",
        )
        assert c.file == "src/main.py"
        assert c.line == 10

    def test_formatted_body_contains_emoji(self) -> None:
        c = ReviewComment(
            file="a.py",
            line=1,
            severity=Severity.WARNING,
            comment="Use context manager",
        )
        assert "🟡" in c.formatted_body
        assert "Warning" in c.formatted_body
        assert "Use context manager" in c.formatted_body

    def test_line_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ReviewComment(file="a.py", line=0, severity=Severity.BUG, comment="x")

    def test_comment_cannot_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            ReviewComment(file="a.py", line=1, severity=Severity.BUG, comment="")


class TestReviewResult:
    def test_approved_when_no_comments(self) -> None:
        r = ReviewResult(pr_number=1, repo_full_name="org/repo", comments=[])
        assert r.approved is True
        assert r.github_event == "APPROVE"

    def test_request_changes_when_comments_exist(self) -> None:
        comment = ReviewComment(
            file="a.py", line=5, severity=Severity.BUG, comment="Issue here"
        )
        r = ReviewResult(pr_number=1, repo_full_name="org/repo", comments=[comment])
        assert r.approved is False
        assert r.github_event == "REQUEST_CHANGES"

    def test_default_comments_is_empty_list(self) -> None:
        r = ReviewResult(pr_number=1, repo_full_name="org/repo")
        assert r.comments == []


# ── security module ───────────────────────────────────────────────────────────


class TestVerifyWebhookSignature:
    def test_valid_signature(self) -> None:
        import hashlib
        import hmac as hmac_mod

        from app.core.security import verify_webhook_signature

        body = b'{"test": true}'
        secret = "my-secret"
        sig = (
            "sha256=" + hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
        # Should not raise
        verify_webhook_signature(body, secret, sig)

    def test_missing_header_raises_401(self) -> None:
        from fastapi import HTTPException

        from app.core.security import verify_webhook_signature

        with pytest.raises(HTTPException) as exc_info:
            verify_webhook_signature(b"body", "secret", None)
        assert exc_info.value.status_code == 401

    def test_wrong_signature_raises_401(self) -> None:
        from fastapi import HTTPException

        from app.core.security import verify_webhook_signature

        with pytest.raises(HTTPException) as exc_info:
            verify_webhook_signature(b"body", "secret", "sha256=wrongdigest")
        assert exc_info.value.status_code == 401

    def test_missing_sha256_prefix_raises_401(self) -> None:
        import hashlib
        import hmac as hmac_mod

        from fastapi import HTTPException

        from app.core.security import verify_webhook_signature

        body = b"data"
        secret = "s"
        raw_digest = hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()
        with pytest.raises(HTTPException):
            verify_webhook_signature(body, secret, raw_digest)


# ── config ────────────────────────────────────────────────────────────────────


class TestSettings:
    def test_skip_extensions_list(self) -> None:
        from app.core.config import Settings

        s = Settings(skip_extensions=".lock,.svg,.png")
        assert s.skip_extensions_list == [".lock", ".svg", ".png"]

    def test_skip_extensions_list_strips_whitespace(self) -> None:
        from app.core.config import Settings

        s = Settings(skip_extensions=" .lock , .svg ")
        assert ".lock" in s.skip_extensions_list
        assert ".svg" in s.skip_extensions_list

    def test_defaults(self) -> None:
        from app.core.config import Settings

        s = Settings(_env_file=None)
        assert s.max_files_per_pr == 10
        assert s.max_diff_chars == 8000
        assert s.large_pr_threshold == 15
        assert s.enable_docs is False

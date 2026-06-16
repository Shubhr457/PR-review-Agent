"""
app/services/github.py

Async GitHub REST API client (httpx).

Responsibilities:
  - Fetch the list of changed files for a PR.
  - Post a batched GitHub Review with inline comments (FR-13 / FR-14 / FR-15).
  - Post a fail-open warning comment when the agent errors (FR-16).
  - Post commit status checks (Milestone 6).

All requests use exponential backoff retry on transient errors (429, 5xx).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, FrozenSet, List, Optional

import httpx

from app.models.review_schemas import ReviewResult

logger = logging.getLogger(__name__)

# GitHub API recommends this media type for REST v3.
_GITHUB_ACCEPT = "application/vnd.github+json"
_GITHUB_API_VERSION = "2022-11-28"

# Timeout budget: generous to survive slow GitHub responses.
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Status codes worth retrying (rate limiting + transient server errors).
_RETRYABLE_STATUS: FrozenSet[int] = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES: int = 3


class GitHubServiceError(Exception):
    """Raised when a GitHub API call fails after all retries."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"GitHub API {status_code}: {detail}")


class GitHubService:
    """Thin async wrapper around the GitHub REST API for PR operations."""

    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": _GITHUB_ACCEPT,
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        }

    # ── helpers ──────────────────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        """Create a fresh AsyncClient (intended for use in ``async with``)."""
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=_TIMEOUT,
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        detail = response.text[:500]  # cap noisy payloads
        logger.error(
            "GitHub API error %s %s: %s",
            response.status_code,
            response.request.url,
            detail,
        )
        raise GitHubServiceError(response.status_code, detail)

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """Make an authenticated request with exponential backoff on transient errors.

        Retries up to _MAX_RETRIES times on 429 (rate limit) and 5xx errors.
        Respects the ``Retry-After`` response header when present.
        """
        response = await client.request(method, path, params=params, json=json)
        for attempt in range(_MAX_RETRIES):
            if response.status_code not in _RETRYABLE_STATUS:
                break
            delay = min(
                float(response.headers.get("Retry-After", 2**attempt)),
                30.0,
            )
            logger.warning(
                "GitHub API %s on %s — retrying in %.1fs (attempt %d/%d).",
                response.status_code,
                path,
                delay,
                attempt + 1,
                _MAX_RETRIES,
            )
            await asyncio.sleep(delay)
            response = await client.request(method, path, params=params, json=json)
        return response

    # ── Fetch PR files ───────────────────────────────────────────────────────

    async def fetch_pr_files(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """Return the list of changed files for a pull request (paginated)."""
        all_files: List[Dict[str, Any]] = []
        page = 1

        async with self._client() as client:
            while True:
                response = await self._request(
                    client,
                    "GET",
                    f"/repos/{owner}/{repo}/pulls/{pr_number}/files",
                    params={"per_page": per_page, "page": page},
                )
                self._raise_for_status(response)

                batch: List[Dict[str, Any]] = response.json()
                if not batch:
                    break
                all_files.extend(batch)

                if len(batch) < per_page:
                    break
                page += 1

        logger.info(
            "Fetched %d files for %s/%s#%d.",
            len(all_files),
            owner,
            repo,
            pr_number,
        )
        return all_files

    # ── Post review (FR-13, FR-14, FR-15) ────────────────────────────────────

    async def post_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        review: ReviewResult,
        commit_sha: str,
    ) -> Dict[str, Any]:
        """Post a single batched GitHub Review."""
        comments: List[Dict[str, Any]] = [
            {"path": c.file, "line": c.line, "body": c.formatted_body}
            for c in review.comments
        ]

        body_text = (
            "✅ **PR Review Agent** — no issues found."
            if review.approved
            else "🔍 **PR Review Agent** — review comments attached."
        )

        payload: Dict[str, Any] = {
            "commit_id": commit_sha,
            "event": review.github_event,
            "body": body_text,
            "comments": comments,
        }

        async with self._client() as client:
            response = await self._request(
                client,
                "POST",
                f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                json=payload,
            )
            self._raise_for_status(response)

        logger.info(
            "Posted %s review with %d comments on %s/%s#%d.",
            review.github_event,
            len(comments),
            owner,
            repo,
            pr_number,
        )
        return response.json()

    # ── Fail-open comment (FR-16) ────────────────────────────────────────────

    async def post_failure_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        error_msg: str,
    ) -> Optional[Dict[str, Any]]:
        """Post a warning comment so the PR is never permanently blocked (FR-16)."""
        body = (
            "⚠️ **PR Review Agent — Error**\n\n"
            "The automated review could not be completed. "
            "This PR is **not blocked** — you may merge at your discretion.\n\n"
            f"```\n{error_msg}\n```"
        )

        try:
            async with self._client() as client:
                response = await self._request(
                    client,
                    "POST",
                    f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
                    json={"body": body},
                )
                self._raise_for_status(response)
            logger.warning(
                "Posted failure comment on %s/%s#%d: %s",
                owner,
                repo,
                pr_number,
                error_msg,
            )
            return response.json()
        except Exception:
            logger.exception(
                "Could not post failure comment on %s/%s#%d.",
                owner,
                repo,
                pr_number,
            )
            return None

    # ── Post Commit Status (Milestone 6) ─────────────────────────────────────

    async def post_commit_status(
        self,
        owner: str,
        repo: str,
        sha: str,
        state: str,
        description: str,
        context: str = "PR Review Agent",
        target_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Post a commit status check on a specific commit."""
        payload: Dict[str, Any] = {
            "state": state,
            "description": description[:140],
            "context": context,
        }
        if target_url:
            payload["target_url"] = target_url

        async with self._client() as client:
            response = await self._request(
                client,
                "POST",
                f"/repos/{owner}/{repo}/statuses/{sha}",
                json=payload,
            )
            self._raise_for_status(response)

        logger.info(
            "Posted status '%s' for commit %s on %s/%s. Context: %s.",
            state,
            sha[:7],
            owner,
            repo,
            context,
        )
        return response.json()

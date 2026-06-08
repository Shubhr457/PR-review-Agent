"""
app/models/github_schemas.py

Pydantic models for GitHub pull_request webhook payloads.

GitHub sends a rich JSON body; we model only the fields the agent needs so
that unexpected additions from GitHub's API evolution don't break parsing.
Unknown fields are ignored via model_config.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class _GitHubBase(BaseModel):
    """Shared config: ignore any extra fields GitHub may add in the future."""

    model_config = ConfigDict(extra="ignore")


# ── Sub-models ───────────────────────────────────────────────────────────────


class UserInfo(_GitHubBase):
    login: str
    id: int


class RepoInfo(_GitHubBase):
    id: int
    name: str
    full_name: str  # e.g. "octocat/Hello-World"
    clone_url: str
    default_branch: str


class BranchRef(_GitHubBase):
    """Represents the head or base branch of a PR."""

    ref: str  # branch name
    sha: str
    repo: RepoInfo


class PullRequestInfo(_GitHubBase):
    number: int
    title: str
    state: str  # "open" | "closed"
    body: Optional[str] = None
    user: UserInfo
    head: BranchRef
    base: BranchRef
    url: str  # GitHub API URL
    html_url: str  # Web URL
    diff_url: str
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0


class WebhookPayload(_GitHubBase):
    """
    Top-level payload for a pull_request webhook event.

    Supported actions: opened | synchronize | reopened
    """

    action: str
    number: int
    pull_request: PullRequestInfo
    repository: RepoInfo
    sender: UserInfo

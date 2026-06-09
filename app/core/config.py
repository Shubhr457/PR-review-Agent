"""
app/core/config.py

Application configuration loaded from environment variables or a .env file.
In production (AWS Lambda) these values come from AWS Secrets Manager; the
secrets module writes them to environment variables at cold-start so this
class is always the single source of truth regardless of environment.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── GitHub ──────────────────────────────────────────────────────────────
    github_token: str = ""
    github_webhook_secret: str = ""
    github_api_base_url: str = "https://api.github.com"

    # ── OpenAI ──────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # ── AWS / SQS ───────────────────────────────────────────────────────────
    sqs_queue_url: str = ""

    # ── Tuning ──────────────────────────────────────────────────────────────
    max_files_per_pr: int = 10
    max_diff_chars: int = 8_000
    large_pr_threshold: int = 15
    skip_extensions: str = (
        ".lock,.svg,.png,.jpg,.jpeg,.gif,.json,.csv,.md,.txt,.yaml,.yml"
    )

    # ── App metadata ────────────────────────────────────────────────────────
    enable_docs: bool = False
    app_version: str = "1.0.0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def skip_extensions_list(self) -> "list[str]":
        """Return skip_extensions as a list of lowercase strings."""
        return [
            ext.strip().lower()
            for ext in self.skip_extensions.split(",")
            if ext.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance (one per Lambda container)."""
    return Settings()

"""
app/services/openai_review.py

OpenAI code review service integrating GPT-4o.
Implements:
  - FR-09: Send each file diff independently with custom prompt.
  - FR-10: Request structured JSON, parsed and validated via Pydantic.
"""

import logging
from typing import List

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.models.review_schemas import ReviewComment

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior software engineer and security auditor reviewing code changes in a Pull Request.
Your task is to analyze the git diff for a single file and identify:
1. Logic bugs or errors.
2. Security vulnerabilities.
3. Performance bottlenecks or anti-patterns.
4. Clean code and style consistency issues (e.g. naming, redundancy, error handling).

For each issue identified, output a structured comment with:
- file: The exact filename provided in the context.
- line: The line number in the NEW version of the file (right side of the diff) where the issue is located or should be addressed.
- severity: One of: "bug", "warning", or "suggestion".
- comment: An actionable, specific explanation of the issue and how to fix it.

CRITICAL INSTRUCTIONS FOR LINE NUMBERS:
- The diff contains hunk headers of the format @@ -old_start,old_count +new_start,new_count @@.
- You must map your review comments to the correct line numbers in the *new* file (the post-change file).
- The first line of the new file's content in a hunk starts at `new_start`.
- As you scan the hunk, count lines: increment the line number for added lines (starting with '+') and unchanged context lines (starting with ' ').
- Do NOT increment the line number for deleted lines (starting with '-'). Deleted lines do not exist in the new file, so you cannot post comments on them.
- If an issue is related to a deleted line, target the line number in the new file immediately preceding or following the deletion.
- Make sure the targeted `line` is an integer >= 1 and actually exists in the new version of the file diff hunks. Do not target line numbers outside the diff hunks.

If no issues are found in the diff, return an empty comments list. Keep your comments constructive, concise, and professional.
"""


class FileReviewResponse(BaseModel):
    """Container for parsed structured comments returned by OpenAI."""

    comments: List[ReviewComment]


class OpenAIReviewService:
    """Service to interact with OpenAI API for structural code review."""

    def __init__(self, api_key: str, base_url: str = None) -> None:
        """Initialize the async OpenAI client."""
        # Note: If base_url is provided, it can be used for mock testing or proxying.
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def review_file_diff(
        self,
        filename: str,
        patch: str,
        model: str = "gpt-4o",
    ) -> List[ReviewComment]:
        """Send a single file diff to OpenAI and return parsed comments.

        Implements FR-09 and FR-10.
        """
        if not patch:
            logger.info("Empty patch for file %s. Skipping review.", filename)
            return []

        user_content = f"Filename: {filename}\n\nDiff Patch:\n```diff\n{patch}\n```"

        try:
            logger.info(
                "Sending review request for '%s' to OpenAI (model: %s)",
                filename,
                model,
            )
            completion = await self.client.beta.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format=FileReviewResponse,
            )

            response_data = completion.choices[0].message.parsed
            if response_data and response_data.comments:
                # Force correct filename in case AI hallucinates a different one.
                for comment in response_data.comments:
                    comment.file = filename
                logger.info(
                    "OpenAI completed review for %s: found %d comments.",
                    filename,
                    len(response_data.comments),
                )
                return response_data.comments

            logger.info("OpenAI completed review for %s: no issues found.", filename)
            return []

        except Exception as exc:
            logger.exception("Failed to review file %s via OpenAI API.", filename)
            raise exc

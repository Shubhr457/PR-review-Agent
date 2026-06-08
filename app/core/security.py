"""
app/core/security.py

HMAC-SHA256 webhook signature verification.

GitHub signs every webhook payload with the shared secret you configure in
the repository webhook settings.  The signature is sent in the
X-Hub-Signature-256 header as:

    sha256=<hex_digest>

We verify it using hmac.compare_digest (constant-time) to prevent
timing-oracle attacks.
"""

import hashlib
import hmac
from typing import Optional

from fastapi import HTTPException, status


def verify_webhook_signature(
    payload_body: bytes,
    secret: str,
    signature_header: Optional[str],
) -> None:
    """
    Validate a GitHub webhook signature.

    Args:
        payload_body:      Raw request body bytes.
        secret:            The webhook secret configured in GitHub.
        signature_header:  Value of the X-Hub-Signature-256 header.

    Raises:
        HTTPException 401: If the header is missing or the digest does not match.
    """
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Hub-Signature-256 header.",
        )

    expected_digest = hmac.new(
        key=secret.encode(),
        msg=payload_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    expected_signature = f"sha256={expected_digest}"

    if not hmac.compare_digest(signature_header, expected_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )

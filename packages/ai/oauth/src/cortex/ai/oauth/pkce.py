"""PKCE utilities using Python's cryptography library.

Mechanical port of hoocode's ``packages/ai/src/utils/oauth/pkce.ts``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets


def _base64url_encode(data: bytes) -> str:
    """Encode bytes as base64url string."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


async def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge.

    Returns:
        Tuple of (verifier, challenge) strings.
    """
    # Generate random verifier (32 bytes = 256 bits of entropy)
    verifier_bytes = secrets.token_bytes(32)
    verifier = _base64url_encode(verifier_bytes)

    # Compute SHA-256 challenge
    challenge_bytes = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = _base64url_encode(challenge_bytes)

    return verifier, challenge

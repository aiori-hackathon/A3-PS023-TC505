# CleanSlot SafeCore - Worker Authentication Module
# Protects internal worker endpoints with constant-time Bearer token verification.
# Reads shared secret from CLEANSLOT_TOKEN environment variable without logging it.
# Rejects unauthorized requests from outside or misconfigured controllers.

import os
import hmac
from typing import Optional
from fastapi import Header, HTTPException, status


def get_expected_token() -> str:
    """Retrieve expected token from environment without logging it."""
    return os.environ.get("CLEANSLOT_TOKEN", "cleanslot-secret-token")


def verify_worker_auth(authorization: Optional[str] = Header(None)) -> str:
    """
    Validate Authorization Bearer token header on worker requests.
    Uses hmac.compare_digest for constant-time comparison.
    Never prints or logs the token.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    expected_token = get_expected_token()

    if not hmac.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token

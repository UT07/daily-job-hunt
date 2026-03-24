"""FastAPI auth middleware for Supabase JWT validation.

Extracts and validates JWT tokens from the Authorization header.
Requires the SUPABASE_JWT_SECRET environment variable to be set.
"""

import logging
import os
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Bearer token extraction scheme
_bearer_scheme = HTTPBearer(auto_error=False)

# Supabase JWT config
_ALGORITHM = "HS256"
_AUDIENCE = "authenticated"


class AuthUser(BaseModel):
    """Authenticated user extracted from a valid Supabase JWT."""

    id: str
    email: str


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> AuthUser:
    """FastAPI dependency that validates a Supabase JWT and returns the user.

    Usage:
        @app.get("/protected")
        def protected(user: AuthUser = Depends(get_current_user)):
            ...

    Raises HTTPException 401 if the token is missing, expired, or invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        logger.error("SUPABASE_JWT_SECRET is not set")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Auth not configured",
        )

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[_ALGORITHM],
            audience=_AUDIENCE,
        )
    except JWTError as e:
        logger.warning("JWT validation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    email = payload.get("email")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user ID",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthUser(id=user_id, email=email or "")

"""FastAPI auth middleware for Supabase JWT validation.

Supports both HS256 (legacy) and ES256 (new default) Supabase projects.
Requires SUPABASE_JWT_SECRET for HS256, or fetches JWKS for ES256.
"""

import logging
import os
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt, jwk
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)

# Cache for JWKS keys
_jwks_cache: Optional[dict] = None


class AuthUser(BaseModel):
    """Authenticated user extracted from a valid Supabase JWT."""
    id: str
    email: str


def _get_jwks() -> dict:
    """Fetch JWKS (JSON Web Key Set) from Supabase for ES256 verification."""
    global _jwks_cache
    if _jwks_cache:
        return _jwks_cache

    supabase_url = os.environ.get("SUPABASE_URL", "")
    if not supabase_url:
        return {}

    import requests
    try:
        resp = requests.get(f"{supabase_url}/auth/v1/.well-known/jwks.json", timeout=10)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        logger.info("[AUTH] Fetched JWKS from Supabase (%d keys)", len(_jwks_cache.get("keys", [])))
        return _jwks_cache
    except Exception as e:
        logger.warning("[AUTH] Failed to fetch JWKS: %s", e)
        return {}


def _decode_token(token: str) -> dict:
    """Decode and verify a Supabase JWT, supporting both HS256 and ES256."""
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "HS256")

    if alg == "HS256":
        secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        if not secret:
            raise JWTError("SUPABASE_JWT_SECRET not set for HS256 token")
        return jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")

    elif alg in ("ES256", "RS256"):
        jwks_data = _get_jwks()
        keys = jwks_data.get("keys", [])
        kid = header.get("kid")

        if not keys:
            raise JWTError(f"No JWKS keys available for {alg} verification")

        # Find matching key by kid
        key_data = None
        for k in keys:
            if k.get("kid") == kid:
                key_data = k
                break

        if not key_data:
            # Try first key if no kid match
            key_data = keys[0] if keys else None

        if not key_data:
            raise JWTError(f"No matching JWKS key found for kid={kid}")

        public_key = jwk.construct(key_data)
        return jwt.decode(
            token,
            public_key,
            algorithms=[alg],
            audience="authenticated",
        )
    else:
        raise JWTError(f"Unsupported algorithm: {alg}")


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> AuthUser:
    """FastAPI dependency that validates a Supabase JWT and returns the user."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        payload = _decode_token(token)
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

"""hms_auth — authentication dependency producing a RequestContext.

Validates OIDC JWT tokens issued by Keycloak (IAM-001, IAM-006). If OIDC environment
variables are not configured, falls back to the development mock token parser
to facilitate local development and testing.
"""

import logging
import os
import time
from typing import Any

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, Request
from hms_tenancy import RequestContext, current_tenant_id
from jwt import PyJWK
from jwt.exceptions import ExpiredSignatureError, InvalidSignatureError, InvalidTokenError

logger = logging.getLogger("hms_auth")

# Load settings from environment variables
OIDC_ISSUER = os.environ.get("OIDC_ISSUER")
OIDC_AUDIENCE = os.environ.get("OIDC_AUDIENCE")
OIDC_JWKS_URI = os.environ.get("OIDC_JWKS_URI")
ALLOW_DEV_TOKENS = os.environ.get("ALLOW_DEV_TOKENS", "").lower() in ("true", "1") or os.environ.get("ENV", "development").lower() in ("development", "test")

# Fallback to dev mode if OIDC configuration is incomplete
DEV_MODE = not (OIDC_ISSUER and OIDC_AUDIENCE)

_DEV_ROLES = {"admin", "receptionist", "physician", "nurse", "billing", "operator", "billing_clerk", "finance_manager"}

# In-memory cache for JWKS keys to avoid requesting certs on every request
_jwks_cache: dict[str, Any] = {
    "keys": {},
    "expires_at": 0.0
}
CACHE_TTL = 3600.0  # 1 hour cache lifetime


async def get_jwks_keys() -> dict[str, Any]:
    """Retrieve the JSON Web Key Set (JWKS) public keys, utilizing an in-memory cache."""
    global _jwks_cache
    now = time.time()
    if _jwks_cache["keys"] and now < _jwks_cache["expires_at"]:
        return _jwks_cache["keys"]

    jwks_uri = OIDC_JWKS_URI
    if not jwks_uri and OIDC_ISSUER:
        # Discover JWKS endpoint via OIDC discovery document
        well_known_url = f"{OIDC_ISSUER.rstrip('/')}/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(well_known_url, timeout=5.0)
                resp.raise_for_status()
                jwks_uri = resp.json().get("jwks_uri")
        except Exception as e:
            # If discovery fails but we have cached keys, reuse them as fallback
            if _jwks_cache["keys"]:
                return _jwks_cache["keys"]
            raise HTTPException(
                status_code=500,
                detail=f"OIDC discovery metadata resolution failed: {e!s}"
            )

    if not jwks_uri:
        raise HTTPException(status_code=500, detail="OIDC JWKS URI could not be resolved")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(jwks_uri, timeout=5.0)
            resp.raise_for_status()
            jwks_data = resp.json()
            keys = {key["kid"]: key for key in jwks_data.get("keys", []) if "kid" in key}
            _jwks_cache["keys"] = keys
            _jwks_cache["expires_at"] = now + CACHE_TTL
            return keys
    except Exception as e:
        if _jwks_cache["keys"]:
            return _jwks_cache["keys"]
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch OIDC JWKS public keys: {e!s}"
        )


async def get_context(
    request: Request,
    authorization: str | None = Header(default=None),
) -> RequestContext:
    """Resolve the acting tenant/user/role from the bearer token.

    Validates the bearer token signature, expiration, issuer, and audience.
    Never reads tenant_id from user-controlled headers or body (PLT-002).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()

    env_mode = os.getenv("ENV", "development").lower()
    allow_dev = os.getenv("ALLOW_DEV_TOKENS", "").lower() in ("true", "1") or env_mode in ("development", "dev", "test")
    if token.startswith("dev."):
        if not allow_dev or env_mode in ("production", "staging"):
            logger.error("AUTH_FAILURE: Dev auth token fallback attempted outside allowed dev environment (ENV=%s).", env_mode)
            raise HTTPException(
                status_code=401,
                detail="AUTH_FAILURE: Dev auth tokens forbidden in this environment. Real OIDC token required."
            )
        # DEV/TEST ONLY: `dev.<tenant>.<role>`
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "dev":
            logger.warning("AUTH_FAILURE: Invalid dev token structure: %s", token)
            raise HTTPException(status_code=401, detail="invalid dev token")
        _, tenant_id, role = parts
        if role not in _DEV_ROLES:
            logger.warning("AUTH_FAILURE: Dev token contains unauthorized role '%s'", role)
            raise HTTPException(status_code=403, detail=f"unknown role '{role}'")
        current_tenant_id.set(tenant_id)
        return RequestContext(
            tenant_id=tenant_id,
            user_id=f"{role}@{tenant_id}",
            role=role
        )

    # Real OIDC token validation
    try:
        # Extract the key ID (kid) from the JWT header without verifying the token signature yet
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid:
            logger.warning("AUTH_FAILURE: Bearer token missing 'kid' in header.")
            raise HTTPException(status_code=401, detail="missing key id (kid) in token header")

        keys = await get_jwks_keys()
        
        # If kid is missing from cache, force reload once to support rotated keys
        if kid not in keys:
            global _jwks_cache
            _jwks_cache["expires_at"] = 0.0
            keys = await get_jwks_keys()
            if kid not in keys:
                logger.warning("AUTH_FAILURE: Unknown key ID (kid=%s) in token.", kid)
                raise HTTPException(status_code=401, detail="unknown key id (kid) in token")

        # Convert JWK to public key
        jwk = keys[kid]
        public_key = PyJWK(jwk).key

        # Decode and verify signature & issuer
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False, "verify_iss": False}
        )

        iss = payload.get("iss")
        if OIDC_ISSUER:
            valid_issuers = [
                OIDC_ISSUER,
                OIDC_ISSUER.replace("keycloak", "localhost"),
                OIDC_ISSUER.replace("keycloak", "127.0.0.1"),
                OIDC_ISSUER.replace("localhost", "127.0.0.1"),
                OIDC_ISSUER.replace("localhost", "keycloak"),
                OIDC_ISSUER.replace("127.0.0.1", "localhost"),
                OIDC_ISSUER.replace("127.0.0.1", "keycloak"),
                "http://localhost:8080/realms/hms",
                "http://127.0.0.1:8080/realms/hms",
                "http://keycloak:8080/realms/hms",
                "https://stage.zensynq.com/auth/realms/hms",
                "https://stage.zensynq.com/realms/hms",
                "http://stage.zensynq.com/auth/realms/hms",
                "http://stage.zensynq.com/realms/hms",
            ]
            if iss and (iss in valid_issuers or iss.endswith("/realms/hms")):
                pass
            else:
                raise jwt.InvalidIssuerError(f"Invalid issuer: {iss}")

        # Verify audience (aud claim or authorized party azp claim)
        aud_claims = payload.get("aud")
        if isinstance(aud_claims, str):
            aud_claims = [aud_claims]
        elif not isinstance(aud_claims, list):
            aud_claims = []
        azp_claim = payload.get("azp")
        
        if OIDC_AUDIENCE and OIDC_AUDIENCE not in aud_claims and azp_claim != OIDC_AUDIENCE:
            logger.warning("AUTH_FAILURE: Token audience '%s' or azp '%s' does not match expected OIDC_AUDIENCE '%s'", aud_claims, azp_claim, OIDC_AUDIENCE)
            raise HTTPException(status_code=401, detail="invalid token audience")
    except ExpiredSignatureError:
        logger.warning("AUTH_FAILURE: Bearer token expired.")
        raise HTTPException(status_code=401, detail="token signature has expired")
    except (InvalidSignatureError, InvalidTokenError) as e:
        logger.warning("AUTH_FAILURE: Invalid token signature or claims: %s", e)
        raise HTTPException(status_code=401, detail=f"invalid token signature or claims: {e!s}")
    except Exception as e:
        logger.warning("AUTH_FAILURE: Token validation failed: %s", e)
        raise HTTPException(status_code=401, detail=f"token validation failed: {e!s}")

    # Extract tenant identification from claims (supports nested dict {"app": {"tenant_id": ...}}, string claim, etc.)
    tenant_id = None
    if isinstance(payload.get("app"), dict):
        tenant_id = payload["app"].get("tenant_id")
    if not tenant_id:
        tenant_id = payload.get("app.tenant_id") or payload.get("tenant_id") or payload.get("tenant")
    if not tenant_id:
        all_roles = (
            payload.get("realm_access", {}).get("roles", [])
            + (payload.get("roles") if isinstance(payload.get("roles"), list) else [])
        )
        if "operator" in all_roles or "admin" in all_roles:
            tenant_id = "platform_operator"
        else:
            raise HTTPException(status_code=401, detail="missing tenant identifier claim in token")

    # Extract user identity (standard OIDC subject claim)
    user_id = payload.get("sub") or payload.get("preferred_username")
    if not user_id:
        raise HTTPException(status_code=401, detail="missing subject claim in token")

    # Resolve roles from Keycloak or standard custom claims
    roles: list[str] = []
    
    # 1. Standard custom role claims
    if "roles" in payload:
        claims_roles = payload["roles"]
        if isinstance(claims_roles, list):
            roles.extend(claims_roles)
        elif isinstance(claims_roles, str):
            roles.append(claims_roles)
    if "role" in payload:
        roles.append(payload["role"])

    # 2. Keycloak realm-level roles
    realm_access = payload.get("realm_access", {})
    roles.extend(realm_access.get("roles", []))

    # 3. Keycloak client-level roles
    resource_access = payload.get("resource_access", {})
    for _client_id, access in resource_access.items():
        if isinstance(access, dict):
            roles.extend(access.get("roles", []))

    # Match against the allowed set of dev roles (map 'doctor' -> 'physician')
    ROLE_MAPPING = {"doctor": "physician"}
    matched_role = None
    for r in roles:
        mapped_r = ROLE_MAPPING.get(r, r)
        if mapped_r in _DEV_ROLES:
            matched_role = mapped_r
            break

    if not matched_role:
        raise HTTPException(status_code=403, detail="user does not have a valid role mapping")

    current_tenant_id.set(tenant_id)
    return RequestContext(tenant_id=tenant_id, user_id=user_id, role=matched_role)


# Convenience alias for routers: `ctx: RequestContext = Depends(auth)`
auth = get_context


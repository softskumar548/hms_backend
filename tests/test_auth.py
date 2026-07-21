from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest

import hms_auth
from hms_tenancy import RequestContext

# Pre-generate RSA key pair for testing
PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend()
)
import json
PUBLIC_KEY = PRIVATE_KEY.public_key()
jwk_str = RSAAlgorithm.to_jwk(PUBLIC_KEY)
if isinstance(jwk_str, bytes):
    jwk_str = jwk_str.decode("utf-8")
JWK = json.loads(jwk_str)
JWK.update({"kid": "test-kid", "alg": "RS256", "use": "sig"})

ALT_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend()
)


@pytest.fixture
def mock_request():
    req = AsyncMock(spec=Request)
    return req


@pytest.fixture(autouse=True)
def _provision_test_tenants():
    """Override the database provisioning fixture to do nothing, as auth tests don't require DB."""
    pass


@pytest.mark.asyncio
async def test_dev_mode_fallback_success(mock_request):
    """Test fallback dev token parser behaves correctly when OIDC is not configured."""
    with patch("hms_auth.DEV_MODE", True):
        ctx = await hms_auth.get_context(mock_request, "Bearer dev.apollo.admin")
        assert ctx.tenant_id == "apollo"
        assert ctx.user_id == "admin@apollo"
        assert ctx.role == "admin"


@pytest.mark.asyncio
async def test_dev_mode_fallback_failures(mock_request):
    """Test fallback dev mode raises appropriate HTTP exceptions for malformed inputs."""
    with patch("hms_auth.DEV_MODE", True):
        # Missing header
        with pytest.raises(HTTPException) as exc:
            await hms_auth.get_context(mock_request, None)
        assert exc.value.status_code == 401

        # Malformed header
        with pytest.raises(HTTPException) as exc:
            await hms_auth.get_context(mock_request, "Bearer dev.apollo")
        assert exc.value.status_code == 401

        # Invalid role
        with pytest.raises(HTTPException) as exc:
            await hms_auth.get_context(mock_request, "Bearer dev.apollo.attacker")
        assert exc.value.status_code == 403


@pytest.mark.asyncio
@patch("hms_auth.get_jwks_keys")
async def test_oidc_validation_success(mock_get_keys, mock_request):
    """Test successful token validation with verified RSA signature and standard claims."""
    mock_get_keys.return_value = {"test-kid": JWK}

    with patch("hms_auth.DEV_MODE", False), \
         patch("hms_auth.OIDC_ISSUER", "https://auth.example.com"), \
         patch("hms_auth.OIDC_AUDIENCE", "hms-api"):
        
        # Generate valid signed JWT
        now = int(time.time())
        claims = {
            "iss": "https://auth.example.com",
            "aud": "hms-api",
            "sub": "user_abc",
            "tenant_id": "tenant_1",
            "roles": ["physician"],
            "exp": now + 300,
        }
        token = jwt.encode(
            claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-kid"}
        )

        ctx = await hms_auth.get_context(mock_request, f"Bearer {token}")
        assert ctx.tenant_id == "tenant_1"
        assert ctx.user_id == "user_abc"
        assert ctx.role == "physician"


@pytest.mark.asyncio
@patch("hms_auth.get_jwks_keys")
async def test_oidc_validation_expired(mock_get_keys, mock_request):
    """Test that expired tokens raise a 401 signature expired exception."""
    mock_get_keys.return_value = {"test-kid": JWK}

    with patch("hms_auth.DEV_MODE", False), \
         patch("hms_auth.OIDC_ISSUER", "https://auth.example.com"), \
         patch("hms_auth.OIDC_AUDIENCE", "hms-api"):
        
        now = int(time.time())
        claims = {
            "iss": "https://auth.example.com",
            "aud": "hms-api",
            "sub": "user_abc",
            "tenant_id": "tenant_1",
            "roles": ["physician"],
            "exp": now - 10,  # Expired
        }
        token = jwt.encode(
            claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-kid"}
        )

        with pytest.raises(HTTPException) as exc:
            await hms_auth.get_context(mock_request, f"Bearer {token}")
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail


@pytest.mark.asyncio
@patch("hms_auth.get_jwks_keys")
async def test_oidc_validation_invalid_issuer(mock_get_keys, mock_request):
    """Test that issuer mismatch fails validation."""
    mock_get_keys.return_value = {"test-kid": JWK}

    with patch("hms_auth.DEV_MODE", False), \
         patch("hms_auth.OIDC_ISSUER", "https://auth.example.com"), \
         patch("hms_auth.OIDC_AUDIENCE", "hms-api"):
        
        claims = {
            "iss": "https://attacker.com",  # Issuer mismatch
            "aud": "hms-api",
            "sub": "user_abc",
            "tenant_id": "tenant_1",
            "roles": ["physician"],
            "exp": int(time.time()) + 300,
        }
        token = jwt.encode(
            claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-kid"}
        )

        with pytest.raises(HTTPException) as exc:
            await hms_auth.get_context(mock_request, f"Bearer {token}")
        assert exc.value.status_code == 401
        assert "issuer" in exc.value.detail or "claims" in exc.value.detail


@pytest.mark.asyncio
@patch("hms_auth.get_jwks_keys")
async def test_oidc_validation_invalid_audience(mock_get_keys, mock_request):
    """Test that audience mismatch fails validation."""
    mock_get_keys.return_value = {"test-kid": JWK}

    with patch("hms_auth.DEV_MODE", False), \
         patch("hms_auth.OIDC_ISSUER", "https://auth.example.com"), \
         patch("hms_auth.OIDC_AUDIENCE", "hms-api"):
        
        claims = {
            "iss": "https://auth.example.com",
            "aud": "attacker-client",  # Audience mismatch
            "sub": "user_abc",
            "tenant_id": "tenant_1",
            "roles": ["physician"],
            "exp": int(time.time()) + 300,
        }
        token = jwt.encode(
            claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-kid"}
        )

        with pytest.raises(HTTPException) as exc:
            await hms_auth.get_context(mock_request, f"Bearer {token}")
        assert exc.value.status_code == 401
        assert "audience" in exc.value.detail or "claims" in exc.value.detail


@pytest.mark.asyncio
@patch("hms_auth.get_jwks_keys")
async def test_oidc_validation_invalid_signature(mock_get_keys, mock_request):
    """Test that tokens signed by an untrusted key fail validation."""
    mock_get_keys.return_value = {"test-kid": JWK}

    with patch("hms_auth.DEV_MODE", False), \
         patch("hms_auth.OIDC_ISSUER", "https://auth.example.com"), \
         patch("hms_auth.OIDC_AUDIENCE", "hms-api"):
        
        claims = {
            "iss": "https://auth.example.com",
            "aud": "hms-api",
            "sub": "user_abc",
            "tenant_id": "tenant_1",
            "roles": ["physician"],
            "exp": int(time.time()) + 300,
        }
        # Sign with ALT_PRIVATE_KEY, which is not in JWKS
        token = jwt.encode(
            claims, ALT_PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-kid"}
        )

        with pytest.raises(HTTPException) as exc:
            await hms_auth.get_context(mock_request, f"Bearer {token}")
        assert exc.value.status_code == 401
        assert "signature" in exc.value.detail


@pytest.mark.asyncio
@patch("hms_auth.get_jwks_keys")
async def test_oidc_validation_roles_structures(mock_get_keys, mock_request):
    """Test OIDC mapping handles realm-level, client-level, and custom role claim structures."""
    mock_get_keys.return_value = {"test-kid": JWK}

    with patch("hms_auth.DEV_MODE", False), \
         patch("hms_auth.OIDC_ISSUER", "https://auth.example.com"), \
         patch("hms_auth.OIDC_AUDIENCE", "hms-api"):
        
        # Test Case 1: Custom string "role" claim
        claims = {
            "iss": "https://auth.example.com",
            "aud": "hms-api",
            "sub": "user_1",
            "tenant_id": "tenant_1",
            "role": "nurse",
            "exp": int(time.time()) + 300,
        }
        token = jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-kid"})
        ctx = await hms_auth.get_context(mock_request, f"Bearer {token}")
        assert ctx.role == "nurse"

        # Test Case 2: Keycloak realm-level roles
        claims = {
            "iss": "https://auth.example.com",
            "aud": "hms-api",
            "sub": "user_2",
            "tenant_id": "tenant_1",
            "realm_access": {"roles": ["offline_access", "receptionist"]},
            "exp": int(time.time()) + 300,
        }
        token = jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-kid"})
        ctx = await hms_auth.get_context(mock_request, f"Bearer {token}")
        assert ctx.role == "receptionist"

        # Test Case 3: Keycloak client-level roles
        claims = {
            "iss": "https://auth.example.com",
            "aud": "hms-api",
            "sub": "user_3",
            "tenant_id": "tenant_1",
            "resource_access": {
                "hms-api": {"roles": ["billing"]},
                "other-client": {"roles": ["admin"]}
            },
            "exp": int(time.time()) + 300,
        }
        token = jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-kid"})
        ctx = await hms_auth.get_context(mock_request, f"Bearer {token}")
        assert ctx.role == "billing"


@pytest.mark.asyncio
@patch("hms_auth.get_jwks_keys")
async def test_oidc_validation_no_matching_roles(mock_get_keys, mock_request):
    """Test that a token without matching valid roles raises a 403 forbidden exception."""
    mock_get_keys.return_value = {"test-kid": JWK}

    with patch("hms_auth.DEV_MODE", False), \
         patch("hms_auth.OIDC_ISSUER", "https://auth.example.com"), \
         patch("hms_auth.OIDC_AUDIENCE", "hms-api"):
        
        claims = {
            "iss": "https://auth.example.com",
            "aud": "hms-api",
            "sub": "user_abc",
            "tenant_id": "tenant_1",
            "roles": ["unauthorized_role"],
            "exp": int(time.time()) + 300,
        }
        token = jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-kid"})

        with pytest.raises(HTTPException) as exc:
            await hms_auth.get_context(mock_request, f"Bearer {token}")
        assert exc.value.status_code == 403
        assert "role mapping" in exc.value.detail

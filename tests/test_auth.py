"""Tests for ASGI AuthMiddleware, HMAC-signed tokens, and OAuth 2.0 Dynamic Client Registration."""

from unittest.mock import patch

from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from src.auth import (
    AuthMiddleware,
    create_signed_refresh_token,
    create_signed_token,
    handle_oauth_authorize,
    handle_oauth_jwks,
    handle_oauth_metadata,
    handle_oauth_protected_resource,
    handle_oauth_register,
    handle_oauth_token,
    verify_signed_refresh_token,
    verify_signed_token,
)


def create_test_app():
    """Create a minimal Starlette app protected by AuthMiddleware."""
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.routing import Route

    routes = [
        Route("/protected", endpoint=lambda r: PlainTextResponse("secret_data"), methods=["GET"]),
        Route(
            "/.well-known/oauth-authorization-server",
            endpoint=handle_oauth_metadata,
            methods=["GET", "OPTIONS"],
        ),
        Route(
            "/.well-known/oauth-protected-resource",
            endpoint=handle_oauth_protected_resource,
            methods=["GET", "OPTIONS"],
        ),
        Route("/oauth/register", endpoint=handle_oauth_register, methods=["POST", "OPTIONS"]),
        Route("/oauth/authorize", endpoint=handle_oauth_authorize, methods=["GET", "POST"]),
        Route("/oauth/token", endpoint=handle_oauth_token, methods=["POST", "OPTIONS"]),
        Route("/oauth/jwks", endpoint=handle_oauth_jwks, methods=["GET"]),
        Route("/healthz", endpoint=lambda r: PlainTextResponse("ok"), methods=["GET"]),
        Route("/livez", endpoint=lambda r: PlainTextResponse("alive"), methods=["GET"]),
    ]

    middleware = [Middleware(AuthMiddleware, api_key="valid-test-key", auth_mode="api_key")]
    return Starlette(routes=routes, middleware=middleware)


def test_public_routes_bypass_auth():
    """Verify health checks and metadata routes do not require auth."""
    client = TestClient(create_test_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200

    resp_live = client.get("/livez")
    assert resp_live.status_code == 200

    resp_meta = client.get("/.well-known/oauth-authorization-server")
    assert resp_meta.status_code == 200
    data = resp_meta.json()
    assert "authorization_endpoint" in data
    assert "jwks_uri" in data
    assert "code_challenge_methods_supported" in data


def test_oauth_protected_resource_endpoint():
    """Verify RFC 9470 Protected Resource Metadata is served."""
    client = TestClient(create_test_app())
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    data = resp.json()
    assert "resource" in data
    assert "authorization_servers" in data
    assert "bearer_methods_supported" in data


def test_protected_route_unauthorized_without_key():
    """Verify protected routes reject requests missing authentication."""
    client = TestClient(create_test_app())
    resp = client.get("/protected")
    assert resp.status_code == 401


def test_protected_route_with_bearer_key():
    """Verify protected routes succeed with valid Authorization Bearer key."""
    client = TestClient(create_test_app())
    headers = {"Authorization": "Bearer valid-test-key"}
    resp = client.get("/protected", headers=headers)
    assert resp.status_code == 200
    assert resp.text == "secret_data"


def test_protected_route_with_custom_header():
    """Verify protected routes succeed with X-MCP-API-Key header."""
    client = TestClient(create_test_app())
    headers = {"X-MCP-API-Key": "valid-test-key"}
    resp = client.get("/protected", headers=headers)
    assert resp.status_code == 200
    assert resp.text == "secret_data"


def test_hmac_signed_token_creation_and_verification():
    """Verify HMAC-signed stateless tokens are created and verified correctly."""
    token = create_signed_token(client_id="test-client-123")
    assert token.startswith("mcp_tok_")
    assert verify_signed_token(token) is True

    # Invalid token should fail
    assert verify_signed_token("invalid_token") is False
    assert verify_signed_token("mcp_tok_bad_sig") is False
    assert verify_signed_token("") is False


def test_hmac_signed_refresh_token_creation_and_verification():
    """Verify HMAC-signed stateless refresh tokens are created and verified correctly."""
    token = create_signed_refresh_token(client_id="test-client-456")
    assert token.startswith("mcp_refresh_")
    is_valid, client_id = verify_signed_refresh_token(token)
    assert is_valid is True
    assert client_id == "test-client-456"

    # Invalid refresh token should fail
    assert verify_signed_refresh_token("invalid_refresh") == (False, "")
    assert verify_signed_refresh_token("mcp_refresh_bad_sig") == (False, "")
    assert verify_signed_refresh_token("") == (False, "")

    # Access token must NOT verify as refresh token
    access_tok = create_signed_token(client_id="test")
    assert verify_signed_refresh_token(access_tok) == (False, "")


def test_hmac_signed_token_auth():
    """Verify HMAC-signed OAuth tokens authenticate protected routes."""
    token = create_signed_token(client_id="chatgpt-test")
    client = TestClient(create_test_app())
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/protected", headers=headers)
    assert resp.status_code == 200
    assert resp.text == "secret_data"


def test_oauth_dynamic_client_registration_and_full_flow():
    """Verify full ChatGPT OAuth 2.0 flow: register → authorize → token → access."""
    client = TestClient(create_test_app())

    # 1. Register dynamic client
    reg_resp = client.post("/oauth/register", json={"client_name": "ChatGPT-Test-Client"})
    assert reg_resp.status_code == 201
    reg_data = reg_resp.json()
    assert "client_id" in reg_data
    assert "client_secret" in reg_data

    # 2. Authorize — verify consent screen appears without password
    import os
    with patch.dict(os.environ, {"MCP_API_KEY": "valid-test-key"}):
        consent_resp = client.get(
            "/oauth/authorize",
            params={
                "redirect_uri": "https://chatgpt.example.com/callback",
                "state": "test-state-123",
                "client_id": reg_data["client_id"],
            },
            follow_redirects=False,
        )
        assert consent_resp.status_code == 200
        assert "Authorize Ask-a-Friend MCP" in consent_resp.text
        assert 'name="admin_password"' in consent_resp.text

        # Invalid password returns 401 with error message
        bad_auth = client.post(
            "/oauth/authorize",
            data={
                "redirect_uri": "https://chatgpt.example.com/callback",
                "state": "test-state-123",
                "client_id": reg_data["client_id"],
                "admin_password": "wrong-password",
            },
            follow_redirects=False,
        )
        assert bad_auth.status_code == 401
        assert "Invalid Admin Password" in bad_auth.text

        # Valid password authorizes and redirects with code
        auth_resp = client.post(
            "/oauth/authorize",
            data={
                "redirect_uri": "https://chatgpt.example.com/callback",
                "state": "test-state-123",
                "client_id": reg_data["client_id"],
                "admin_password": "valid-test-key",
            },
            follow_redirects=False,
        )
        assert auth_resp.status_code == 302
        location = auth_resp.headers["location"]
        assert "code=mcp_code_" in location
        assert "state=test-state-123" in location

    # Extract code from redirect URL
    import urllib.parse

    parsed = urllib.parse.urlparse(location)
    query_params = urllib.parse.parse_qs(parsed.query)
    code = query_params["code"][0]

    # 3. Exchange code for token
    token_resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": reg_data["client_id"],
        },
    )
    assert token_resp.status_code == 200
    token_data = token_resp.json()
    access_token = token_data["access_token"]
    assert access_token.startswith("mcp_tok_")
    assert "refresh_token" in token_data

    # 4. Access protected route using acquired OAuth token
    headers = {"Authorization": f"Bearer {access_token}"}
    prot_resp = client.get("/protected", headers=headers)
    assert prot_resp.status_code == 200
    assert prot_resp.text == "secret_data"

    # 5. Verify refresh token is HMAC-signed and valid
    refresh_token = token_data["refresh_token"]
    assert refresh_token.startswith("mcp_refresh_")
    is_valid, rt_client_id = verify_signed_refresh_token(refresh_token)
    assert is_valid is True

    # 6. Exchange refresh token for new access token
    refresh_resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": reg_data["client_id"],
        },
    )
    assert refresh_resp.status_code == 200
    refresh_data = refresh_resp.json()
    assert refresh_data["access_token"].startswith("mcp_tok_")
    assert refresh_data["refresh_token"].startswith("mcp_refresh_")


def test_oauth_token_rejects_invalid_code():
    """Verify token endpoint rejects invalid authorization codes."""
    client = TestClient(create_test_app())
    token_resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": "fake_invalid_code",
            "client_id": "test",
        },
    )
    assert token_resp.status_code == 400
    assert token_resp.json()["error"] == "invalid_grant"


def test_oauth_token_rejects_replay():
    """Verify authorization codes are one-time use (replay protection)."""
    client = TestClient(create_test_app())

    # Get a valid code
    import os
    with patch.dict(os.environ, {"MCP_API_KEY": "valid-test-key"}):
        auth_resp = client.get(
            "/oauth/authorize",
            params={
                "redirect_uri": "https://example.com/callback",
                "state": "s",
                "admin_password": "valid-test-key",
            },
            follow_redirects=False,
        )
    import urllib.parse

    location = auth_resp.headers["location"]
    parsed = urllib.parse.urlparse(location)
    query_params = urllib.parse.parse_qs(parsed.query)
    code = query_params["code"][0]

    # First exchange — should succeed
    resp1 = client.post("/oauth/token", json={"grant_type": "authorization_code", "code": code})
    assert resp1.status_code == 200

    # Second exchange with same code — should fail (one-time use)
    resp2 = client.post("/oauth/token", json={"grant_type": "authorization_code", "code": code})
    assert resp2.status_code == 400
    assert resp2.json()["error"] == "invalid_grant"


def test_oauth_jwks_endpoint():
    """Verify JWKS endpoint returns empty keys (HMAC symmetric signing)."""
    client = TestClient(create_test_app())
    resp = client.get("/oauth/jwks")
    assert resp.status_code == 200
    assert resp.json() == {"keys": []}


def test_oauth_refresh_token_rejects_invalid():
    """Verify refresh token endpoint rejects invalid/forged refresh tokens."""
    client = TestClient(create_test_app())

    # Completely invalid token
    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": "invalid_garbage_token",
            "client_id": "attacker",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"

    # Empty refresh token
    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": "",
            "client_id": "attacker",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"

    # Forged token with mcp_refresh_ prefix but bad signature
    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": "mcp_refresh_forged_payload_badsig",
            "client_id": "attacker",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


# ─── Insecure Configuration Guards ───────────────────────────────────────────


def test_validate_auth_config_rejects_missing_key():
    """api_key/oauth2 modes must refuse to start without a shared secret."""
    import pytest

    from src.auth import AuthConfigError, validate_auth_config

    for mode in ("api_key", "oauth2"):
        for missing in (None, "", "   "):
            with pytest.raises(AuthConfigError, match="requires MCP_API_KEY"):
                validate_auth_config(mode, missing)


def test_validate_auth_config_allows_unauthenticated_modes():
    """'none' and 'iam' do not depend on MCP_API_KEY and must still start."""
    from src.auth import validate_auth_config

    validate_auth_config("none", None)
    validate_auth_config("iam", None)
    validate_auth_config("api_key", "a-sufficiently-long-test-key")


def test_no_hardcoded_fallback_signing_key():
    """The signing key must never fall back to a value published in source."""
    import os
    from unittest.mock import patch as _patch

    from src.auth import _derive_signing_key

    # With no key material configured, each derivation must be random.
    with _patch.dict(os.environ, {}, clear=True):
        first = _derive_signing_key()
        second = _derive_signing_key()

    assert first != second, "Unset key material must yield an ephemeral random key"
    assert len(first) == 32

    # Legacy published constant must not be accepted as key material.
    legacy = "ask-a-friend-default-signing-key-1.0.0"
    with _patch.dict(os.environ, {}, clear=True):
        assert _derive_signing_key() != legacy.encode()


def test_signing_key_is_domain_separated_from_api_key():
    """The raw API key must not be used directly as the HMAC signing key."""
    import os
    from unittest.mock import patch as _patch

    from src.auth import _derive_signing_key

    api_key = "aaf_super_secret_master_key_value"
    with _patch.dict(os.environ, {"MCP_API_KEY": api_key}, clear=True):
        derived = _derive_signing_key()

    assert derived != api_key.encode()
    assert len(derived) == 32

    # Derivation is deterministic, so tokens survive restarts and replicas.
    with _patch.dict(os.environ, {"MCP_API_KEY": api_key}, clear=True):
        assert _derive_signing_key() == derived


def test_oauth_signing_key_env_var_takes_precedence():
    """OAUTH_SIGNING_KEY allows rotating token signing independently."""
    import os
    from unittest.mock import patch as _patch

    from src.auth import _derive_signing_key

    with _patch.dict(
        os.environ, {"MCP_API_KEY": "the-api-key", "OAUTH_SIGNING_KEY": "the-signing-key"}, clear=True
    ):
        with_both = _derive_signing_key()
    with _patch.dict(os.environ, {"OAUTH_SIGNING_KEY": "the-signing-key"}, clear=True):
        signing_only = _derive_signing_key()

    assert with_both == signing_only


def test_oauth_authorize_fails_closed_without_admin_key():
    """Without MCP_API_KEY, /oauth/authorize must not mint authorization codes."""
    import os

    client = TestClient(create_test_app())

    with patch.dict(os.environ, {}, clear=True):
        resp = client.get(
            "/oauth/authorize",
            params={
                "redirect_uri": "https://attacker.example.com/callback",
                "state": "s",
                "client_id": "attacker",
            },
            follow_redirects=False,
        )

    # Must not redirect back with a usable code.
    assert resp.status_code == 503
    assert resp.json()["error"] == "server_error"
    assert "code=" not in resp.headers.get("location", "")


def test_oauth_authorize_still_requires_password_when_key_set():
    """With a key configured, a wrong password must not yield a code."""
    import os

    client = TestClient(create_test_app())

    with patch.dict(os.environ, {"MCP_API_KEY": "valid-test-key"}):
        resp = client.post(
            "/oauth/authorize",
            data={
                "redirect_uri": "https://attacker.example.com/callback",
                "state": "s",
                "client_id": "attacker",
                "admin_password": "wrong-password",
            },
            follow_redirects=False,
        )

    assert resp.status_code in (200, 401)
    assert "code=" not in resp.headers.get("location", "")


def test_legacy_signed_tokens_remain_valid_after_upgrade():
    """Tokens issued before domain separation must not be invalidated.

    Regression: changing the signing key derivation 403'd every already-connected
    OAuth client (e.g. a live Claude connector) until it re-authorized.
    """
    import base64
    import hashlib
    import hmac
    import os
    import time
    from unittest.mock import patch as _patch

    api_key = "aaf_a_real_operator_secret_value_here"

    with _patch.dict(os.environ, {"MCP_API_KEY": api_key}, clear=True):
        import importlib

        import src.auth as auth_mod

        importlib.reload(auth_mod)

        # Forge a token exactly the way the pre-upgrade server signed them:
        # the raw key material used directly as the HMAC key.
        payload = f"legacy-client:{int(time.time()) + 3600}"
        b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        legacy_sig = hmac.new(
            api_key.encode(), f"mcp_tok:{b64}".encode(), hashlib.sha256
        ).hexdigest()
        legacy_token = f"mcp_tok_{b64}_{legacy_sig}"

        assert auth_mod.verify_signed_token(legacy_token), (
            "Pre-upgrade token must still validate"
        )

        # A newly issued token must also validate.
        assert auth_mod.verify_signed_token(auth_mod.create_signed_token("new-client"))

        # The removed hardcoded constant must still never work.
        dead_key = b"ask-a-friend-default-signing-key-1.0.0"
        dead_sig = hmac.new(
            dead_key, f"mcp_tok:{b64}".encode(), hashlib.sha256
        ).hexdigest()
        assert not auth_mod.verify_signed_token(f"mcp_tok_{b64}_{dead_sig}"), (
            "Published fallback key must never validate"
        )

    # Restore module state for subsequent tests.
    import importlib

    import src.auth as auth_mod

    importlib.reload(auth_mod)

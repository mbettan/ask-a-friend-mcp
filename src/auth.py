"""Authentication Middleware and OAuth 2.0 Dynamic Client Registration.

Supports:
- API Key Bearer & X-MCP-API-Key with constant-time comparison (secrets.compare_digest)
- HMAC-SHA256 signed stateless OAuth tokens (survives container restarts on Cloud Run)
- RFC 7591 OAuth 2.0 Dynamic Client Registration for ChatGPT Native MCP Connector
- RFC 8414 OAuth Authorization Server Metadata with PKCE support
- RFC 9470 OAuth Protected Resource Metadata
- Authorization code validation with 10-minute expiry and one-time use
- Public route exemptions for documentation, health checks, and metadata discovery
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time
from typing import Any

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("ask_friend.auth")


def _signing_key_material() -> str:
    """Return the configured key material for OAuth token signing, if any."""
    return (
        os.environ.get("OAUTH_SIGNING_KEY") or os.environ.get("MCP_API_KEY") or ""
    ).strip()


def _derive_signing_key() -> bytes:
    """Derive the HMAC-SHA256 key used to sign stateless OAuth tokens.

    Key material is taken from OAUTH_SIGNING_KEY (preferred, allows rotating the
    token signing key independently of the API key) or MCP_API_KEY. The material
    is run through SHA-256 with a domain-separation prefix so the raw API key is
    never used directly as the signing key.

    If neither variable is set, a random per-process key is generated. Tokens
    then cannot be forged, but they will not survive a restart and will not
    validate across replicas — which is why `validate_auth_config()` refuses to
    start in auth modes that depend on a stable, shared key.
    """
    material = _signing_key_material()

    if material:
        return hashlib.sha256(
            b"ask-a-friend:oauth-token-signing:v1|" + material.encode()
        ).digest()

    logger.warning(
        "No OAUTH_SIGNING_KEY or MCP_API_KEY set — generating an ephemeral, "
        "per-process OAuth signing key. Issued tokens will be invalidated on "
        "restart and will not validate across replicas. Set MCP_API_KEY for any "
        "deployment that serves OAuth clients."
    )
    return secrets.token_bytes(32)


def _derive_legacy_signing_key() -> bytes | None:
    """Return the pre-v1 signing key, used for *verification only*.

    Before domain separation was introduced, the raw key material was used
    directly as the HMAC key. Existing clients (e.g. an already-connected
    ChatGPT or Claude connector) still hold tokens signed that way, and would
    otherwise get a 403 after upgrading until they re-ran the OAuth flow.

    This is safe: the legacy key is the operator's real secret, which is just as
    strong as the derived key. It is deliberately `None` when no material is
    configured, so the hardcoded fallback constant that used to live here can
    never be accepted again.

    Returns:
        The legacy key, or None if no key material is configured.
    """
    material = _signing_key_material()
    return material.encode() if material else None


# HMAC signing key for stateless OAuth tokens (see _derive_signing_key).
_OAUTH_SIGNING_KEY: bytes = _derive_signing_key()

# Accepted for verification only, to avoid invalidating already-issued tokens.
_LEGACY_OAUTH_SIGNING_KEY: bytes | None = _derive_legacy_signing_key()

# In-memory stores for registered OAuth clients & authorization codes
_REGISTERED_CLIENTS: dict[str, dict[str, Any]] = {}
_OAUTH_CODES: dict[str, dict[str, Any]] = {}

# Public route allowlist (bypasses auth checks)
PUBLIC_PREFIXES = (
    "/.well-known/",
    "/sse/.well-known/",
    "/mcp/.well-known/",
    "/oauth/",
    "/openapi.yaml",
    "/robots.txt",
    "/healthz",
    "/livez",
    "/favicon.ico",
)

# Minimum length for a credible shared secret (deploy.sh generates 52 chars).
MIN_API_KEY_LENGTH = 16

# Auth modes whose security depends entirely on a configured shared secret.
_SECRET_REQUIRED_MODES = ("api_key", "oauth2")


class AuthConfigError(RuntimeError):
    """Raised when the server is started with an insecure auth configuration."""


def validate_auth_config(auth_mode: str, api_key: str | None) -> None:
    """Fail fast when the configured auth mode cannot be enforced.

    In `api_key` and `oauth2` modes every protection in this module — the master
    key comparison, the OAuth consent gate, and HMAC token signing — is keyed off
    a single shared secret. If that secret is missing the server would accept
    unauthenticated OAuth authorizations and issue tokens signed with an
    ephemeral key, so refuse to start rather than expose the Vertex AI backend.

    Raises:
        AuthConfigError: if the mode requires a secret and none is configured.
    """
    mode = (auth_mode or "").lower()
    key = (api_key or "").strip()

    if mode in _SECRET_REQUIRED_MODES and not key:
        raise AuthConfigError(
            f"AUTH_MODE='{mode}' requires MCP_API_KEY to be set, but it is empty.\n"
            "Refusing to start: without it the server would accept unauthenticated "
            "OAuth authorization requests.\n"
            "Fix one of the following:\n"
            "  • Set a strong secret:  export MCP_API_KEY=\"aaf_$(openssl rand -hex 24)\"\n"
            "  • Deploy via ./deploy.sh, which provisions the 'mcp-api-key' secret\n"
            "  • For local, non-exposed development only: export AUTH_MODE=none"
        )

    if mode in _SECRET_REQUIRED_MODES and len(key) < MIN_API_KEY_LENGTH:
        logger.warning(
            "MCP_API_KEY is only %d characters; use at least %d "
            "(e.g. aaf_$(openssl rand -hex 24)) to resist brute forcing.",
            len(key),
            MIN_API_KEY_LENGTH,
        )

    if mode == "none":
        logger.warning(
            "AUTH_MODE=none — all authentication is disabled and every request "
            "reaches the Vertex AI backend. Never use this on a public endpoint."
        )

# ─── HMAC-Signed Stateless OAuth Tokens ──────────────────────────────────────


def _hmac_sign(prefix: str, b64_payload: str) -> str:
    """Compute full HMAC-SHA256 signature for a token payload."""
    return hmac.new(
        _OAUTH_SIGNING_KEY, f"{prefix}:{b64_payload}".encode(), hashlib.sha256
    ).hexdigest()


def _signature_matches(prefix: str, b64_payload: str, signature: str) -> bool:
    """Constant-time check of a token signature against current and legacy keys.

    New tokens are always signed with the current key. The legacy key is only
    consulted so that tokens issued before domain separation was introduced stay
    valid until they expire, sparing already-connected clients a forced
    re-authorization. Both branches are always evaluated so the comparison cost
    does not reveal which key matched.
    """
    message = f"{prefix}:{b64_payload}".encode()

    current = hmac.new(_OAUTH_SIGNING_KEY, message, hashlib.sha256).hexdigest()
    matched = secrets.compare_digest(signature, current)

    if _LEGACY_OAUTH_SIGNING_KEY is not None:
        legacy = hmac.new(
            _LEGACY_OAUTH_SIGNING_KEY, message, hashlib.sha256
        ).hexdigest()
        matched = secrets.compare_digest(signature, legacy) or matched

    return matched


def create_signed_token(client_id: str, lifetime_seconds: int = 2592000) -> str:
    """Create a cryptographically signed, stateless Bearer token (HMAC-SHA256).

    Token format: mcp_tok_{base64_payload}_{signature}
    Payload: {client_id}:{expires_at_unix_timestamp}
    """
    expires_at = int(time.time()) + lifetime_seconds
    raw_payload = f"{client_id}:{expires_at}"
    b64_payload = base64.urlsafe_b64encode(raw_payload.encode()).decode().rstrip("=")
    sig = _hmac_sign("mcp_tok", b64_payload)
    return f"mcp_tok_{b64_payload}_{sig}"


def create_signed_refresh_token(client_id: str, lifetime_seconds: int = 7776000) -> str:
    """Create a cryptographically signed, stateless refresh token (HMAC-SHA256).

    Token format: mcp_refresh_{base64_payload}_{signature}
    Payload: {client_id}:{expires_at_unix_timestamp}
    Default lifetime: 90 days.
    """
    expires_at = int(time.time()) + lifetime_seconds
    raw_payload = f"{client_id}:{expires_at}"
    b64_payload = base64.urlsafe_b64encode(raw_payload.encode()).decode().rstrip("=")
    sig = _hmac_sign("mcp_refresh", b64_payload)
    return f"mcp_refresh_{b64_payload}_{sig}"


def _verify_signed_token_impl(token: str, prefix: str) -> tuple[bool, str]:
    """Verify HMAC signature and expiration. Returns (is_valid, client_id)."""
    marker = f"{prefix}_"
    if not token or not token.startswith(marker):
        return False, ""
    remainder = token[len(marker):]
    sep_idx = remainder.rfind("_")
    if sep_idx == -1:
        return False, ""
    b64_payload = remainder[:sep_idx]
    signature = remainder[sep_idx + 1:]
    if not _signature_matches(prefix, b64_payload, signature):
        return False, ""
    try:
        padded = b64_payload + "=" * (-len(b64_payload) % 4)
        raw_payload = base64.urlsafe_b64decode(padded).decode()
        client_id, expires_at_str = raw_payload.split(":", 1)
        if int(expires_at_str) <= time.time():
            return False, ""
        return True, client_id
    except Exception:
        return False, ""


def verify_signed_token(token: str) -> bool:
    """Verify HMAC signature and expiration of a Bearer token (constant-time)."""
    valid, _ = _verify_signed_token_impl(token, "mcp_tok")
    return valid


def verify_signed_refresh_token(token: str) -> tuple[bool, str]:
    """Verify HMAC signature and expiration of a refresh token.

    Returns (is_valid, client_id).
    """
    return _verify_signed_token_impl(token, "mcp_refresh")


class AuthMiddleware:
    """Pure ASGI Authentication Middleware with HMAC-signed token support."""

    def __init__(
        self, app: ASGIApp, api_key: str | None = None, auth_mode: str = "api_key"
    ) -> None:
        self.app = app
        self.api_key = api_key or os.getenv("MCP_API_KEY")
        self.auth_mode = (auth_mode or os.getenv("AUTH_MODE", "api_key")).lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # 1. Allow public routes and landing page
        if path == "/" or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        # 2. If AUTH_MODE is none, bypass
        if self.auth_mode == "none":
            await self.app(scope, receive, send)
            return

        # 3. Extract credentials from headers and query params
        headers = Headers(scope=scope)
        auth_header = headers.get("authorization", "")
        custom_key_header = headers.get("x-mcp-api-key", "")

        provided_token = ""
        if auth_header.startswith("Bearer "):
            provided_token = auth_header[7:].strip()
        elif custom_key_header:
            provided_token = custom_key_header.strip()

        # NOTE: Query parameter token fallback (access_token=, token=) intentionally
        # removed — tokens in URLs leak via access logs, proxy logs, browser history,
        # and Referer headers (RFC 6750 §2.3 discourages this pattern).

        if not provided_token:
            logger.warning(
                "Auth rejected: missing credentials",
                extra={"path": path, "status_code": 401},
            )
            response = JSONResponse(
                {
                    "error": "unauthorized",
                    "message": "Missing Authorization Bearer or X-MCP-API-Key header.",
                    "recovery_hint": "Provide 'Authorization: Bearer <TOKEN>' or 'X-MCP-API-Key: <KEY>' header.",
                },
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Bearer error="invalid_token", error_description="Missing or invalid token"',
                },
            )
            await response(scope, receive, send)
            return

        # 4. Validate Token: HMAC-signed OAuth tokens (stateless, survives restarts)
        is_valid = False

        if verify_signed_token(provided_token):
            is_valid = True
        # Constant-time comparison for master API key
        elif self.api_key and secrets.compare_digest(provided_token, self.api_key):
            is_valid = True

        if not is_valid:
            logger.warning(
                "Auth rejected: invalid token",
                extra={
                    "path": path,
                    "status_code": 403,
                    "token_prefix": provided_token[:10] + "..." if len(provided_token) > 10 else "[short]",
                },
            )
            response = JSONResponse(
                {
                    "error": "invalid_token",
                    "message": "The provided authentication token or API key is invalid or expired.",
                },
                status_code=403,
            )
            await response(scope, receive, send)
            return

        logger.debug(
            "Auth succeeded",
            extra={
                "path": path,
                "auth_type": "hmac_token" if provided_token.startswith("mcp_tok_") else "api_key",
            },
        )
        await self.app(scope, receive, send)


# ─── OAuth 2.0 Route Handlers (RFC 7591 / 8414 / 9470) ──────────────────────


def get_effective_base_url(request: Request) -> str:
    """Resolve effective external base URL honoring X-Forwarded-Proto, SERVER_BASE_URL, or HTTPS default."""
    if custom_base := os.environ.get("SERVER_BASE_URL"):
        return custom_base.rstrip("/")
    base = str(request.base_url).rstrip("/")
    host = request.headers.get("host", "").lower()
    if not host.startswith("localhost") and not host.startswith("127.0.0.1"):
        if base.startswith("http://"):
            base = "https://" + base[len("http://") :]
    return base


async def handle_oauth_metadata(request: Request) -> Response:
    """OAuth 2.0 Authorization Server Metadata (RFC 8414) with PKCE support."""
    base_url = get_effective_base_url(request)
    metadata = {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/oauth/register",
        "jwks_uri": f"{base_url}/oauth/jwks",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
            "none",
        ],
        "scopes_supported": ["mcp:tools", "mcp:resources", "mcp:prompts", "offline_access"],
    }
    return JSONResponse(metadata)


async def handle_oauth_protected_resource(request: Request) -> Response:
    """OAuth 2.0 Protected Resource Metadata (RFC 9470)."""
    base_url = get_effective_base_url(request)
    return JSONResponse(
        {
            "resource": base_url,
            "authorization_servers": [base_url],
            "scopes_supported": ["mcp:tools", "mcp:resources", "mcp:prompts", "offline_access"],
            "bearer_methods_supported": ["header"],
        }
    )


async def handle_oauth_register(request: Request) -> Response:
    """Dynamic Client Registration Endpoint (RFC 7591)."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = {}

    client_id = f"mcp_client_{secrets.token_urlsafe(16)}"
    client_secret = f"mcp_secret_{secrets.token_urlsafe(24)}"
    client_name = body.get("client_name", "ChatGPT-MCP-Client")
    redirect_uris = body.get("redirect_uris", [])
    requested_scope = body.get("scope") or "mcp:tools mcp:resources mcp:prompts offline_access"

    _REGISTERED_CLIENTS[client_id] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "scope": requested_scope,
        "created_at": time.time(),
    }

    logger.info(f"📋 [OAUTH DCR] Dynamically registered client: {client_id}")

    return JSONResponse(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": requested_scope,
        },
        status_code=201,
    )


def _render_oauth_login_page(
    client_id: str,
    redirect_uri: str,
    state: str,
    scope: str,
    error: str = "",
) -> HTMLResponse:
    """Render a secure dark-mode OAuth authorization consent screen."""
    error_html = f'<div class="error-box">❌ {html.escape(error)}</div>' if error else ""
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Authorize Ask-a-Friend MCP</title>
  <style>
    :root {{
      --bg: #0a0c10;
      --card: #12161f;
      --border: rgba(255, 255, 255, 0.12);
      --accent: #58a6ff;
      --text: #e6edf3;
      --muted: #8b949e;
      --error: #f85149;
    }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      padding: 1.5rem;
    }}
    .auth-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 2rem;
      max-width: 440px;
      width: 100%;
      box-shadow: 0 16px 36px rgba(0, 0, 0, 0.6);
    }}
    .brand {{
      font-size: 1.4rem;
      font-weight: 700;
      margin-bottom: 0.5rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .desc {{
      color: var(--muted);
      font-size: 0.9rem;
      margin-bottom: 1.5rem;
      line-height: 1.5;
    }}
    .badge {{
      display: inline-block;
      background: rgba(88, 166, 255, 0.15);
      color: var(--accent);
      padding: 0.2rem 0.5rem;
      border-radius: 6px;
      font-size: 0.85rem;
      font-family: monospace;
    }}
    .form-group {{
      margin-bottom: 1.25rem;
    }}
    label {{
      display: block;
      font-size: 0.85rem;
      font-weight: 600;
      margin-bottom: 0.5rem;
      color: var(--text);
    }}
    input[type="password"] {{
      width: 100%;
      box-sizing: border-box;
      padding: 0.75rem;
      background: #0d1117;
      border: 1px solid var(--border);
      border-radius: 6px;
      color: #fff;
      font-size: 0.95rem;
      outline: none;
    }}
    input[type="password"]:focus {{
      border-color: var(--accent);
    }}
    .btn {{
      width: 100%;
      padding: 0.8rem;
      background: var(--accent);
      color: #0a0c10;
      font-weight: 600;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-size: 1rem;
      transition: opacity 0.2s;
    }}
    .btn:hover {{
      opacity: 0.9;
    }}
    .error-box {{
      background: rgba(248, 81, 73, 0.15);
      border: 1px solid var(--error);
      color: var(--error);
      padding: 0.65rem;
      border-radius: 6px;
      font-size: 0.85rem;
      margin-bottom: 1rem;
    }}
  </style>
</head>
<body>
  <div class="auth-card">
    <div class="brand">🤝 Ask-a-Friend MCP</div>
    <div class="desc">
      Client <span class="badge">{html.escape(client_id)}</span> is requesting access to your private Vertex AI Peer Review service.
    </div>
    {error_html}
    <form method="POST" action="/oauth/authorize">
      <input type="hidden" name="redirect_uri" value="{html.escape(redirect_uri)}">
      <input type="hidden" name="state" value="{html.escape(state)}">
      <input type="hidden" name="client_id" value="{html.escape(client_id)}">
      <input type="hidden" name="scope" value="{html.escape(scope)}">
      <div class="form-group">
        <label for="admin_password">Enter Admin Password / Master API Key</label>
        <input type="password" id="admin_password" name="admin_password" placeholder="Enter admin password..." required autofocus>
      </div>
      <button type="submit" class="btn">Authorize Client &rarr;</button>
    </form>
  </div>
</body>
</html>
"""
    status = 401 if error else 200
    return HTMLResponse(content=html_content, status_code=status)


async def handle_oauth_authorize(request: Request) -> Response:
    """OAuth Authorization Endpoint — requires Admin Password before issuing one-time auth code."""
    params: dict[str, str] = dict(request.query_params)
    if request.method == "POST":
        try:
            form = await request.form()
            params.update({k: str(v) for k, v in form.items()})
        except Exception:
            pass

    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")
    client_id = params.get("client_id", "chatgpt")
    requested_scope = params.get("scope") or "mcp:tools mcp:resources mcp:prompts offline_access"
    provided_password = params.get("admin_password") or params.get("password") or ""

    if not redirect_uri:
        logger.warning("❌ [OAUTH AUTHORIZE] Missing redirect_uri parameter")
        return JSONResponse({"error": "Missing redirect_uri parameter"}, status_code=400)

    expected_key = os.getenv("MCP_API_KEY", "").strip()

    # Fail closed: /oauth/authorize is a public route, so without a configured
    # admin key there is nothing to authenticate the resource owner with and any
    # caller could mint an authorization code.
    if not expected_key:
        logger.error(
            "❌ [OAUTH AUTHORIZE] Refusing to issue code: MCP_API_KEY is not configured"
        )
        return JSONResponse(
            {
                "error": "server_error",
                "error_description": (
                    "OAuth is not available because the server has no admin key "
                    "configured. Set MCP_API_KEY and restart."
                ),
            },
            status_code=503,
        )

    # Require the Admin Master Key before issuing an authorization code
    if not provided_password:
        # Render consent login screen
        return _render_oauth_login_page(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            scope=requested_scope,
        )
    if not secrets.compare_digest(provided_password.strip(), expected_key):
        logger.warning(f"❌ [OAUTH AUTHORIZE] Invalid admin password submitted for client={client_id}")
        return _render_oauth_login_page(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            scope=requested_scope,
            error="Invalid Admin Password. Access Denied.",
        )

    # Password verified -> issue code
    now = time.time()
    expired = [k for k, v in _OAUTH_CODES.items() if now - v.get("created_at", 0) > 600]
    for k in expired:
        _OAUTH_CODES.pop(k, None)

    code = f"mcp_code_{secrets.token_urlsafe(32)}"
    _OAUTH_CODES[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": requested_scope,
        "created_at": now,
    }

    separator = "&" if "?" in redirect_uri else "?"
    callback_url = f"{redirect_uri}{separator}code={code}&state={state}"
    logger.info(
        f"✅ [OAUTH AUTHORIZE] Admin verified! Issued auth code ({code[:16]}...) for client={client_id}"
    )
    return RedirectResponse(url=callback_url, status_code=302)


async def handle_oauth_token(request: Request) -> Response:
    """OAuth Token Endpoint — validates auth codes and issues HMAC-signed Bearer tokens."""
    try:
        content_type = request.headers.get("content-type", "")
        params: dict[str, Any] = {}

        if "application/json" in content_type:
            try:
                params = await request.json()
            except Exception:
                pass
        else:
            try:
                form = await request.form()
                params = dict(form)
            except Exception:
                pass

        # Query parameter fallback
        for k, v in request.query_params.items():
            if k not in params:
                params[k] = v

        grant_type = params.get("grant_type", "authorization_code")
        code = params.get("code")
        client_id = params.get("client_id", "chatgpt")

        # Handle refresh token grants — validate the refresh token's HMAC signature
        if grant_type == "refresh_token":
            refresh_token = params.get("refresh_token", "")
            is_valid, token_client_id = verify_signed_refresh_token(refresh_token)
            if not is_valid:
                return JSONResponse(
                    {
                        "error": "invalid_grant",
                        "error_description": "Invalid, expired, or missing refresh token.",
                    },
                    status_code=400,
                )
            # Use the client_id embedded in the refresh token, not the request param
            access_token = create_signed_token(client_id=token_client_id)
            new_refresh_token = create_signed_refresh_token(client_id=token_client_id)
            granted_scope = params.get("scope") or "mcp:tools mcp:resources mcp:prompts offline_access"
            logger.info(
                f"🔄 [OAUTH REFRESH] Rotated tokens for client={token_client_id}"
            )
            return JSONResponse(
                {
                    "access_token": access_token,
                    "token_type": "Bearer",
                    "expires_in": 2592000,
                    "refresh_token": new_refresh_token,
                    "scope": granted_scope,
                }
            )

        if grant_type != "authorization_code":
            return JSONResponse(
                {
                    "error": "unsupported_grant_type",
                    "error_description": f"Grant type '{grant_type}' not supported.",
                },
                status_code=400,
            )

        # Validate authorization code (one-time use)
        if not code or code not in _OAUTH_CODES:
            return JSONResponse(
                {
                    "error": "invalid_grant",
                    "error_description": "Invalid, expired, or missing authorization code.",
                },
                status_code=400,
            )

        code_record = _OAUTH_CODES.pop(code)  # One-time use deletion
        if time.time() - code_record.get("created_at", 0) > 600:
            return JSONResponse(
                {
                    "error": "invalid_grant",
                    "error_description": "Authorization code has expired (10-minute limit).",
                },
                status_code=400,
            )

        resolved_client_id = code_record.get("client_id", client_id)
        access_token = create_signed_token(client_id=resolved_client_id)
        refresh_token = create_signed_refresh_token(client_id=resolved_client_id)
        granted_scope = code_record.get("scope") or params.get("scope") or "mcp:tools mcp:resources mcp:prompts offline_access"
        logger.info(
            f"🎫 [OAUTH TOKEN] Issued HMAC-signed Bearer token ({access_token[:18]}...) with scope: {granted_scope}"
        )

        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 2592000,  # 30 days
                "refresh_token": refresh_token,
                "scope": granted_scope,
            }
        )
    except Exception as exc:
        logger.error(f"❌ [OAUTH TOKEN ERROR] {exc}", exc_info=True)
        return JSONResponse(
            {"error": "OAUTH_TOKEN_FAILED", "detail": str(exc)}, status_code=500
        )


async def handle_oauth_jwks(request: Request) -> Response:
    """JWKS endpoint (empty — using HMAC symmetric signing)."""
    return JSONResponse({"keys": []})

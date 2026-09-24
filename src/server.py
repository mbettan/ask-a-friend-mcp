"""FastMCP Server Entrypoint for Ask-a-Friend Consumer MCP.

Dual-transport FastMCP (Streamable HTTP /mcp + SSE /sse) + OpenAPI REST (/api/v1/)
with OAuth 2.0 Dynamic Client Registration, CORS, and AuthMiddleware.

Architecture mirrors the battle-tested TubeLens (youtube-channel-fetcher) MCP server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from starlette.routing import Mount, Route

# Add scripts/ to sys.path for direct module imports
PROJECT_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = str(PROJECT_ROOT / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from ask_friend import ROUTING
from ask_friend import ask_a_friend as _ask_a_friend_backend
from cache import get_cache_stats
from resolve_config import resolve_config

from src.auth import (
    AuthMiddleware,
    handle_oauth_authorize,
    handle_oauth_jwks,
    handle_oauth_metadata,
    handle_oauth_protected_resource,
    handle_oauth_register,
    handle_oauth_token,
    validate_auth_config,
)
from src.config import get_server_config
from src.interceptor import SecurityInterceptor
from src.models import MODEL_CATALOG, AskRequest, FriendModelInfo, ListFriendsResponse

# --------------------------------------------------------------------------
# Structured Logging with Redaction (Cloud Run-aware)
# --------------------------------------------------------------------------

#: Field names that must never reach a log sink.
SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {
        "prompt", "context", "answer", "content", "raw", "body",
        "api_key", "authorization", "mcp_api_key", "text", "input",
        "output", "query", "messages", "payload", "system",
        "system_instruction", "secret", "token", "password",
        "admin_password", "error_body", "raw_prompt", "raw_context",
        "raw_answer", "error_text", "candidates", "parts",
    }
)

_LOG_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message", "asctime", "taskName",
}

MAX_LOG_RECORD_BYTES = 2048


class RedactingFilter(logging.Filter):
    """Strips sensitive keys from ``extra`` payloads before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(record.__dict__):
            if key.lower() in SENSITIVE_FIELDS:
                record.__dict__[key] = "[redacted]"
        return True


class CloudLoggingFormatter(logging.Formatter):
    """One JSON object per line, in Cloud Logging's ``severity`` convention.
    Caps records at MAX_LOG_RECORD_BYTES to prevent log explosion."""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        # Safe truncation before JSON serialization
        if len(msg) > MAX_LOG_RECORD_BYTES:
            msg = msg[:MAX_LOG_RECORD_BYTES - 20] + "... [truncated]"

        payload: dict[str, Any] = {
            "severity": record.levelname,
            "message": msg,
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key in _LOG_RESERVED or key.startswith("_") or key.lower() in SENSITIVE_FIELDS:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = str(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)[-512:]

        raw = json.dumps(payload, default=str, ensure_ascii=False)
        if len(raw) > MAX_LOG_RECORD_BYTES:
            raw = raw[:MAX_LOG_RECORD_BYTES - 20] + '..."}'
        return raw


class LocalFormatter(logging.Formatter):
    """Human-readable formatter for local/pytest that appends structured extra fields."""

    _base_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        # Collect extra fields that aren't part of the standard LogRecord
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _LOG_RESERVED and not k.startswith("_") and k.lower() not in SENSITIVE_FIELDS
        }
        if extras:
            pairs = " ".join(f"{k}={v}" for k, v in extras.items())
            return f"{base} | {pairs}"
        return base


def _configure_logging() -> None:
    """Cloud Run detection: JSON for production, human-friendly for local/pytest.
    Defaults to WARNING level in production to prevent storing request/response payloads."""
    root_logger = logging.getLogger()

    # In Cloud Run (production), default to WARNING to eliminate content/payload logging
    default_level = "WARNING" if os.getenv("K_SERVICE") else "INFO"
    log_level_name = os.getenv("LOG_LEVEL", default_level).upper()
    log_level = getattr(logging, log_level_name, logging.WARNING)
    root_logger.setLevel(log_level)

    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())

    if os.getenv("K_SERVICE"):
        # Cloud Run — structured JSON logging
        handler.setFormatter(CloudLoggingFormatter())
    else:
        # Local / pytest — human-readable with extra fields
        fmt = LocalFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        handler.setFormatter(fmt)

    root_logger.addHandler(handler)

    # Silence third-party verbose HTTP/RPC loggers that might log payloads or endpoints
    for noisy in ("httpx", "httpcore", "google_genai", "urllib3", "google.auth", "google", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger("ask_friend.server")

# ─── FastMCP Application ─────────────────────────────────────────────────────

mcp = FastMCP(
    "ask-a-friend-mcp",
    instructions="Cloud-hosted AI peer review — ask Claude Opus 5.5 ('claude-opus-5-5' / 'opus-5-5'), Claude Sonnet 5 ('claude-sonnet-5' / 'sonnet-5'), Google Gemini 3.8 Flash ('gemini-3.8-flash'), or Gemini 3.5 Flash Lite ('gemini-3.5-flash-lite') for scoped second opinions via Vertex AI.",
    host="0.0.0.0",
    port=8080,
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# ─── MCP Tools ───────────────────────────────────────────────────────────────


@mcp.tool()
async def ask_a_friend(
    task_type: Annotated[
        str,
        Field(
            description="Type of task: 'second_opinion' (default for any question, finance, taxes, math, architecture), 'code_review', 'build_tests', 'security_audit', 'spec_critique', 'finance', 'taxes', or 'general'."
        ),
    ] = "second_opinion",
    prompt: Annotated[
        str,
        Field(
            description="Scoped question, calculation, or request across any topic (finance, taxes, software, math, strategy).",
            max_length=50000,
        ),
    ] = "",
    context: Annotated[
        str,
        Field(
            description="Relevant background text, document, financial data, or code snippet.",
            max_length=200000,
        ),
    ] = "",
    friend_model: Annotated[
        str,
        Field(
            description="Model to consult: 'claude-opus-5-5' (or 'opus-5-5'), 'claude-sonnet-5' (or 'sonnet-5'), 'gemini-3.8-flash', 'gemini-3.5-flash-lite', or 'auto'. Default 'auto' routes to Claude Opus 5.5."
        ),
    ] = "auto",
    max_tokens: Annotated[
        int,
        Field(
            description="Maximum tokens in the friend's response (including Adaptive Thinking & Web Search budget). Default 64000; up to 128000.",
            ge=1,
            le=128000,
        ),
    ] = 64000,
) -> str:
    """Ask another AI model ('claude-opus-5-5', 'claude-sonnet-5', 'gemini-3.8-flash', or 'gemini-3.5-flash-lite') for a second opinion on ANY topic.

    Supported friend_model values:
    - 'claude-opus-5-5' / 'opus-5-5' (Anthropic Claude Opus 5.5 with Adaptive Thinking & Web Search)
    - 'claude-sonnet-5' / 'sonnet-5' (Anthropic Claude Sonnet 5 for fast code & architectural reviews)
    - 'gemini-3.8-flash' (Google Gemini 3.8 Flash with High Thinking)
    - 'gemini-3.5-flash-lite' (Google Gemini 3.5 Flash Lite for low-latency queries)
    - 'auto' (default)
    PII is scrubbed pre-transit and rehydrated on return. Responses are cached via SHA-256.
    """
    resolved_max_tokens = max(int(max_tokens), 32768) if max_tokens is not None else 64000
    req_id = uuid.uuid4().hex[:12]
    t0 = time.monotonic()

    logger.info(
        "MCP tool call: ask_a_friend",
        extra={
            "request_id": req_id,
            "task_type": task_type,
            "friend_model": friend_model,
            "max_tokens": resolved_max_tokens,
            "prompt_len": len(prompt),
            "context_len": len(context),
            "transport": "mcp",
        },
    )

    # Inbound Security Audit
    is_safe, violation, tainted_secrets = SecurityInterceptor.inspect_inbound(prompt, context)
    if not is_safe:
        logger.warning(
            "Inbound security violation blocked",
            extra={"request_id": req_id, "violation": violation, "transport": "mcp"},
        )
        return f"🛡️ Security Exception: {violation}"

    # Run synchronous backend in worker thread
    result = await asyncio.to_thread(
        _ask_a_friend_backend,
        {
            "task_type": task_type,
            "prompt": prompt,
            "context": context,
            "friend_model": friend_model,
            "require_approval": False,
            "use_cache": True,
            "max_tokens": resolved_max_tokens,
        },
    )

    elapsed_ms = round((time.monotonic() - t0) * 1000)

    if result.get("status") != "ok":
        reason = result.get("reason", "Unknown execution failure.")
        logger.error(
            "Friend call failed",
            extra={"request_id": req_id, "reason": reason, "elapsed_ms": elapsed_ms},
        )
        return f"❌ Friend call failed: {reason}"

    answer = result.get("answer", "")
    # Outbound Secret Taint Sanitization
    sanitized_answer = SecurityInterceptor.sanitize_outbound(answer, tainted_secrets)

    cached_tag = " (cached)" if result.get("cached") else ""
    friend = result.get("friend", "unknown")
    answer_body = sanitized_answer.strip()

    logger.info(
        "MCP tool completed: ask_a_friend",
        extra={
            "request_id": req_id,
            "friend": friend,
            "cached": result.get("cached", False),
            "answer_len": len(answer_body),
            "elapsed_ms": elapsed_ms,
            "transport": "mcp",
        },
    )

    return f"🗣️ **{friend}**{cached_tag}:\n\n{answer_body}"


@mcp.tool()
async def list_friends() -> str:
    """List all available friend models and auto-routing rules."""
    lines = [
        "# 🤝 Ask-a-Friend Model Catalog\n",
        "| Alias | Description | Recommended For |",
        "| :--- | :--- | :--- |",
    ]
    for entry in MODEL_CATALOG:
        recommended = ", ".join(entry["recommended_for"])
        lines.append(f"| **`{entry['alias']}`** | {entry['description']} | {recommended} |")

    lines.append("\n### ⚡ Auto-Routing Rules (`friend_model='auto'`):")
    cfg = resolve_config()
    routing_rules = cfg.get("use_cases", ROUTING)
    for task, model in routing_rules.items():
        lines.append(f"- **`{task}`** → `{model}`")

    return "\n".join(lines)


# ─── MCP Resources ───────────────────────────────────────────────────────────


# Explicit allowlist of config fields safe to expose via MCP resource
_CONFIG_ALLOWLIST: frozenset[str] = frozenset({
    "project_id", "location", "anthropic_region", "default_model",
    "use_cases", "require_approval", "use_cache", "default_max_tokens",
})


@mcp.resource("askfriend://config")
async def get_config() -> str:
    """Read-only view of active server configuration with credentials redacted."""
    cfg = resolve_config()
    safe_cfg = {k: v for k, v in cfg.items() if k in _CONFIG_ALLOWLIST}
    return json.dumps(safe_cfg, indent=2)


@mcp.resource("askfriend://telemetry/summary")
async def get_telemetry_summary() -> str:
    """Read-only summary of server telemetry, uptime, and cache hit rates."""
    stats = get_cache_stats()
    return json.dumps(
        {
            "status": "online",
            "runtime": "Google Cloud Run (Stateless)",
            "primary_anthropic_model": "opus-5-5 (global, max tokens)",
            "primary_gemini_model": "gemini-3.8-flash (global, high thinking)",
            "cache_stats": stats,
        },
        indent=2,
    )


# ─── MCP Prompts ─────────────────────────────────────────────────────────────


@mcp.prompt()
async def peer_code_review(
    code_snippet: Annotated[str, Field(description="The code to review.")],
    language: Annotated[str, Field(description="Programming language.")] = "python",
    focus: Annotated[str, Field(description="Review focus areas.")] = "bugs, security, performance",
) -> str:
    """Structured prompt template for requesting peer code review from Claude Opus 5.5 (opus-5-5)."""
    return f"""You are an expert {language} code reviewer. Review this code focusing strictly on: {focus}.

Format findings:
L<line>: 🔴 bug: <description & fix>
L<line>: 🟡 warning: <description>
L<line>: 🔵 nit: <description>

Be terse. No filler. Code-exact references only.

--- CODE ---
{code_snippet}
"""


@mcp.prompt()
async def security_audit_request(
    code_snippet: Annotated[str, Field(description="The code or endpoint to audit.")],
    threat_model: Annotated[
        str, Field(description="Context for threat model.")
    ] = "web application",
) -> str:
    """Structured prompt template for requesting a security audit from Claude Opus 5.5 (opus-5-5)."""
    return f"""You are a principal application security engineer. Audit this code in the context of a {threat_model}.

Check for: SQLi, XSS, SSRF, auth bypass, IDOR, path traversal, secrets exposure, race conditions.

Format findings:
- Severity: CRITICAL / HIGH / MEDIUM / LOW
- CWE ID:
- Vulnerable Line(s):
- Remediation Code:

--- CODE ---
{code_snippet}
"""


# ─── Starlette REST & Documentation Handlers ─────────────────────────────────


async def handle_rest_ask(request: Request) -> Response:
    """REST API endpoint: POST /api/v1/ask (for Custom GPT Actions & REST clients)."""
    req_id = uuid.uuid4().hex[:12]
    t0 = time.monotonic()

    try:
        body = await request.json()
        req_data = AskRequest(**body)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(
            "REST /api/v1/ask: invalid request body",
            extra={"request_id": req_id, "error": str(e), "transport": "rest"},
        )
        return JSONResponse(
            {"status": "error", "error": f"Invalid request body: {e!s}"}, status_code=400
        )

    logger.info(
        "REST /api/v1/ask: request received",
        extra={
            "request_id": req_id,
            "task_type": req_data.task_type,
            "friend_model": req_data.friend_model or "auto",
            "prompt_len": len(req_data.prompt),
            "context_len": len(req_data.context or ""),
            "transport": "rest",
        },
    )

    # Security check
    is_safe, violation, tainted = SecurityInterceptor.inspect_inbound(
        req_data.prompt, req_data.context or ""
    )
    if not is_safe:
        logger.warning(
            "REST /api/v1/ask: security violation blocked",
            extra={"request_id": req_id, "violation": violation, "transport": "rest"},
        )
        return JSONResponse(
            {"status": "error", "error": f"Security violation: {violation}"}, status_code=400
        )

    result = await asyncio.to_thread(
        _ask_a_friend_backend,
        {
            "task_type": req_data.task_type,
            "prompt": req_data.prompt,
            "context": req_data.context or "",
            "friend_model": req_data.friend_model or "auto",
            "require_approval": False,
            "use_cache": True,
            "max_tokens": req_data.max_tokens or 4096,
        },
    )

    elapsed_ms = round((time.monotonic() - t0) * 1000)

    if result.get("status") != "ok":
        logger.error(
            "REST /api/v1/ask: inference failed",
            extra={
                "request_id": req_id,
                "friend": result.get("friend", "unknown"),
                "reason": result.get("reason", "Unknown"),
                "elapsed_ms": elapsed_ms,
            },
        )
        return JSONResponse(
            {
                "status": "error",
                "friend": result.get("friend", "unknown"),
                "answer": "",
                "cached": False,
                "error": result.get("reason", "Inference execution error."),
            },
            status_code=500,
        )

    sanitized = SecurityInterceptor.sanitize_outbound(result.get("answer", ""), tainted)

    logger.info(
        "REST /api/v1/ask: completed",
        extra={
            "request_id": req_id,
            "friend": result.get("friend", "unknown"),
            "cached": result.get("cached", False),
            "answer_len": len(sanitized),
            "elapsed_ms": elapsed_ms,
            "transport": "rest",
        },
    )

    return JSONResponse(
        {
            "status": "ok",
            "friend": result.get("friend", "unknown"),
            "answer": sanitized,
            "cached": result.get("cached", False),
            "error": None,
        }
    )


async def handle_rest_friends(request: Request) -> Response:
    """REST API endpoint: GET /api/v1/friends."""
    models = [FriendModelInfo(**entry) for entry in MODEL_CATALOG]
    resp = ListFriendsResponse(models=models, routing_rules=ROUTING)
    return JSONResponse(resp.model_dump())


async def handle_openapi_spec(request: Request) -> Response:
    """Serve OpenAPI 3.1.0 schema for Custom GPT Actions."""
    for candidate in (PROJECT_ROOT / "config" / "openapi.yaml", PROJECT_ROOT / "docs" / "openapi.yaml"):
        if candidate.is_file():
            return FileResponse(str(candidate), media_type="application/x-yaml")
    return Response("OpenAPI specification not found", status_code=404)


async def handle_healthz(request: Request) -> Response:
    """Health check / liveness probe for Cloud Run."""
    return JSONResponse({"status": "healthy", "service": "ask-a-friend-mcp", "version": "1.0.0"})


async def handle_livez(request: Request) -> Response:
    """Readiness probe for Cloud Run."""
    return JSONResponse({"status": "alive"})


async def handle_robots(request: Request) -> Response:
    """Serve restrictive robots.txt on Cloud Run backend (website is hosted on GitHub Pages only)."""
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


async def handle_root_status(request: Request) -> Response:
    """Return minimal JSON service status on Cloud Run root path."""
    return JSONResponse(
        {
            "service": "ask-a-friend-mcp",
            "status": "online",
            "version": "1.0.0",
            "transports": ["/mcp", "/sse", "/api/v1/ask", "/api/v1/friends"],
        }
    )


# ─── ASGI App Factory ────────────────────────────────────────────────────────


def create_app() -> Starlette:
    """Create Starlette ASGI application combining FastMCP, REST, OAuth, and Auth.

    Mirrors the TubeLens (youtube-channel-fetcher) architecture:
    - Streamable HTTP mounted via Mount("/mcp", app=...) and Mount("/", app=...)
    - SSE via SseServerTransport with manual handler
    - Full OAuth discovery route variants for ChatGPT & Claude compatibility
    - CORS middleware for browser-based clients
    """
    cfg = get_server_config()

    # FastMCP Streamable HTTP app (reset session manager for clean app lifecycle)
    mcp._session_manager = None
    mcp_http_app = mcp.streamable_http_app()

    # SSE transport (TubeLens pattern — manual handler for ChatGPT compatibility)
    sse_transport = SseServerTransport(
        "/messages/",
        security_settings=mcp.settings.transport_security,
    )

    async def handle_sse(request: Request) -> Response:
        """Handle SSE stream connections (ChatGPT native MCP connector)."""
        client_ip = request.client.host if request.client else "unknown"
        ua = request.headers.get("user-agent", "unknown")
        session_id = uuid.uuid4().hex[:12]
        logger.info(
            "SSE stream opened",
            extra={"session_id": session_id, "client_ip": client_ip, "user_agent": ua},
        )
        t0 = time.monotonic()
        try:
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await mcp._mcp_server.run(
                    streams[0],
                    streams[1],
                    mcp._mcp_server.create_initialization_options(),
                )
            elapsed_s = round(time.monotonic() - t0, 1)
            logger.info(
                "SSE stream closed normally",
                extra={"session_id": session_id, "client_ip": client_ip, "duration_s": elapsed_s},
            )
        except Exception as exc:
            elapsed_s = round(time.monotonic() - t0, 1)
            logger.error(
                "SSE stream error",
                extra={"session_id": session_id, "client_ip": client_ip, "duration_s": elapsed_s, "error": str(exc)},
                exc_info=True,
            )
        return Response()

    # Build route table — order matters (explicit routes before catch-all mounts)
    routes: list[Any] = [
        # Health & readiness probes
        Route("/healthz", endpoint=handle_healthz, methods=["GET"]),
        Route("/livez", endpoint=handle_livez, methods=["GET"]),
        Route("/", endpoint=handle_root_status, methods=["GET"]),
        # OpenAPI schema & restrictive robots.txt for backend instance
        Route("/openapi.yaml", endpoint=handle_openapi_spec, methods=["GET"]),
        Route("/robots.txt", endpoint=handle_robots, methods=["GET"]),
        # REST v1 API (for Custom GPT Actions)
        Route("/api/v1/ask", endpoint=handle_rest_ask, methods=["POST"]),
        Route("/api/v1/friends", endpoint=handle_rest_friends, methods=["GET"]),
        # ── OAuth 2.0 Discovery (all ChatGPT & Claude path variants) ──
        Route("/.well-known/oauth-authorization-server", endpoint=handle_oauth_metadata, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-authorization-server/sse", endpoint=handle_oauth_metadata, methods=["GET", "OPTIONS"]),
        Route("/sse/.well-known/oauth-authorization-server", endpoint=handle_oauth_metadata, methods=["GET", "OPTIONS"]),
        Route("/.well-known/openid-configuration", endpoint=handle_oauth_metadata, methods=["GET", "OPTIONS"]),
        Route("/.well-known/openid-configuration/sse", endpoint=handle_oauth_metadata, methods=["GET", "OPTIONS"]),
        Route("/sse/.well-known/openid-configuration", endpoint=handle_oauth_metadata, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-protected-resource", endpoint=handle_oauth_protected_resource, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-protected-resource/sse", endpoint=handle_oauth_protected_resource, methods=["GET", "OPTIONS"]),
        Route("/sse/.well-known/oauth-protected-resource", endpoint=handle_oauth_protected_resource, methods=["GET", "OPTIONS"]),
        Route("/mcp/.well-known/oauth-authorization-server", endpoint=handle_oauth_metadata, methods=["GET", "OPTIONS"]),
        Route("/mcp/.well-known/openid-configuration", endpoint=handle_oauth_metadata, methods=["GET", "OPTIONS"]),
        Route("/mcp/.well-known/oauth-protected-resource", endpoint=handle_oauth_protected_resource, methods=["GET", "OPTIONS"]),
        # OAuth 2.0 endpoints (with OPTIONS for CORS preflight)
        Route("/oauth/register", endpoint=handle_oauth_register, methods=["POST", "OPTIONS"]),
        Route("/oauth/authorize", endpoint=handle_oauth_authorize, methods=["GET", "POST"]),
        Route("/oauth/token", endpoint=handle_oauth_token, methods=["POST", "OPTIONS"]),
        Route("/oauth/jwks", endpoint=handle_oauth_jwks, methods=["GET"]),
        # ── SSE Transport (TubeLens pattern) ──
        Mount("/sse/messages", app=sse_transport.handle_post_message),
        Mount("/messages", app=sse_transport.handle_post_message),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
    ]

    # Mount FastMCP Streamable HTTP app (TubeLens pattern: /mcp + root catch-all)
    routes.append(Mount("/mcp", app=mcp_http_app))
    routes.append(Mount("/", app=mcp_http_app))

    # Refuse to build an app whose auth mode cannot actually be enforced.
    validate_auth_config(cfg.auth_mode, cfg.mcp_api_key)

    # Middleware stack: CORS first, then Auth
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=[
                "https://chatgpt.com",
                "https://chat.openai.com",
                "https://claude.ai",
                "https://console.cloud.google.com",
                "http://localhost:3000",
                "http://localhost:8080",
                "http://127.0.0.1:3000",
                "http://127.0.0.1:8080",
            ],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "X-MCP-API-Key", "Content-Type", "Accept"],
        ),
        Middleware(AuthMiddleware, api_key=cfg.mcp_api_key, auth_mode=cfg.auth_mode),
    ]

    lifespan = getattr(mcp_http_app.router, "lifespan_context", None)

    app = Starlette(debug=False, routes=routes, middleware=middleware, lifespan=lifespan)
    return app


# ─── Module-Level App (for uvicorn src.server:app) ──────────────────────────

app = create_app()


# ─── Server Entrypoint ──────────────────────────────────────────────────────


def run_server() -> None:
    """Run production server using uvicorn."""
    import uvicorn

    cfg = get_server_config()

    logger.info(
        "Starting Ask-a-Friend MCP server",
        extra={
            "host": cfg.host,
            "port": cfg.port,
            "auth_mode": cfg.auth_mode,
            "vertex_project_id": cfg.vertex_project_id,
            "anthropic_region": cfg.anthropic_region,
            "default_model": cfg.ask_friend_default_model,
            "cache_enabled": cfg.ask_friend_use_cache,
            "max_tokens": cfg.ask_friend_max_tokens,
        },
    )

    uvicorn.run(
        "src.server:app",
        host=cfg.host,
        port=cfg.port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    run_server()

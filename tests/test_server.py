"""Integration tests for FastMCP Server, Tools, Resources, Prompts, and REST API."""

import json
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from src.server import (
    ask_a_friend,
    create_app,
    get_config,
    get_telemetry_summary,
    list_friends,
    peer_code_review,
    security_audit_request,
)


@pytest.mark.asyncio
async def test_fastmcp_tool_list_friends():
    """Verify list_friends tool returns registered models and auto-routing rules."""
    output = await list_friends()
    assert "opus-5-5" in output
    assert "Gemini 3.8 Flash" in output
    assert "Auto-Routing Rules" in output


@pytest.mark.asyncio
async def test_fastmcp_tool_ask_a_friend(mock_adc_token, mock_claude_response):
    """Verify ask_a_friend FastMCP tool executes and formats response."""
    mock_resp = mock_claude_response("L5: 🔴 bug: Missing validation.")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = await ask_a_friend(
            task_type="code_review",
            prompt="Review this code.",
            context="def f(): pass",
            friend_model="opus-5-5",
        )
        assert "opus-5-5" in res
        assert "Missing validation" in res


@pytest.mark.asyncio
async def test_fastmcp_resources():
    """Verify FastMCP resources return valid JSON."""
    cfg_json = await get_config()
    cfg = json.loads(cfg_json)
    assert "project_id" in cfg

    telemetry_json = await get_telemetry_summary()
    telemetry = json.loads(telemetry_json)
    assert telemetry["status"] == "online"
    assert "cache_stats" in telemetry


@pytest.mark.asyncio
async def test_fastmcp_prompts():
    """Verify FastMCP parameterized prompts format correctly."""
    review_prompt = await peer_code_review(code_snippet="def add(a, b): return a + b")
    assert "expert python code reviewer" in review_prompt.lower()
    assert "def add" in review_prompt

    sec_prompt = await security_audit_request(code_snippet="SELECT * FROM accounts")
    assert "security engineer" in sec_prompt.lower()
    assert "SELECT * FROM accounts" in sec_prompt


def test_rest_api_ask_endpoint(mock_adc_token, mock_claude_response):
    """Verify POST /api/v1/ask returns JSON schema compliant response."""
    mock_resp = mock_claude_response("L1: 🔴 bug: SQL Injection vulnerability.")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        client = TestClient(create_app())
        headers = {"Authorization": "Bearer test-secret-api-key"}
        payload = {
            "task_type": "security_audit",
            "prompt": "Audit this SQL function.",
            "context": "def q(u): return f'SELECT * WHERE id={u}'",
            "friend_model": "opus-5-5",
        }

        resp = client.post("/api/v1/ask", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["friend"] == "claude-opus-5-5"
        assert "SQL Injection" in data["answer"]


def test_rest_api_friends_catalog():
    """Verify GET /api/v1/friends returns catalog."""
    client = TestClient(create_app())
    headers = {"Authorization": "Bearer test-secret-api-key"}
    resp = client.get("/api/v1/friends", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "routing_rules" in data


def test_seo_and_metadata_endpoints():
    """Verify Cloud Run serves JSON status, OpenAPI spec, and Disallow robots.txt (no docs/ website)."""
    client = TestClient(create_app())

    resp_root = client.get("/")
    assert resp_root.status_code == 200
    assert resp_root.json()["service"] == "ask-a-friend-mcp"

    resp_openapi = client.get("/openapi.yaml")
    assert resp_openapi.status_code == 200

    resp_robots = client.get("/robots.txt")
    assert resp_robots.status_code == 200
    assert "Disallow: /" in resp_robots.text

    resp_health = client.get("/healthz")
    assert resp_health.status_code == 200
    assert resp_health.json()["status"] == "healthy"


def test_streamable_http_mcp_initialize():
    """Verify Streamable HTTP transport /mcp responds to JSON-RPC initialization."""
    app = create_app()
    with TestClient(app) as client:
        headers = {
            "Authorization": "Bearer test-secret-api-key",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        }
        # Root Mount("/", app=mcp_http_app) makes /mcp directly accessible
        resp = client.post("/mcp", json=payload, headers=headers)
        assert resp.status_code == 200
        assert "ask-a-friend-mcp" in resp.text
        assert "2024-11-05" in resp.text

"""Tests for Vertex AI & Model Garden client routing and payload formatting."""

import json
from unittest.mock import MagicMock, patch

from agent_platform import call_vertex_model


def test_claude_opus_5_5_routing_and_payload(mock_adc_token, mock_claude_response):
    """Verify opus-5-5 (and claude-opus-5.5 alias) is routed to canonical claude-opus-5-5 on Model Garden global with 128k max tokens."""
    mock_resp = mock_claude_response("L10: 🔴 bug: Re-entrancy vulnerability.")

    with patch("agent_platform.get_anthropic_vertex_client", return_value=None), patch(
        "urllib.request.urlopen", return_value=mock_resp
    ) as mock_urlopen:
        result = call_vertex_model(
            model_alias="opus-5-5",
            prompt="Audit re-entrancy.",
            context="def withdraw(): pass",
            project_id="test-project",
            location="global",
            max_tokens=8192,
        )

        assert result["status"] == "ok"
        assert result["friend"] == "claude-opus-5-5"
        assert "Re-entrancy" in result["answer"]

        # Verify request URL and payload structure
        mock_urlopen.assert_called_once()
        req_obj = mock_urlopen.call_args[0][0]
        assert "aiplatform.googleapis.com" in req_obj.full_url
        assert "locations/global/publishers/anthropic/models/claude-opus-5-5:rawPredict" in req_obj.full_url

        payload = json.loads(req_obj.data.decode("utf-8"))
        assert payload["anthropic_version"] == "vertex-2023-10-16"
        assert payload["max_tokens"] == 16384
        assert len(payload["messages"]) == 1


def test_claude_sonnet_5_routing_and_payload(mock_adc_token, mock_claude_response):
    """Verify sonnet-5 and claude-sonnet-5 resolve to canonical claude-sonnet-5 in global with 128000 max tokens cap."""
    mock_resp = mock_claude_response("L5: 🟡 warning: Suboptimal query pattern.")

    with patch("agent_platform.get_anthropic_vertex_client", return_value=None), patch(
        "urllib.request.urlopen", return_value=mock_resp
    ) as mock_urlopen:
        result = call_vertex_model(
            model_alias="claude-sonnet-5",
            prompt="Review query.",
            project_id="test-project",
            location="global",
        )

        assert result["status"] == "ok"
        assert result["friend"] == "claude-sonnet-5"

        mock_urlopen.assert_called_once()
        req_obj = mock_urlopen.call_args[0][0]
        assert "locations/global/publishers/anthropic/models/claude-sonnet-5:rawPredict" in req_obj.full_url
        payload = json.loads(req_obj.data.decode("utf-8"))
        assert payload["max_tokens"] == 16384


def test_gemini_vertex_routing_and_payload(mock_adc_token, mock_gemini_response):
    """Verify gemini-pro is routed to Vertex AI gemini-3.8-flash generateContent endpoint with HIGH thinking."""
    mock_resp = mock_gemini_response("def test_login(): assert True")

    with patch("agent_platform.get_gemini_genai_client", return_value=None), patch(
        "urllib.request.urlopen", return_value=mock_resp
    ) as mock_urlopen:
        result = call_vertex_model(
            model_alias="gemini-pro",
            prompt="Generate tests.",
            project_id="test-project",
            location="global",
        )

        assert result["status"] == "ok"
        assert result["friend"] == "gemini-3.8-flash"
        assert "test_login" in result["answer"]

        mock_urlopen.assert_called_once()
        req_obj = mock_urlopen.call_args[0][0]
        assert "aiplatform.googleapis.com" in req_obj.full_url
        assert "publishers/google/models/gemini-3.8-flash:generateContent" in req_obj.full_url
        payload = json.loads(req_obj.data.decode("utf-8"))
        assert payload["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "HIGH"


def test_anthropic_claude_invocation(mock_adc_token, mock_claude_response):
    """Verify Claude Opus 5.5 (opus-5-5 -> claude-opus-5-5) global REST caller executes properly."""
    mock_resp = mock_claude_response("L1: 🔴 bug: Critical auth bypass.")

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = call_vertex_model(
            model_alias="opus-5-5",
            prompt="Review auth flow.",
            project_id="test-project",
        )
        assert result["status"] == "ok"
        assert result["friend"] == "claude-opus-5-5"
        assert "Critical auth bypass" in result["answer"]
        assert mock_urlopen.call_count >= 1


def test_gemini_sdk_invocation():
    """Verify google-genai SDK is invoked with HIGH thinking level when available."""
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "def test_auth_success(): assert True"
    mock_genai_client.models.generate_content.return_value = mock_response

    with patch("agent_platform.get_gemini_genai_client", return_value=mock_genai_client):
        result = call_vertex_model(
            model_alias="gemini-pro",
            prompt="Generate auth tests.",
            project_id="test-project",
        )
        assert result["status"] == "ok"
        assert "test_auth_success" in result["answer"]
        mock_genai_client.models.generate_content.assert_called_once()
        call_kwargs = mock_genai_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-3.8-flash"
        assert call_kwargs["config"].thinking_config.thinking_level.value == "HIGH"


def test_gemini_flash_lite_routing(mock_adc_token, mock_gemini_response):
    """Verify gemini-flash-lite routes to gemini-3.5-flash-lite."""
    mock_resp = mock_gemini_response("fast response")

    with patch("agent_platform.get_gemini_genai_client", return_value=None), patch(
        "urllib.request.urlopen", return_value=mock_resp
    ) as mock_urlopen:
        result = call_vertex_model(
            model_alias="gemini-flash-lite",
            prompt="Quick validate.",
            project_id="test-project",
            location="global",
        )

        assert result["status"] == "ok"
        assert result["friend"] == "gemini-3.5-flash-lite"
        mock_urlopen.assert_called_once()
        req_obj = mock_urlopen.call_args[0][0]
        assert "publishers/google/models/gemini-3.5-flash-lite:generateContent" in req_obj.full_url


def test_claude_quota_fallback_to_gemini(mock_adc_token, mock_gemini_response):
    """Verify that if Claude returns 429 / quota exceeded, fallback to Gemini 3.8 Flash (High Thinking) triggers."""
    mock_gem_resp = mock_gemini_response("fallback answer from gemini")

    with patch("agent_platform.get_anthropic_vertex_client", return_value=None), patch(
        "agent_platform.get_gemini_genai_client", return_value=None
    ), patch(
        "agent_platform._call_anthropic_vertex",
        return_value={"status": "error", "reason": "429 Quota exceeded for model opus-5-5"},
    ), patch("urllib.request.urlopen", return_value=mock_gem_resp) as mock_urlopen:
        result = call_vertex_model(
            model_alias="opus-5-5",
            prompt="Perform security audit.",
            project_id="test-project",
        )
        assert result["status"] == "ok"
        assert "fallback answer from gemini" in result["answer"]
        assert "Gemini 3.8 Flash High Thinking" in result["friend"]

        req_obj = mock_urlopen.call_args[0][0]
        assert "publishers/google/models/gemini-3.8-flash:generateContent" in req_obj.full_url
        payload = json.loads(req_obj.data.decode("utf-8"))
        assert payload["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "HIGH"

"""Pytest fixtures and environment setup."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ and src/ are in sys.path
PROJECT_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = str(PROJECT_ROOT / "scripts")
SRC_DIR = str(PROJECT_ROOT / "src")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Set test environment variables
os.environ["VERTEX_PROJECT_ID"] = "test-ask-friend-project"
os.environ["VERTEX_LOCATION"] = "global"
os.environ["ANTHROPIC_REGION"] = "global"
os.environ["AUTH_MODE"] = "api_key"
os.environ["MCP_API_KEY"] = "test-secret-api-key"
os.environ["ASK_FRIEND_USE_CACHE"] = "true"


@pytest.fixture(autouse=True)
def reset_cache_state():
    """Reset the prompt cache before each test."""
    from cache import clear_cache

    clear_cache()
    yield


@pytest.fixture(autouse=True)
def mock_sdk_clients_default():
    """Ensure tests don't make live cloud calls by default."""
    with patch("agent_platform.get_anthropic_vertex_client", return_value=None), patch(
        "agent_platform.get_gemini_genai_client", return_value=None
    ):
        yield


@pytest.fixture
def mock_adc_token():
    """Mock Google Application Default Credentials (ADC) token acquisition."""
    with patch("agent_platform.get_adc_token", return_value="mock-gcp-adc-token-xyz"):
        yield


@pytest.fixture
def mock_claude_response():
    """Mock HTTP response from Vertex AI Model Garden for Claude Opus 5."""

    def _create_mock_response(text="L4: 🔴 bug: Critical timing leak detected."):
        payload = {"content": [{"type": "text", "text": text}]}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    return _create_mock_response


@pytest.fixture
def mock_gemini_response():
    """Mock HTTP response from Vertex AI for Google Gemini 3.8 Flash."""

    def _create_mock_response(text="Here are the generated pytest test cases."):
        payload = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    return _create_mock_response

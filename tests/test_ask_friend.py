"""Tests for Core Orchestrator (ask_a_friend), PII scrubbing, and SHA-256 caching."""

from unittest.mock import patch

from ask_friend import ask_a_friend
from cache import get_cache_stats


def test_ask_a_friend_auto_routing_code_review(mock_adc_token, mock_claude_response):
    """Verify code_review task auto-routes to opus-5-5."""
    mock_resp = mock_claude_response("L1: 🔴 bug: Direct equality check.")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = ask_a_friend(
            {
                "task_type": "code_review",
                "prompt": "Review this function.",
                "context": "def auth(): pass",
                "friend_model": "auto",
            }
        )

        assert result["status"] == "ok"
        assert result["friend"] == "claude-opus-5-5"
        assert result["cached"] is False
        assert "Direct equality check" in result["answer"]


def test_ask_a_friend_custom_use_cases_routing(mock_adc_token, mock_gemini_response):
    """Verify custom use_cases in config overrides default task routing."""
    mock_resp = mock_gemini_response("L1: 🔴 bug: Custom test review.")

    custom_cfg = {
        "project_id": "test-project",
        "location": "global",
        "use_cases": {
            "code_review": "gemini-flash-lite",
        },
    }

    with patch("ask_friend.resolve_config", return_value=custom_cfg), patch(
        "urllib.request.urlopen", return_value=mock_resp
    ):
        result = ask_a_friend(
            {
                "task_type": "code_review",
                "prompt": "Review this function.",
                "context": "def auth(): pass",
                "friend_model": "auto",
            }
        )

        assert result["status"] == "ok"
        assert result["friend"] == "gemini-3.5-flash-lite"


def test_ask_a_friend_force_model_override(mock_adc_token, mock_claude_response):
    """Verify ASK_FRIEND_FORCE_MODEL forces opus-5-5 across all tasks."""
    mock_resp = mock_claude_response("L1: 🔴 bug: Forced Opus review.")

    forced_cfg = {
        "project_id": "test-project",
        "location": "global",
        "force_model": "opus-5-5",
    }

    with patch("ask_friend.resolve_config", return_value=forced_cfg), patch(
        "urllib.request.urlopen", return_value=mock_resp
    ):
        result = ask_a_friend(
            {
                "task_type": "build_tests",
                "prompt": "Build tests.",
                "friend_model": "auto",
            }
        )

        assert result["status"] == "ok"
        assert result["friend"] == "claude-opus-5-5"


def test_ask_a_friend_pii_scrubbing_and_rehydration(mock_adc_token, mock_claude_response):
    """Verify API keys and emails are scrubbed before inference and restored after."""
    secret_key = "sk-ant-api03-12345678901234567890"
    mock_resp = mock_claude_response("Do not hardcode your key __PII_REDACTED_1__.")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = ask_a_friend(
            {
                "task_type": "code_review",
                "prompt": f"Review key {secret_key}",
                "context": "",
                "friend_model": "opus-5-5",
            }
        )

        assert result["status"] == "ok"
        # Secret should be rehydrated in the final output
        assert secret_key in result["answer"]
        assert "__PII_REDACTED_1__" not in result["answer"]


def test_ask_a_friend_sha256_caching(mock_adc_token, mock_claude_response):
    """Verify subsequent identical calls return cached response without calling backend."""
    mock_resp = mock_claude_response("Initial review output.")

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        # First call -> Miss
        res1 = ask_a_friend(
            {
                "task_type": "security_audit",
                "prompt": "Audit this snippet.",
                "friend_model": "opus-5-5",
                "use_cache": True,
            }
        )
        assert res1["status"] == "ok"
        assert res1["cached"] is False
        assert mock_urlopen.call_count == 1

        # Second identical call -> Cache Hit
        res2 = ask_a_friend(
            {
                "task_type": "security_audit",
                "prompt": "Audit this snippet.",
                "friend_model": "opus-5-5",
                "use_cache": True,
            }
        )
        assert res2["status"] == "ok"
        assert res2["cached"] is True
        assert res2["answer"] == res1["answer"]
        # urlopen should not have been called a second time
        assert mock_urlopen.call_count == 1

        stats = get_cache_stats()
        assert stats["cache_hits"] >= 1


def test_ask_a_friend_multi_domain_support(mock_adc_token, mock_claude_response):
    """Verify arbitrary domain tasks (finance, taxes, general) route cleanly with tailored personas."""
    mock_resp = mock_claude_response("Section 179 allows full deduction up to the annual limit.")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = ask_a_friend(
            {
                "task_type": "taxes",
                "prompt": "Explain Section 179 depreciation rules for equipment purchases.",
                "context": "Equipment cost: $150,000",
                "friend_model": "opus-5-5",
            }
        )

        assert result["status"] == "ok"
        assert result["friend"] == "claude-opus-5-5"
        assert "Section 179" in result["answer"]

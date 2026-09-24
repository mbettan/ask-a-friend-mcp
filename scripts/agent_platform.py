"""Vertex AI & Model Garden Client for Ask-a-Friend.

Handles authentication via Google Application Default Credentials (ADC),
AnthropicVertex SDK, and Google GenAI SDK, dispatching inference calls to
Google Gemini (gemini-3.8-flash / gemini-3.5-flash-lite) and Anthropic Claude
(claude-opus-5-5 / claude-sonnet-5) on Vertex AI Model Garden.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

import google.auth
import google.auth.transport.requests

try:
    import certifi

    _SSL_CONTEXT: ssl.SSLContext | None = ssl.create_default_context(
        cafile=certifi.where()
    )
except ImportError:
    _SSL_CONTEXT = None

logger = logging.getLogger("ask_friend.agent_platform")

# Model aliases mapped to canonical Vertex AI / Model Garden identifiers
MODEL_MAP: dict[str, str] = {
    # Anthropic Claude Opus 5.5
    "opus-5-5": "claude-opus-5-5",
    "claude-opus-5-5": "claude-opus-5-5",
    "claude-opus-5.5": "claude-opus-5-5",
    "claude-garden": "claude-opus-5-5",
    # Anthropic Claude Opus 5
    "opus-5": "claude-opus-5",
    "claude-opus-5": "claude-opus-5",
    # Anthropic Claude Sonnet 5
    "sonnet-5": "claude-sonnet-5",
    "claude-sonnet-5": "claude-sonnet-5",
    "claude-3-7-sonnet": "claude-sonnet-5",
    "claude-3-5-sonnet": "claude-sonnet-5",
    # Google Gemini Flash
    "gemini-3.8-flash": "gemini-3.8-flash",
    "gemini-pro": "gemini-3.8-flash",
    "gemini-flash": "gemini-3.8-flash",
    # Google Gemini Flash Lite
    "gemini-3.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-flash-lite": "gemini-3.5-flash-lite",
}

# Max output tokens per canonical model class
MAX_TOKENS_MAP: dict[str, int] = {
    "claude-opus-5-5": 128000,
    "claude-opus-5": 128000,
    "claude-sonnet-5": 128000,
    "gemini-3.8-flash": 65536,
    "gemini-3.5-flash-lite": 65536,
}

_anthropic_clients: dict[str, Any] = {}
_gemini_clients: dict[str, Any] = {}
_cached_credentials: Any = None


def get_max_output_tokens(model_name: str, default: int = 65536) -> int:
    """Resolve maximum output tokens via canonical model name."""
    canonical_name = MODEL_MAP.get(model_name, model_name)
    return MAX_TOKENS_MAP.get(canonical_name, default)


def get_adc_token() -> str:
    """Acquire or refresh OAuth 2.0 access token via cached Google ADC."""
    global _cached_credentials
    if _cached_credentials is None:
        _cached_credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )

    if not _cached_credentials.valid:
        auth_req = google.auth.transport.requests.Request()
        _cached_credentials.refresh(auth_req)

    if not _cached_credentials.token:
        raise ValueError("Failed to retrieve valid ADC token.")
    return str(_cached_credentials.token)


def get_anthropic_vertex_client(project_id: str, region: str) -> Any:
    """Initialize or return cached AnthropicVertex client."""
    key = f"{project_id}:{region}"
    if key not in _anthropic_clients:
        try:
            from anthropic import AnthropicVertex

            _anthropic_clients[key] = AnthropicVertex(
                region=region, project_id=project_id
            )
        except Exception as e:
            logger.debug("Could not initialize AnthropicVertex SDK: %s", e)
            return None
    return _anthropic_clients.get(key)


def get_gemini_genai_client(project_id: str, location: str) -> Any:
    """Initialize or return cached google-genai Client."""
    key = f"{project_id}:{location}"
    if key not in _gemini_clients:
        try:
            from google import genai

            _gemini_clients[key] = genai.Client(
                vertexai=True, project=project_id, location=location
            )
        except Exception as e:
            logger.debug("Could not initialize google-genai SDK: %s", e)
            return None
    return _gemini_clients.get(key)


def call_vertex_model(
    model_alias: str,
    prompt: str,
    context: str = "",
    system_instruction: str = "",
    project_id: str | None = None,
    location: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Call a Vertex AI or Model Garden model endpoint.

    Args:
        model_alias: Model key (e.g. 'gemini-pro', 'opus-5-5')
        prompt: User's scoped question
        context: Trimmed code or text context
        system_instruction: Optional system persona prompt
        project_id: GCP project ID (discovered from environment or ADC)
        location: Regional location for Vertex AI (defaults to 'global')
        max_tokens: Maximum tokens in response

    Returns:
        dict: {"status": "ok"|"error", "answer": str, "friend": str, "reason": str}
    """
    project_id = (
        project_id
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("PROJECT_ID")
        or os.getenv("VERTEX_PROJECT_ID")
        or os.getenv("AGENT_PLATFORM_PROJECT_ID")
    )
    if not project_id:
        raise ValueError(
            "No GCP project ID configured. Set GOOGLE_CLOUD_PROJECT, "
            "VERTEX_PROJECT_ID, or PROJECT_ID environment variable."
        )
    location = (
        location
        or os.getenv("VERTEX_LOCATION")
        or os.getenv("AGENT_PLATFORM_LOCATION")
        or "global"
    )

    resolved_model = MODEL_MAP.get(model_alias, model_alias)
    model_cap = get_max_output_tokens(resolved_model, default=65536)
    effective_max_tokens = min(max_tokens, model_cap) if max_tokens else model_cap

    logger.info(
        "Dispatching model call",
        extra={
            "model_alias": model_alias,
            "resolved_model": resolved_model,
            "max_tokens": effective_max_tokens,
            "project_id": project_id,
            "location": location,
            "prompt_len": len(prompt),
            "context_len": len(context),
        },
    )

    full_prompt = prompt
    if context.strip():
        full_prompt = (
            f"--- CONTEXT ---\n{context.strip()}\n\n--- QUESTION"
            f" ---\n{prompt.strip()}"
        )

    # Route based on model family (Claude vs Gemini)
    if any(
        k in resolved_model.lower() for k in ("claude", "opus", "sonnet", "haiku")
    ):
        t0 = time.monotonic()
        resp = _call_anthropic_vertex(
            model=resolved_model,
            prompt=full_prompt,
            system_instruction=system_instruction,
            project_id=project_id,
            location=location,
            max_tokens=effective_max_tokens,
        )
        call_ms = round((time.monotonic() - t0) * 1000)

        if resp.get("status") == "ok":
            logger.info(
                "Claude inference succeeded",
                extra={
                    "model": resolved_model,
                    "call_ms": call_ms,
                    "answer_len": len(resp.get("answer", "")),
                },
            )
            return resp

        # Fallback to Gemini 3.8 Flash (High Thinking) if Claude fails
        logger.warning(
            "Claude call failed, engaging Gemini 3.8 Flash (High Thinking) fallback",
            extra={
                "model": resolved_model,
                "reason": resp.get("reason", "unknown"),
                "call_ms": call_ms,
                "fallback_model": "gemini-3.8-flash",
                "thinking_level": "HIGH",
            },
        )
        t1 = time.monotonic()
        fallback_resp = _call_gemini_vertex(
            model="gemini-3.8-flash",
            prompt=full_prompt,
            system_instruction=system_instruction,
            project_id=project_id,
            location=location,
            max_tokens=effective_max_tokens,
            thinking_level="HIGH",
        )
        fallback_ms = round((time.monotonic() - t1) * 1000)

        if fallback_resp.get("status") == "ok":
            fallback_resp["friend"] = (
                f"{resolved_model} (fallback: Gemini 3.8 Flash High Thinking)"
            )
            logger.info(
                "Gemini fallback succeeded",
                extra={
                    "call_ms": fallback_ms,
                    "answer_len": len(fallback_resp.get("answer", "")),
                },
            )
            return fallback_resp

        return resp
    else:
        t0 = time.monotonic()
        default_thinking = "HIGH" if "3.8" in resolved_model else None
        result = _call_gemini_vertex(
            model=resolved_model,
            prompt=full_prompt,
            system_instruction=system_instruction,
            project_id=project_id,
            location=location,
            max_tokens=effective_max_tokens,
            thinking_level=default_thinking,
        )
        call_ms = round((time.monotonic() - t0) * 1000)
        if result.get("status") == "ok":
            logger.info(
                "Gemini inference succeeded",
                extra={
                    "model": resolved_model,
                    "call_ms": call_ms,
                    "answer_len": len(result.get("answer", "")),
                },
            )
        else:
            logger.error(
                "Gemini inference failed",
                extra={
                    "model": resolved_model,
                    "call_ms": call_ms,
                    "reason": result.get("reason", "unknown"),
                },
            )
        return result


def _is_web_search_enabled() -> bool:
    """Return True if server-side web search is enabled (default: True)."""
    return os.getenv("ASK_FRIEND_WEB_SEARCH", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _call_anthropic_vertex(
    model: str,
    prompt: str,
    system_instruction: str,
    project_id: str,
    location: str,
    max_tokens: int,
    enable_web_search: bool | None = None,
) -> dict[str, Any]:
    """Execute request against Anthropic Claude via SDK or Vertex rawPredict REST fallback."""
    if enable_web_search is None:
        enable_web_search = _is_web_search_enabled()

    # AnthropicVertex SDK enforces max_tokens <= 21333 for non-streaming calls (10-min timeout formula).
    # 16,384 tokens (~65,000 chars) avoids the SDK ValueError while providing ample budget.
    claude_max_tokens = 16384 if max_tokens >= 1024 else max_tokens

    web_search_tools = [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 1,
        }
    ]

    # Define candidate order for version fallback
    candidates = [model]
    if model == "claude-opus-5-5":
        candidates.append("claude-opus-5")

    # 1. Attempt AnthropicVertex SDK
    anthropic_client = get_anthropic_vertex_client(project_id, location)
    if anthropic_client is not None:
        for candidate in candidates:
            for use_tools in ([True, False] if enable_web_search else [False]):
                try:
                    kwargs: dict[str, Any] = {
                        "model": candidate,
                        "max_tokens": claude_max_tokens,
                        "thinking": {"type": "adaptive"},
                        "output_config": {"effort": "medium" if use_tools else "high"},
                        "extra_body": {"anthropic_beta": ["context-1m-2025-08-07"]},
                        "messages": [{"role": "user", "content": prompt}],
                    }
                    if system_instruction:
                        kwargs["system"] = [
                            {
                                "type": "text",
                                "text": system_instruction,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ]
                    if use_tools:
                        kwargs["tools"] = web_search_tools

                    response = anthropic_client.messages.create(**kwargs)
                    answer_text = "".join(
                        block.text
                        for block in response.content
                        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
                    )
                    stop_reason = getattr(response, "stop_reason", "end_turn")
                    if stop_reason in ("pause_turn", "max_tokens") and answer_text.strip():
                        logger.info(
                            "Continuing Claude turn after stop_reason=%s (len=%d)",
                            stop_reason,
                            len(answer_text),
                        )
                        cont_kwargs = dict(kwargs)
                        cont_kwargs.pop("tools", None)
                        cont_kwargs["output_config"] = {"effort": "medium"}
                        cont_kwargs["messages"] = [
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": answer_text},
                            {
                                "role": "user",
                                "content": "Continue immediately from the exact point where you stopped and complete the remaining analysis and code audit concisely.",
                            },
                        ]
                        cont_resp = anthropic_client.messages.create(**cont_kwargs)
                        cont_text = "".join(
                            block.text
                            for block in cont_resp.content
                            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
                        )
                        if cont_text.strip():
                            answer_text = f"{answer_text.rstrip()}\n\n{cont_text.strip()}"

                    if answer_text.strip():
                        return {
                            "status": "ok",
                            "friend": candidate,
                            "answer": answer_text.strip(),
                        }
                except Exception as exc:
                    if use_tools and "allowedPartnerModelFeatures" in str(exc):
                        logger.info(
                            "Retrying %s without web_search due to shard org-policy cache",
                            candidate,
                        )
                        continue
                    logger.warning(
                        "AnthropicVertex SDK failed for %s, trying next candidate or REST: %s",
                        candidate,
                        exc,
                    )
                    break

    # 2. REST rawPredict Fallback
    try:
        token = get_adc_token()
    except Exception as e:
        return {
            "status": "error",
            "friend": model,
            "reason": (
                f"GCP Authentication failed (ADC): {e}. Verify gcloud auth"
                " application-default login."
            ),
        }

    base_payload: dict[str, Any] = {
        "anthropic_version": "vertex-2023-10-16",
        "anthropic_beta": ["context-1m-2025-08-07"],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": claude_max_tokens,
    }
    if system_instruction:
        base_payload["system"] = [
            {
                "type": "text",
                "text": system_instruction,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    last_error: dict[str, Any] = {
        "status": "error",
        "friend": model,
        "reason": "Model Garden error (404)",
    }

    for candidate in candidates:
        url = (
            f"https://aiplatform.googleapis.com/v1/projects/{project_id}"
            f"/locations/{location}/publishers/anthropic/models/{candidate}:rawPredict"
        )
        for use_tools in ([True, False] if enable_web_search else [False]):
            payload = dict(base_payload)
            if use_tools:
                payload["tools"] = web_search_tools
                payload["output_config"] = {"effort": "medium"}
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(
                    req, timeout=120, context=_SSL_CONTEXT
                ) as response:
                    resp_data = json.loads(response.read().decode("utf-8"))
                    content_blocks = resp_data.get("content", [])
                    answer_text = "".join(
                        b.get("text", "") for b in content_blocks if b.get("type") == "text"
                    )
                    stop_reason = resp_data.get("stop_reason", "end_turn")
                    if stop_reason in ("pause_turn", "max_tokens") and answer_text.strip():
                        cont_payload = dict(base_payload)
                        cont_payload["output_config"] = {"effort": "medium"}
                        cont_payload["messages"] = [
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": answer_text},
                            {
                                "role": "user",
                                "content": "Continue immediately from the exact point where you stopped and complete the remaining analysis and code audit concisely.",
                            },
                        ]
                        cont_req = urllib.request.Request(
                            url,
                            data=json.dumps(cont_payload).encode("utf-8"),
                            headers=headers,
                            method="POST",
                        )
                        with urllib.request.urlopen(
                            cont_req, timeout=90, context=_SSL_CONTEXT
                        ) as cont_resp:
                            cont_data = json.loads(cont_resp.read().decode("utf-8"))
                            cont_blocks = cont_data.get("content", [])
                            cont_text = "".join(
                                b.get("text", "")
                                for b in cont_blocks
                                if b.get("type") == "text"
                            )
                            if cont_text.strip():
                                answer_text = f"{answer_text.rstrip()}\n\n{cont_text.strip()}"

                    if answer_text.strip():
                        return {
                            "status": "ok",
                            "friend": candidate,
                            "answer": answer_text.strip(),
                        }
            except urllib.error.HTTPError as e:
                err_body = ""
                try:
                    err_body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                if use_tools and e.code == 400 and "allowedPartnerModelFeatures" in err_body:
                    continue
                logger.error(
                    "Claude Model Garden HTTP error",
                    extra={"model": candidate, "status_code": e.code},
                )
                last_error = {
                    "status": "error",
                    "friend": candidate,
                    "reason": f"Model Garden error ({e.code})",
                }
                if e.code not in (404, 429, 500, 502, 503, 504):
                    return last_error
                break
            except (
                urllib.error.URLError,
                json.JSONDecodeError,
                OSError,
                ValueError,
                KeyError,
            ) as e:
                logger.error(
                    "Claude Model Garden exception",
                    extra={
                        "model": candidate,
                        "error_type": type(e).__name__,
                        "error": str(e),
                    },
                )
                return {
                    "status": "error",
                    "friend": candidate,
                    "reason": f"Request failed: {e!s}",
                }

    return last_error


def _call_gemini_vertex(
    model: str,
    prompt: str,
    system_instruction: str,
    project_id: str,
    location: str,
    max_tokens: int,
    thinking_level: str | None = None,
    enable_web_search: bool | None = None,
) -> dict[str, Any]:
    """Execute generateContent request against Google Gemini via SDK or REST."""
    if enable_web_search is None:
        enable_web_search = _is_web_search_enabled()

    # 1. Try modern google-genai SDK
    genai_client = get_gemini_genai_client(project_id, location)
    if genai_client is not None:
        try:
            from google.genai import types

            config_kwargs: dict[str, Any] = {
                "max_output_tokens": max_tokens,
                "temperature": 0.2,
            }
            if enable_web_search:
                config_kwargs["tools"] = [
                    types.Tool(google_search=types.GoogleSearch()),
                    types.Tool(url_context=types.UrlContext()),
                ]
            if thinking_level:
                try:
                    level_enum = types.ThinkingLevel(thinking_level.upper())
                    config_kwargs["thinking_config"] = types.ThinkingConfig(
                        thinking_level=level_enum
                    )
                except Exception:
                    config_kwargs["thinking_config"] = types.ThinkingConfig(
                        thinking_budget=-1
                    )

            if system_instruction:
                config_kwargs["system_instruction"] = system_instruction

            response = genai_client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )

            # Handle response text and thoughts separation
            answer_text = ""
            if response and getattr(response, "text", None):
                answer_text = response.text
            elif response and getattr(response, "candidates", None):
                parts = response.candidates[0].content.parts or []
                answer_text = "".join(
                    p.text
                    for p in parts
                    if getattr(p, "text", None) and not getattr(p, "thought", False)
                )
                if not answer_text:
                    answer_text = "".join(
                        p.text for p in parts if getattr(p, "text", None)
                    )

            if answer_text.strip():
                return {
                    "status": "ok",
                    "friend": model,
                    "answer": answer_text.strip(),
                }
        except Exception as exc:
            logger.warning(
                "google-genai SDK invocation failed, trying REST fallback: %s", exc
            )

    # 2. REST generateContent Fallback
    try:
        token = get_adc_token()
    except Exception as e:
        return {
            "status": "error",
            "friend": model,
            "reason": (
                f"GCP Authentication failed (ADC): {e}. Verify gcloud auth"
                " application-default login."
            ),
        }

    url = (
        f"https://aiplatform.googleapis.com/v1/projects/{project_id}"
        f"/locations/{location}/publishers/google/models/{model}:generateContent"
    )

    generation_config: dict[str, Any] = {
        "maxOutputTokens": max_tokens,
        "temperature": 0.2,
    }
    if thinking_level:
        generation_config["thinkingConfig"] = {
            "thinkingLevel": thinking_level.upper()
        }

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    if enable_web_search:
        payload["tools"] = [{"googleSearch": {}}, {"urlContext": {}}]
    if system_instruction:
        payload["systemInstruction"] = {
            "role": "system",
            "parts": [{"text": system_instruction}],
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(
            req, timeout=120, context=_SSL_CONTEXT
        ) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
            candidates = resp_data.get("candidates", [])
            if not candidates:
                feedback = resp_data.get("promptFeedback", {})
                block_reason = feedback.get("blockReason", "Unknown block")
                return {
                    "status": "error",
                    "friend": model,
                    "reason": (
                        f"No candidates returned by Gemini (prompt blocked:"
                        f" {block_reason})."
                    ),
                }

            first_candidate = candidates[0]
            finish_reason = first_candidate.get("finishReason", "")
            parts = first_candidate.get("content", {}).get("parts", [])
            answer_text = "".join(
                p.get("text", "") for p in parts if not p.get("thought")
            )
            if not answer_text:
                answer_text = "".join(p.get("text", "") for p in parts)

            if not answer_text.strip():
                return {
                    "status": "error",
                    "friend": model,
                    "reason": (
                        f"Empty completion returned (finishReason:"
                        f" {finish_reason or 'UNKNOWN'})."
                    ),
                }

            return {
                "status": "ok",
                "friend": model,
                "answer": answer_text.strip(),
            }
    except urllib.error.HTTPError as e:
        logger.error(
            "Gemini Vertex AI HTTP error",
            extra={"model": model, "status_code": e.code},
        )
        return {
            "status": "error",
            "friend": model,
            "reason": f"Vertex AI error ({e.code})",
        }
    except (
        urllib.error.URLError,
        json.JSONDecodeError,
        OSError,
        ValueError,
        KeyError,
    ) as e:
        logger.error("Gemini Vertex AI exception: %s", e)
        return {
            "status": "error",
            "friend": model,
            "reason": f"Request failed: {e!s}",
        }

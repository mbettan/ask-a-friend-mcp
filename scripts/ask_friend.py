"""Core Orchestrator for Ask-a-Friend.

Coordinates intelligent model routing, PII scrubbing, SHA-256 caching, and
system persona prompt generation.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agent_platform import call_vertex_model
from cache import get_cached, make_cache_key, set_cached
from pii import rehydrate_pii, scrub_pii
from resolve_config import resolve_config

logger = logging.getLogger("ask_friend.orchestrator")

# Intelligent routing table based on task type strengths
ROUTING = {
    "code_review": "opus-5-5",  # Deep reasoning with maximum token context
    "security_audit": "opus-5-5",  # Thorough vulnerability analysis
    "spec_critique": "opus-5-5",  # Broad architectural critique
    "build_tests": "opus-5-5",  # Comprehensive test generation
    "second_opinion": "opus-5-5",  # High-conviction architectural second opinion
}

# Persona system prompts for diverse domains (software, finance, taxes, strategy, general)
TASK_SYSTEM_PROMPTS = {
    "code_review": (
        "You are an expert code reviewer. Provide a dense, terse review. "
        "No conversational filler. Format findings with line references (e.g. L42: 🔴 bug: <desc>). "
        "Include precise code corrections where applicable."
    ),
    "security_audit": (
        "You are a senior application security engineer. Audit the code for vulnerabilities "
        "(OWASP Top 10, CWEs, injection, auth bypass, IDOR, SSRF, secrets). "
        "For each finding state: Severity (CRITICAL/HIGH/MED/LOW), CWE ID, Vulnerable line(s), and Remediation."
    ),
    "spec_critique": (
        "You are a principal software architect. Critique the specification or design proposal. "
        "Highlight single points of failure, scalability bottlenecks, race conditions, and edge cases. "
        "Be direct, structured, and technical."
    ),
    "build_tests": (
        "You are a test automation specialist. Generate comprehensive, production-grade test cases "
        "covering happy paths, edge cases, boundary conditions, and failure modes. Return ready-to-run code."
    ),
    "second_opinion": (
        "You are a trusted expert peer and advisor. Provide a clear, definitive second opinion on any question or proposed solution across software engineering, architecture, finance, tax analysis, strategy, logic, or general inquiries. "
        "State the exact answer or conclusion clearly and outline concise supporting reasoning or calculations."
    ),
    "finance": (
        "You are a senior financial analyst and strategic advisor. Provide precise quantitative analysis, financial modeling critiques, valuation assessments, and financial second opinions with clear structuring and actionable takeaways."
    ),
    "taxes": (
        "You are an expert tax strategy and accounting specialist. Analyze tax scenarios, deduction strategies, structuring considerations, and regulatory implications thoroughly, accurately, and objectively."
    ),
    "general": (
        "You are a trusted expert advisor. Provide a clear, structured, and definitive answer or second opinion on the requested topic. State conclusions clearly with concise supporting reasoning."
    ),
}


def ask_a_friend(params: dict[str, Any]) -> dict[str, Any]:
    """Orchestrate asking a friend model for peer review.

    Args:
        params: dict containing:
            - task_type: str (code_review, build_tests, second_opinion, security_audit, spec_critique)
            - prompt: str
            - context: str (optional)
            - friend_model: str (default "auto")
            - use_cache: bool (default True)
            - max_tokens: int (optional)

    Returns:
        dict: {"status": "ok"|"error", "answer": str, "friend": str, "cached": bool, "reason": str}
    """
    task_type = params.get("task_type", "second_opinion").strip()
    raw_prompt = params.get("prompt", "").strip()
    raw_context = params.get("context", "").strip()
    requested_model = params.get("friend_model", "auto").strip().lower()
    use_cache = params.get("use_cache", True)
    max_tokens = params.get("max_tokens")

    if not raw_prompt:
        logger.warning("Empty prompt received, rejecting request")
        return {
            "status": "error",
            "reason": "Prompt cannot be empty.",
            "friend": "none",
        }

    # Resolve friend model
    cfg = resolve_config()
    routing_table = cfg.get("use_cases", ROUTING)
    routing_reason = "explicit"
    if requested_model and requested_model != "auto":
        friend_model = requested_model
    elif force_model := cfg.get("force_model"):
        friend_model = force_model
        routing_reason = "force_model"
    else:
        friend_model = routing_table.get(task_type, "opus-5-5")
        routing_reason = "auto_routing"

    logger.info(
        "Model routing resolved",
        extra={
            "task_type": task_type,
            "requested_model": requested_model,
            "resolved_model": friend_model,
            "routing_reason": routing_reason,
            "prompt_len": len(raw_prompt),
            "context_len": len(raw_context),
        },
    )

    # 1. PII Scrubbing (pre-transit)
    scrubbed_prompt, prompt_map = scrub_pii(raw_prompt)
    scrubbed_context, context_map = scrub_pii(raw_context)
    combined_map = {**prompt_map, **context_map}

    pii_redactions = len(combined_map)
    if pii_redactions > 0:
        logger.info(
            "PII scrubbing applied",
            extra={"redactions": pii_redactions, "friend_model": friend_model},
        )

    # 2. Check SHA-256 Cache
    cache_key = make_cache_key(friend_model, task_type, scrubbed_prompt, scrubbed_context)
    if use_cache:
        cached_entry = get_cached(cache_key)
        if cached_entry:
            logger.info(
                "Cache hit — returning cached response",
                extra={
                    "friend_model": friend_model,
                    "task_type": task_type,
                    "cache_key_prefix": cache_key[:16],
                },
            )
            answer = rehydrate_pii(cached_entry["answer"], combined_map)
            return {
                "status": "ok",
                "answer": answer,
                "friend": cached_entry["friend"],
                "cached": True,
            }

    # 3. Resolve System Instruction
    system_instruction = TASK_SYSTEM_PROMPTS.get(
        task_type,
        (
            "You are a trusted expert advisor and peer. Provide a clear, definitive, and structured answer "
            "or second opinion on the question or topic requested (including software, finance, taxes, strategy, mathematics, and logic). "
            "State conclusions clearly with concise supporting reasoning or calculations."
        ),
    )

    # 4. Dispatch Inference Request to Vertex AI / Model Garden
    t0 = time.monotonic()
    response = call_vertex_model(
        model_alias=friend_model,
        prompt=scrubbed_prompt,
        context=scrubbed_context,
        system_instruction=system_instruction,
        project_id=cfg.get("project_id"),
        location=cfg.get("location"),
        max_tokens=max_tokens or cfg.get("default_max_tokens"),
    )
    inference_ms = round((time.monotonic() - t0) * 1000)

    if response.get("status") != "ok":
        logger.error(
            "Inference call failed",
            extra={
                "friend_model": friend_model,
                "task_type": task_type,
                "reason": response.get("reason", "unknown"),
                "inference_ms": inference_ms,
            },
        )
        return dict(response)

    raw_answer = response.get("answer", "")

    logger.info(
        "Inference call succeeded",
        extra={
            "friend_model": response.get("friend", friend_model),
            "task_type": task_type,
            "answer_len": len(raw_answer),
            "inference_ms": inference_ms,
        },
    )

    # 5. Populate Cache (before rehydration to cache canonical scrubbed data)
    if use_cache and raw_answer:
        set_cached(cache_key, raw_answer, response.get("friend", friend_model))

    # 6. Rehydrate PII (post-transit)
    final_answer = rehydrate_pii(raw_answer, combined_map)

    return {
        "status": "ok",
        "answer": final_answer,
        "friend": response.get("friend", friend_model),
        "cached": False,
    }

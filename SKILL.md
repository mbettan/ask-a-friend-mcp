---
name: ask-a-friend-mcp
description: >
  Request a scoped second opinion, code review, security audit, architecture critique, or test suite
  from Anthropic Claude Opus 5.5 (opus-5-5) or Google Gemini 3.8 Flash (High Thinking) via a cloud-hosted FastMCP server.
  Triggers on "ask a friend", "get second opinion", "phone a friend", "peer review", or auto after 2+ retries on a hard bug.
alwaysApply: false
allowed-tools:
  - ask_a_friend
  - list_friends
---

# Ask-a-Friend MCP — Skill Guide

Stuck on a tricky bug, security audit, test edge case, or architectural decision? Phone a friend model on Google Vertex AI (`global`) with real-time Web Search, Adaptive Thinking (`effort="high"`), and 128K output tokens enabled by default.

## Default Model Capabilities

- **Anthropic Claude Opus 5.5 (`opus-5-5`) & Claude Sonnet 5 (`sonnet-5`):**
  - **Adaptive Thinking & High Effort:** `thinking: {"type": "adaptive"}` + `output_config: {"effort": "high"}`
  - **Real-Time Web Search (`web_search_20250305`):** Up to 5 live web searches per request for up-to-date docs, CVEs, and APIs
  - **1M Token Context & 128K Max Output:** `context-1m-2025-08-07` beta header + `128,000` max output tokens
  - **Ephemeral Prompt Caching:** `cache_control: {"type": "ephemeral"}` on system prompts and large code contexts
- **Google Gemini 3.8 Flash (`gemini-3.8-flash` / resilient fallback):**
  - **High Thinking (`thinking_level="HIGH"`):** Deep reasoning with `65,536` max output tokens
  - **Google Search & URL Context Grounding:** `googleSearch` + `urlContext` enabled by default

## Decision Tree & Task Routing

```
Task Delegation Request
       │
       ▼
What is the primary objective?
├── Deep Code Review ────────► ask_a_friend(task_type="code_review", prompt="...", context=code)
│                              → Routes to Claude Opus 5.5 (`opus-5-5`, 128K max tokens, Adaptive High + Web Search)
├── Security Audit ──────────► ask_a_friend(task_type="security_audit", prompt="...", context=code)
│                              → Routes to Claude Opus 5.5 (`opus-5-5`, OWASP/CWE triage + live CVE web search)
├── Spec / Architecture ─────► ask_a_friend(task_type="spec_critique", prompt="...", context=spec)
│                              → Routes to Claude Opus 5.5 (`opus-5-5`, SPOFs, scalability, race conditions)
├── Comprehensive Tests ─────► ask_a_friend(task_type="build_tests", prompt="...", context=code)
│                              → Routes to Claude Opus 5.5 (`opus-5-5`, pytest/unit test suites, edge cases)
└── General Second Opinion ──► ask_a_friend(task_type="second_opinion", prompt="...", context=code)
                               → Routes to Claude Opus 5.5 (`opus-5-5`, trade-off evaluation, alternative solutions)
```

## Golden Rules

1. **Be Direct & Concise:** Friend models are instructed to respond tersely with pure technical substance and zero conversational filler.
2. **Pre-Trim Context:** Provide only the relevant function, module, error log, or diff in `context` (up to 200,000 characters).
3. **Auto-Routing (`friend_model="auto"`):** Let the orchestrator route to the optimal model based on task type, with automatic failover to `gemini-3.8-flash` (`HIGH` Thinking).
4. **Explicit Model Selection:** You can explicitly choose `friend_model="opus-5-5"`, `friend_model="sonnet-5"`, `friend_model="gemini-3.8-flash"`, or `friend_model="gemini-3.5-flash-lite"`.
5. **Pre-Transit Privacy:** Sensitive tokens, keys, and PII are automatically scrubbed before leaving your host environment and rehydrated upon receiving the friend's answer.
6. **Deterministic Caching:** Identical prompts and contexts are served instantly from the SHA-256 cache to save latency and token quota.

## Available MCP Tools

| Tool | Parameters | Description |
|---|---|---|
| `ask_a_friend` | `task_type`, `prompt`, `context=""`, `friend_model="auto"`, `max_tokens=128000` | Delegate a scoped question or review task to a friend model. |
| `list_friends` | *none* | Returns the catalog of active friend models and auto-routing rules. |

## Available MCP Resources & Prompts

- **Resource `askfriend://config`:** Read-only view of active server configuration.
- **Resource `askfriend://telemetry/summary`:** Summary of uptime, cache hit rate, and active models.
- **Prompt `peer_code_review`:** Parameterized prompt for code review.
- **Prompt `security_audit_request`:** Parameterized prompt for vulnerability auditing.

## Example Workflows

### 1. Code Review on a Hard Bug
```python
ask_a_friend(
    task_type="code_review",
    prompt="Identify why this async connection pool deadlocks under high load.",
    context="""
    async def get_connection():
        async with lock:
            if not pool:
                await refill_pool()
            return pool.pop()
    """,
)
```

### 2. Pre-Merge Security Audit
```python
ask_a_friend(
    task_type="security_audit",
    prompt="Audit this token verification function for signature malleability and timing leaks.",
    context=auth_code_snippet,
)
```

### 3. Comprehensive Unit Test Generation
```python
ask_a_friend(
    task_type="build_tests",
    prompt="Write parametrized pytest test cases covering boundary values, empty inputs, and invalid formats.",
    context=parser_code_snippet,
)
```

"""Pydantic Domain Models for Ask-a-Friend Consumer MCP."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TaskType = Literal[
    "code_review",
    "build_tests",
    "second_opinion",
    "security_audit",
    "spec_critique",
    "finance",
    "taxes",
    "general",
]

FriendModel = Literal[
    "auto",
    "opus-5-5",
    "claude-opus-5-5",
    "claude-opus-5",
    "sonnet-5",
    "claude-sonnet-5",
    "claude-garden",
    "gemini-3.8-flash",
    "gemini-pro",
    "gemini-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite",
    "gpt-garden",
    "custom_endpoint",
]


class AskRequest(BaseModel):
    """Payload schema for REST /api/v1/ask and Custom GPT Actions."""

    task_type: TaskType = Field(
        default="second_opinion",
        description="Type of task to delegate: code_review, build_tests, second_opinion, security_audit, spec_critique, finance, taxes, or general.",
    )
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=50000,
        description="Scoped question or review request. Be direct and specific — no filler.",
    )
    context: str | None = Field(
        default="",
        max_length=200000,
        description="Relevant source code snippet, diff, log trace, or technical text.",
    )
    friend_model: str | None = Field(
        default="auto",
        description="Model to consult. Default 'auto' routes intelligently by task type to opus-5-5.",
    )
    max_tokens: int | None = Field(
        default=8192,
        ge=1,
        le=128000,
        description="Maximum token limit in the friend model's response.",
    )


class AskResponse(BaseModel):
    """Response payload for REST /api/v1/ask."""

    status: str = Field(..., description="Status: 'ok' or 'error'.")
    friend: str = Field(..., description="The resolved model that authored the response.")
    answer: str = Field(..., description="Dense technical peer review response.")
    cached: bool = Field(default=False, description="Whether the response was served from cache.")
    error: str | None = Field(default=None, description="Error message if status is 'error'.")


class FriendModelInfo(BaseModel):
    """Metadata for an available friend model."""

    alias: str = Field(..., description="Friendly model identifier.")
    description: str = Field(..., description="Underlying provider and target details.")
    recommended_for: list[str] = Field(
        default_factory=list, description="List of recommended task types."
    )


class ListFriendsResponse(BaseModel):
    """Response model for /api/v1/friends."""

    models: list[FriendModelInfo] = Field(..., description="Catalog of available friend models.")
    routing_rules: dict[str, str] = Field(..., description="Task-to-model auto-routing rules.")


# Single source of truth for all available friend models.
# Consumed by both MCP list_friends tool and REST /api/v1/friends endpoint.
MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "alias": "claude-opus-5-5",
        "description": "Anthropic Claude Opus 5.5 (alias: opus-5-5) via Vertex AI Model Garden (global) — Deep reasoning, Adaptive Thinking & Web Search",
        "recommended_for": ["code_review", "security_audit", "spec_critique", "second_opinion"],
    },
    {
        "alias": "opus-5-5",
        "description": "Anthropic Claude Opus 5.5 (opus-5-5) via Vertex AI Model Garden (global) — Deep reasoning & max tokens",
        "recommended_for": ["code_review", "security_audit", "spec_critique"],
    },
    {
        "alias": "claude-sonnet-5",
        "description": "Anthropic Claude Sonnet 5 (alias: sonnet-5) via Vertex AI Model Garden (global) — Fast architectural & code reviews",
        "recommended_for": ["code_review", "spec_critique"],
    },
    {
        "alias": "sonnet-5",
        "description": "Anthropic Claude Sonnet 5 (sonnet-5) via Vertex AI Model Garden (global) — Architectural reviews",
        "recommended_for": ["code_review", "spec_critique"],
    },
    {
        "alias": "gemini-3.8-flash",
        "description": "Google Gemini 3.8 Flash (High Thinking) via Vertex AI (global) — High-thinking reasoning & fallback",
        "recommended_for": ["build_tests", "second_opinion"],
    },
    {
        "alias": "gemini-3.5-flash-lite",
        "description": "Google Gemini 3.5 Flash Lite (alias: gemini-flash-lite) via Vertex AI (global) — Ultra low-latency queries",
        "recommended_for": ["second_opinion"],
    },
]

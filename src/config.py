"""Server Configuration using Pydantic Settings."""

from __future__ import annotations

import os
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseSettings):
    """Server and runtime environment settings."""

    # Server binding
    port: int = 8080
    host: str = "0.0.0.0"

    # Authentication
    auth_mode: Literal["api_key", "iam", "oauth2", "none"] = "api_key"
    mcp_api_key: str | None = None

    # Google Cloud & Vertex AI
    vertex_project_id: str = ""
    vertex_location: str = "global"
    anthropic_region: str = "global"

    # Model and Cache defaults
    ask_friend_default_model: str = "auto"
    ask_friend_require_approval: bool = False
    ask_friend_use_cache: bool = True
    ask_friend_max_tokens: int = 4096

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


def get_server_config() -> ServerConfig:
    """Retrieve validated server configuration singleton."""
    raw_auth = os.getenv("AUTH_MODE", "api_key").lower()
    auth_mode: Literal["api_key", "iam", "oauth2", "none"] = (
        raw_auth if raw_auth in ("api_key", "iam", "oauth2", "none") else "api_key"  # type: ignore[assignment]
    )
    vertex_project_id = (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("VERTEX_PROJECT_ID")
        or os.getenv("AGENT_PLATFORM_PROJECT_ID")
        or os.getenv("PROJECT_ID")
        or ""
    )
    return ServerConfig(
        port=int(os.getenv("PORT", "8080")),
        host=os.getenv("HOST", "0.0.0.0"),
        auth_mode=auth_mode,
        mcp_api_key=os.getenv("MCP_API_KEY"),
        vertex_project_id=vertex_project_id,
        vertex_location=os.getenv("VERTEX_LOCATION", "global"),
        anthropic_region=os.getenv("ANTHROPIC_REGION", "global"),
        ask_friend_default_model=os.getenv("ASK_FRIEND_DEFAULT_MODEL", "auto"),
        ask_friend_require_approval=os.getenv("ASK_FRIEND_REQUIRE_APPROVAL", "false").lower()
        in ("1", "true"),
        ask_friend_use_cache=os.getenv("ASK_FRIEND_USE_CACHE", "true").lower() in ("1", "true"),
        ask_friend_max_tokens=int(os.getenv("ASK_FRIEND_MAX_TOKENS", "4096")),
    )


ServerConfig.model_rebuild()

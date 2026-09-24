"""Configuration resolver for Ask-a-Friend Consumer MCP.

Precedence: defaults < config.json < environment variables.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "project_id": "",
    "location": "global",
    "anthropic_region": "global",
    "default_model": "opus-5-5",
    "use_cases": {
        "code_review": "opus-5-5",
        "security_audit": "opus-5-5",
        "spec_critique": "opus-5-5",
        "build_tests": "opus-5-5",
        "second_opinion": "opus-5-5",
    },
    "require_approval": False,
    "use_cache": True,
    "default_max_tokens": 128000,
}


def resolve_config() -> dict[str, Any]:
    """Resolve layered configuration.

    Returns a dict containing active settings with credentials safely separated.
    """
    cfg = dict(DEFAULT_CONFIG)
    cfg["use_cases"] = dict(DEFAULT_CONFIG["use_cases"])

    # Check for optional config/config.json or ~/.ask-friend/config.json
    config_paths = [
        Path(__file__).parent.parent / "config" / "config.json",
    ]
    if not os.getenv("PYTEST_CURRENT_TEST"):
        config_paths.append(Path.home() / ".ask-friend" / "config.json")

    for cfg_path in config_paths:
        if cfg_path.is_file():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                    if isinstance(file_data, dict):
                        if "use_cases" in file_data and isinstance(file_data["use_cases"], dict):
                            cfg["use_cases"].update(file_data["use_cases"])
                            file_data = {k: v for k, v in file_data.items() if k != "use_cases"}
                        cfg.update(file_data)
            except (json.JSONDecodeError, OSError):
                pass

    # Environment variables override all
    if os.getenv("GOOGLE_CLOUD_PROJECT"):
        cfg["project_id"] = os.environ["GOOGLE_CLOUD_PROJECT"]
    elif os.getenv("VERTEX_PROJECT_ID"):
        cfg["project_id"] = os.environ["VERTEX_PROJECT_ID"]
    elif os.getenv("AGENT_PLATFORM_PROJECT_ID"):
        cfg["project_id"] = os.environ["AGENT_PLATFORM_PROJECT_ID"]
    elif os.getenv("PROJECT_ID"):
        cfg["project_id"] = os.environ["PROJECT_ID"]

    # Fallback to active Google Application Default Credentials (ADC) project
    if not cfg.get("project_id") and not os.getenv("PYTEST_CURRENT_TEST"):
        try:
            import google.auth

            _, adc_project = google.auth.default()
            if adc_project:
                cfg["project_id"] = adc_project
        except Exception:
            pass

    if os.getenv("VERTEX_LOCATION"):
        cfg["location"] = os.environ["VERTEX_LOCATION"]
    elif os.getenv("AGENT_PLATFORM_LOCATION"):
        cfg["location"] = os.environ["AGENT_PLATFORM_LOCATION"]

    if os.getenv("ANTHROPIC_REGION"):
        cfg["anthropic_region"] = os.environ["ANTHROPIC_REGION"]

    if os.getenv("ASK_FRIEND_DEFAULT_MODEL"):
        cfg["default_model"] = os.environ["ASK_FRIEND_DEFAULT_MODEL"]

    if os.getenv("ASK_FRIEND_REQUIRE_APPROVAL") is not None:
        val = os.environ["ASK_FRIEND_REQUIRE_APPROVAL"].strip().lower()
        cfg["require_approval"] = val in ("1", "true", "yes")

    if os.getenv("ASK_FRIEND_USE_CACHE") is not None:
        val = os.environ["ASK_FRIEND_USE_CACHE"].strip().lower()
        cfg["use_cache"] = val in ("1", "true", "yes")

    if os.getenv("ASK_FRIEND_MAX_TOKENS"):
        try:
            cfg["default_max_tokens"] = int(os.environ["ASK_FRIEND_MAX_TOKENS"])
        except ValueError:
            pass

    if force_model := os.getenv("ASK_FRIEND_FORCE_MODEL"):
        cfg["force_model"] = force_model.strip()
        cfg["use_cases"] = {k: force_model.strip() for k in cfg.get("use_cases", {})}

    return cfg

"""Security Interceptor for Ask-a-Friend.

Provides inbound prompt injection detection, secret taint tracking, and outbound
data leak prevention.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("ask_friend.interceptor")

# Prompt injection heuristics
PROMPT_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(?:all\s+)?previous\s+instructions"),
    re.compile(r"(?i)disregard\s+(?:all\s+)?prior\s+(?:rules|directives|instructions)"),
    re.compile(
        r"(?i)(?:leak|print|dump|reveal|exfiltrate)\s+(?:the\s+)?(?:system\s+prompt|developer\s+mode|internal\s+instructions)"
    ),
    re.compile(r"(?i)you\s+are\s+now\s+in\s+developer\s+mode"),
    re.compile(r"(?i)dan\s+mode|jailbreak"),
]

# Secret patterns for taint detection
SECRET_TAINT_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"),
]


class SecurityInterceptor:
    """Performs pre-execution security audits and post-execution response sanitization."""

    @staticmethod
    def inspect_inbound(prompt: str, context: str = "") -> tuple[bool, str, set[str]]:
        """Inspect inbound prompt and context for prompt injection and collect tainted secrets.

        Returns:
            tuple: (is_safe, violation_reason, tainted_secrets)
        """
        combined = f"{prompt}\n{context}"

        # 1. Prompt Injection Checks
        for pattern in PROMPT_INJECTION_PATTERNS:
            if pattern.search(combined):
                logger.warning(
                    "Prompt injection detected",
                    extra={
                        "pattern": pattern.pattern,
                        "prompt_len": len(prompt),
                        "context_len": len(context),
                    },
                )
                return (
                    False,
                    "Inbound request flagged by prompt injection guard: Malicious instruction override detected.",
                    set(),
                )

        # 2. Secret Taint Tracking
        tainted_secrets = set()
        for pattern in SECRET_TAINT_PATTERNS:
            matches = pattern.findall(combined)
            for m in matches:
                tainted_secrets.add(m)

        if tainted_secrets:
            logger.info(
                "Secrets detected and taint-tracked for outbound sanitization",
                extra={"tainted_count": len(tainted_secrets)},
            )

        return True, "", tainted_secrets

    @staticmethod
    def sanitize_outbound(response_text: str, tainted_secrets: set[str]) -> str:
        """Ensure no tainted secrets are echoed or leaked in the model's outbound response."""
        if not response_text or not tainted_secrets:
            return response_text

        sanitized = response_text
        redacted_count = 0
        for secret in tainted_secrets:
            if secret in sanitized:
                sanitized = sanitized.replace(secret, "[TAINT_REDACTED_SECRET]")
                redacted_count += 1

        if redacted_count > 0:
            logger.warning(
                "Outbound response contained tainted secrets — redacted",
                extra={"redacted_count": redacted_count},
            )

        return sanitized

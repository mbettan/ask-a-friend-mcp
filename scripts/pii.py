"""Client-side PII scrubbing and rehydration for Ask-a-Friend.

Redacts sensitive tokens, credentials, private keys, emails, and filesystem paths
before sending requests over the wire, and rehydrates them upon receiving the response.
"""

from __future__ import annotations

import re

# Patterns for sensitive tokens and personal data
PII_PATTERNS = [
    # Private Key blocks
    (
        r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----",
        "[REDACTED_PRIVATE_KEY]",
    ),
    # Bearer tokens & JWTs
    (r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer [REDACTED_BEARER_TOKEN]"),
    # Anthropic API Keys (more specific — must precede generic sk- pattern)
    (r"sk-ant-[a-zA-Z0-9_\-]{20,}", "[REDACTED_ANTHROPIC_KEY]"),
    # OpenAI API Keys
    (r"sk-[a-zA-Z0-9]{20,}", "[REDACTED_OPENAI_KEY]"),
    # Google Cloud / AI Studio API Keys
    (r"AIza[0-9A-Za-z\-_]{35}", "[REDACTED_GOOGLE_KEY]"),
    # GitHub Tokens (ghp, gho, ghu, ghs, ghr)
    (r"gh[pousr]_[A-Za-z0-9_]{36,}", "[REDACTED_GITHUB_TOKEN]"),
    # Generic API Keys / Secrets assignments
    (
        r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]([a-zA-Z0-9_\-\.]{12,})['\"]",
        r"\1: '[REDACTED_SECRET]'",
    ),
    # Email addresses
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b", "[REDACTED_EMAIL]"),
    # Home directory paths
    (r"/(?:Users|home)/[a-zA-Z0-9_\-\.]+", "[REDACTED_USER_PATH]"),
    (r"[A-Za-z]:\\Users\\[a-zA-Z0-9_\-\.]+", "[REDACTED_WIN_USER_PATH]"),
]


def scrub_pii(text: str) -> tuple[str, dict[str, str]]:
    """Scrub PII and sensitive credentials from input text.

    Returns:
        tuple: (scrubbed_text, redaction_map)
    """
    if not text:
        return text, {}

    scrubbed = text
    redaction_map: dict[str, str] = {}
    placeholder_idx = 1

    # Redact explicit private keys and high-entropy secrets first
    for pattern, replacement in PII_PATTERNS:
        matches = list(re.finditer(pattern, scrubbed))
        for match in reversed(matches):
            original_val = match.group(0)
            if original_val in redaction_map.values():
                continue

            if "[REDACTED" in replacement:
                placeholder = f"__PII_REDACTED_{placeholder_idx}__"
                placeholder_idx += 1
                redaction_map[placeholder] = original_val
                start, end = match.span()
                scrubbed = scrubbed[:start] + placeholder + scrubbed[end:]
            else:
                scrubbed = re.sub(pattern, replacement, scrubbed)

    return scrubbed, redaction_map


def rehydrate_pii(text: str, redaction_map: dict[str, str]) -> str:
    """Rehydrate scrubbed tokens back into the friend model's response."""
    if not text or not redaction_map:
        return text

    result = text
    for placeholder, original in redaction_map.items():
        result = result.replace(placeholder, original)

    return result

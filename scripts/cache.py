"""In-memory SHA-256 Prompt Response Cache for Ask-a-Friend.

Prevents redundant spend and external API calls for duplicate questions.
Stateless and thread-safe for ASGI / multi-threaded worker execution.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

logger = logging.getLogger("ask_friend.cache")

_LOCK = threading.Lock()
_CACHE: dict[str, dict[str, Any]] = {}
_STATS = {
    "hits": 0,
    "misses": 0,
    "total_requests": 0,
    "start_time": time.time(),
}


def make_cache_key(friend_model: str, task_type: str, prompt: str, context: str) -> str:
    """Generate deterministic SHA-256 hash for a prompt request."""
    payload = json.dumps(
        {
            "friend_model": friend_model.strip().lower(),
            "task_type": task_type.strip().lower(),
            "prompt": prompt.strip(),
            "context": context.strip(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_cached(key: str, max_age_seconds: int = 86400) -> dict[str, Any] | None:
    """Retrieve a cached answer if present and not expired."""
    with _LOCK:
        _STATS["total_requests"] += 1
        entry = _CACHE.get(key)
        if entry:
            age_s = time.time() - entry.get("timestamp", 0)
            if age_s <= max_age_seconds:
                _STATS["hits"] += 1
                return entry
            else:
                del _CACHE[key]
                logger.debug(
                    "Cache entry expired and evicted",
                    extra={"cache_key_prefix": key[:16], "age_s": round(age_s)},
                )
        _STATS["misses"] += 1
        return None


def set_cached(key: str, answer: str, friend: str) -> None:
    """Store an answer in cache."""
    with _LOCK:
        # Enforce simple LRU cap of 10,000 entries
        if len(_CACHE) >= 10000:
            # Evict oldest entry
            oldest_key = min(_CACHE.keys(), key=lambda k: _CACHE[k].get("timestamp", 0))
            del _CACHE[oldest_key]
            logger.info(
                "Cache LRU eviction triggered",
                extra={"evicted_key_prefix": oldest_key[:16], "cache_size": len(_CACHE)},
            )

        _CACHE[key] = {
            "answer": answer,
            "friend": friend,
            "timestamp": time.time(),
        }


def get_cache_stats() -> dict[str, Any]:
    """Return summary statistics of cache performance."""
    with _LOCK:
        total = _STATS["total_requests"]
        hits = _STATS["hits"]
        hit_rate = f"{(hits / total * 100):.1f}%" if total > 0 else "0.0%"
        uptime_seconds = int(time.time() - _STATS["start_time"])
        return {
            "cached_entries": len(_CACHE),
            "total_requests": total,
            "cache_hits": hits,
            "cache_misses": _STATS["misses"],
            "cache_hit_rate": hit_rate,
            "uptime_seconds": uptime_seconds,
        }


def clear_cache() -> None:
    """Clear all cached entries (used in tests)."""
    with _LOCK:
        _CACHE.clear()
        _STATS["hits"] = 0
        _STATS["misses"] = 0
        _STATS["total_requests"] = 0

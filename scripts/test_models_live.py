#!/usr/bin/env python3
"""Live Model Access Verification Script for Ask-a-Friend MCP.

Tests direct access and latency across all supported models and aliases
on Vertex AI Global endpoints using Application Default Credentials (ADC).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_platform import MODEL_MAP, call_vertex_model
from resolve_config import resolve_config


def test_live_models() -> None:
    cfg = resolve_config()
    project_id = cfg.get("project_id") or ""
    location = cfg.get("location") or "global"

    print("=" * 80)
    print("🧪 ASK-A-FRIEND MCP: LIVE VERTEX AI MODEL CONNECTIVITY TEST")
    print(f"📍 Project: {project_id} | Location: {location}")
    print("=" * 80)

    # Distinct models and aliases to test
    test_models = [
        ("opus-5-5", "What is 2+2? Answer in one short sentence."),
        ("claude-opus-5", "What is 2+2? Answer in one short sentence."),
        ("gemini-3.8-flash", "What is 3+3? Answer in one short sentence."),
        ("gemini-3.5-flash-lite", "What is 4+4? Answer in one short sentence."),
        ("sonnet-5", "What is 5+5? Answer in one short sentence."),
        ("gemini-pro", "What is 6+6? Answer in one short sentence."),
        ("gemini-flash-lite", "What is 7+7? Answer in one short sentence."),
    ]

    results = []

    for model_alias, prompt in test_models:
        resolved = MODEL_MAP.get(model_alias, model_alias)
        print(f"\n🔍 Testing alias: [{model_alias}] -> resolved: [{resolved}] ...", end=" ", flush=True)

        start_time = time.perf_counter()
        res = call_vertex_model(
            model_alias=model_alias,
            prompt=prompt,
            project_id=project_id,
            location=location,
            max_tokens=256,
        )
        elapsed = time.perf_counter() - start_time

        status = res.get("status")
        friend = res.get("friend")
        answer = (res.get("answer") or "").strip().replace("\n", " ")
        reason = res.get("reason")

        if status == "ok":
            print(f"✅ PASS ({elapsed:.2f}s)")
            print(f"   🗣️  Responding Friend: {friend}")
            print(f"   💬  Sample Output: {answer[:80]}...")
            results.append((model_alias, resolved, True, elapsed, None))
        else:
            print(f"❌ FAIL ({elapsed:.2f}s)")
            print(f"   ⚠️  Error Reason: {reason}")
            results.append((model_alias, resolved, False, elapsed, reason))

    print("\n" + "=" * 80)
    print("📊 SUMMARY TABLE")
    print("=" * 80)
    print(f"{'Alias':<22} | {'Resolved Target':<22} | {'Status':<8} | {'Latency':<8}")
    print("-" * 80)
    for alias, target, ok, elapsed, reason in results:
        status_str = "✅ OK" if ok else "❌ FAIL"
        print(f"{alias:<22} | {target:<22} | {status_str:<8} | {elapsed:.2f}s")
    print("=" * 80)


if __name__ == "__main__":
    test_live_models()

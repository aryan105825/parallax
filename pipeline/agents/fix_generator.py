"""
Agent 4 — Fix Generator (Qwen2.5-Coder-32B-Instruct)

Fixes applied
─────────────
Bug 3 (broken) — SSE accumulation: returns only new_events; reducer merges.
"""

import json
import time
from datetime import datetime, timezone

from llm_client import LLMClient
from prompts.fix_generator import FIX_GENERATOR_SYSTEM_PROMPT
from state import ParallaxState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fix_generator_node(state: ParallaxState) -> dict:
    new_events: list[dict] = []
    new_events.append({
        "event":     "agent_start",
        "agent":     "fix_generator",
        "timestamp": _now(),
        "data":      {},
    })

    start_time          = time.time()
    consensus_findings  = state.get("consensus_findings", []) or []

    fix_error:      str | None = None
    fixed_findings: list       = [f.copy() for f in consensus_findings]

    try:
        if consensus_findings:
            prompt = (
                f"{FIX_GENERATOR_SYSTEM_PROMPT}\n\n"
                f"FINDINGS TO FIX:\n{json.dumps(consensus_findings, indent=2)}"
            )
            client      = LLMClient()
            response    = client.call_fix_generator(prompt)
            content_str = (
                response.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "{}")
            )
            fixes = json.loads(content_str).get("fixes", [])

            fix_map = {fix["id"]: fix for fix in fixes if "id" in fix}
            for finding in fixed_findings:
                fix = fix_map.get(finding.get("id"))
                if fix:
                    finding["fix_code"]        = fix.get("fix_code")
                    finding["fix_explanation"] = fix.get("fix_explanation")
    except Exception as exc:
        fix_error = str(exc)

    latency_ms   = int((time.time() - start_time) * 1000)
    fixes_count  = sum(1 for f in fixed_findings if "fix_code" in f)

    new_events.append({
        "event":     "agent_complete",
        "agent":     "fix_generator",
        "timestamp": _now(),
        "data": {
            "latency_ms":      latency_ms,
            "fixes_generated": fixes_count,
        },
    })

    return {
        "fixed_findings": fixed_findings,
        "fix_error":      fix_error,
        "sse_events":     new_events,
    }

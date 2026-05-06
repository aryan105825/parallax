"""
Agent 2A — Fast Analyst (Qwen2.5-Coder-7B-Instruct)

Fixes applied
─────────────
Bug 3 (broken) — SSE accumulation: returns only new_events; reducer merges.
Bug 2 (broken) — Findings not in SSE payload: sse.ts reads
    data.data.fast_findings from the agent_complete event.  The original
    agent_complete dict only contained findings_count so
    newState.fastFindings was always set from undefined — the Fast column
    never populated.  Fix: include fast_findings in the event data dict.
"""

import json
import time
from datetime import datetime, timezone

from llm_client import LLMClient
from prompts.fast_analyst import FAST_ANALYST_SYSTEM_PROMPT
from state import ParallaxState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fast_analyst_node(state: ParallaxState) -> dict:
    new_events: list[dict] = []
    new_events.append({
        "event":     "agent_start",
        "agent":     "fast_analyst",
        "timestamp": _now(),
        "data":      {},
    })

    start_time    = time.time()
    file_contents = state.get("file_contents", {})
    code_context  = "\n".join(
        f"--- {fp} ---\n{content}" for fp, content in file_contents.items()
    )
    prompt = f"{FAST_ANALYST_SYSTEM_PROMPT}\n\nCODE TO SCAN:\n{code_context}"

    client     = LLMClient()
    fast_error: str | None = None
    findings:   list       = []

    try:
        response    = client.call_fast_analyst(prompt)
        content_str = (
            response.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "{}")
        )
        findings = json.loads(content_str).get("findings", [])
    except Exception as exc:
        fast_error = str(exc)

    latency_ms = int((time.time() - start_time) * 1000)

    # FIX (bug 2): include the actual findings array — not just the count.
    # sse.ts agent_complete handler:
    #   if (data.data.fast_findings) newState.fastFindings = data.data.fast_findings
    new_events.append({
        "event":     "agent_complete",
        "agent":     "fast_analyst",
        "timestamp": _now(),
        "data": {
            "latency_ms":     latency_ms,
            "findings_count": len(findings),
            "fast_findings":  findings,   # ← was missing; Fast column never populated
        },
    })

    return {
        "fast_findings":    findings,
        "fast_latency_ms":  latency_ms,
        "fast_error":       fast_error,
        "sse_events":       new_events,
    }

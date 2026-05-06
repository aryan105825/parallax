"""
Agent 3 — Consensus (Qwen2.5-Coder-32B-Instruct)

Fixes applied
─────────────
Bug 3 (broken) — SSE accumulation: The original consensus_node mixed two
broken patterns:
  1. events["sse_events"].append(parallel_models_complete_event)
     — mutated the list from _log_event in place, but …
  2. events["sse_events"].extend(_log_event(state, "agent_complete", ...)["sse_events"])
     — the second _log_event still read from the original `state`, so the
     parallel_models_complete event was NOT included in the second return,
     meaning the final sse_events list had out-of-order or missing events.

Fix: local new_events list, appended in order, returned as the only diff.
The Annotated[List, operator.add] reducer in state.py concatenates it with
whatever the two analyst branches already produced.
"""

import json
import time
from datetime import datetime, timezone

from llm_client import LLMClient
from prompts.consensus import CONSENSUS_SYSTEM_PROMPT
from state import ParallaxState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def consensus_node(state: ParallaxState) -> dict:
    new_events: list[dict] = []
    new_events.append({
        "event":     "agent_start",
        "agent":     "consensus",
        "timestamp": _now(),
        "data":      {},
    })

    start_time     = time.time()
    fast_findings  = state.get("fast_findings", []) or []
    deep_findings  = state.get("deep_findings", []) or []

    prompt = (
        f"{CONSENSUS_SYSTEM_PROMPT}\n\n"
        f"FAST FINDINGS:\n{json.dumps(fast_findings, indent=2)}\n\n"
        f"DEEP FINDINGS:\n{json.dumps(deep_findings, indent=2)}"
    )

    client              = LLMClient()
    consensus_error:    str | None = None
    consensus_findings: list       = []
    conflicts:          list       = []
    recommendation:     str        = "PASS"
    summary:            str        = ""

    try:
        response    = client.call_consensus(prompt)
        content_str = (
            response.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "{}")
        )
        result              = json.loads(content_str)
        consensus_findings  = result.get("consensus_findings", [])
        conflicts           = result.get("conflicts", [])
        recommendation      = result.get("merge_recommendation", "PASS")
        summary             = result.get("merge_recommendation_reason", "")
    except Exception as exc:
        consensus_error = str(exc)

    latency_ms = int((time.time() - start_time) * 1000)

    # Emit parallel_models_complete first (spec requirement) then agent_complete.
    fast_lat         = state.get("fast_latency_ms") or 0
    deep_lat         = state.get("deep_latency_ms") or 0
    parallel_savings = abs(fast_lat - deep_lat)   # savings = sequential - wall time

    new_events.append({
        "event":     "parallel_models_complete",
        "agent":     "system",
        "timestamp": _now(),
        "data": {
            "fast_latency_ms":     fast_lat,
            "deep_latency_ms":     deep_lat,
            "parallel_savings_ms": parallel_savings,
        },
    })
    new_events.append({
        "event":     "agent_complete",
        "agent":     "consensus",
        "timestamp": _now(),
        "data": {
            "latency_ms":     latency_ms,
            "findings_count": len(consensus_findings),
            "consensus_findings": consensus_findings,
        },
    })

    return {
        "consensus_findings": consensus_findings,
        "conflicts":          conflicts,
        "consensus_summary":  summary,
        "consensus_error":    consensus_error,
        "sse_events":         new_events,
    }

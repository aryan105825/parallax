"""
Agent 2B — Deep Analyst (Qwen2.5-Coder-32B-Instruct)

Fixes applied
─────────────
Bug 3 (broken) — SSE accumulation: returns only new_events; reducer merges.
Bug 2 (broken) — Findings not in SSE payload: sse.ts reads
    data.data.deep_findings from the agent_complete event.  The original
    dict only contained findings_count so the Deep column never populated.
    Fix: include deep_findings in the event data dict.
"""

import json
import time
from datetime import datetime, timezone

from llm_client import LLMClient
from prompts.deep_analyst import DEEP_ANALYST_SYSTEM_PROMPT
from state import ParallaxState
from tools.vector_store import retrieve_similar


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def deep_analyst_node(state: ParallaxState) -> dict:
    new_events: list[dict] = []
    new_events.append({
        "event":     "agent_start",
        "agent":     "deep_analyst",
        "timestamp": _now(),
        "data":      {},
    })

    start_time    = time.time()
    file_contents = state.get("file_contents", {})

    # Cross-file RAG: augment each file with semantically related neighbours
    code_context = ""
    for fp, content in file_contents.items():
        code_context += f"--- {fp} (PRIMARY) ---\n{content}\n"
        # retrieve_similar returns a list of dicts:
        #   [{"file_path": str, "content": str, "scan_id": str}, ...]
        # We must unpack each dict — iterating the raw dict gives key strings,
        # not the content, and str(dict) passes a repr blob to the model.
        similar_files = retrieve_similar(content, k=3)
        for sim in similar_files:
            sim_path    = sim.get("file_path", "unknown")
            sim_content = sim.get("content", "")
            if sim_path == fp or not sim_content:
                # Skip self-matches and empty results
                continue
            code_context += f"--- {sim_path} (RELATED) ---\n{sim_content}\n"

    prompt = (
        f"{DEEP_ANALYST_SYSTEM_PROMPT}\n\n"
        f"CODE TO SCAN WITH RAG CONTEXT:\n{code_context}"
    )

    client     = LLMClient()
    deep_error: str | None = None
    findings:   list       = []

    try:
        response    = client.call_deep_analyst(prompt)
        content_str = (
            response.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "{}")
        )
        findings = json.loads(content_str).get("findings", [])
    except Exception as exc:
        deep_error = str(exc)

    latency_ms = int((time.time() - start_time) * 1000)

    # FIX (bug 2): include the actual findings array.
    # sse.ts agent_complete handler:
    #   if (data.data.deep_findings) newState.deepFindings = data.data.deep_findings
    new_events.append({
        "event":     "agent_complete",
        "agent":     "deep_analyst",
        "timestamp": _now(),
        "data": {
            "latency_ms":     latency_ms,
            "findings_count": len(findings),
            "deep_findings":  findings,   # ← was missing; Deep column never populated
        },
    })

    return {
        "deep_findings":   findings,
        "deep_latency_ms": latency_ms,
        "deep_error":      deep_error,
        "sse_events":      new_events,
    }

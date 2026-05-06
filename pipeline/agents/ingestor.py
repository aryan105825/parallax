"""
Agent 1 — Ingestor

FIX (bug 3 – broken): SSE event accumulation.

The original code did:
    events = _log_event(state, "agent_start", "ingestor")
    # ... work ...
    events["sse_events"].extend(
        _log_event(state, "agent_complete", "ingestor", {...})["sse_events"]
    )

_log_event always builds from state.get("sse_events", []) — the original
state snapshot — so the second call returns ONLY the new event.  extend()
then adds it onto the first list, which sounds correct *when sse_events
starts empty (ingestor runs first)*, but breaks for every subsequent agent
because _log_event re-reads the stale original state and the extend produces
duplicates of every previously accumulated event.

Fix: each agent now tracks only the NEW events it generates in a local list
and returns that list.  state.py declares sse_events as
Annotated[List, operator.add], so LangGraph's reducer appends the new events
to whatever is already in the accumulated state — no agent needs to read or
carry forward the full history.
"""

import os
from datetime import datetime, timezone

from state import ParallaxState
from tools.github_tools import fetch_repo_files
from tools.embedding_client import get_embedding
from tools.vector_store import index_files


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ingestor_node(state: ParallaxState) -> dict:
    # Track only the events THIS node emits.
    new_events: list[dict] = []
    new_events.append({
        "event":     "agent_start",
        "agent":     "ingestor",
        "timestamp": _now(),
        "data":      {},
    })

    trigger       = state.get("trigger", "")
    target_branch = state.get("target_branch", "main")

    # 1. Fetch files ─────────────────────────────────────────────────────────
    file_contents: dict[str, str] = {}
    ingestor_error: str | None = None

    try:
        if "github.com" in trigger:
            fetched  = fetch_repo_files(trigger, target_branch)
            max_bytes = int(os.getenv("PIPELINE_MAX_FILE_SIZE_MB", "10")) * 1024 * 1024

            for fp, content in fetched.items():
                if len(content.encode("utf-8")) > max_bytes:
                    # File skipped per guardrail spec — scan continues.
                    file_contents[fp] = "[SKIPPED: FILE TOO LARGE]"
                else:
                    file_contents[fp] = content
        else:
            file_contents["raw_input.txt"] = trigger
    except Exception as exc:
        ingestor_error = str(exc)

    # 2. Embed each file and index into pgvector ──────────────────────────────
    for fp, content in file_contents.items():
        if not content.startswith("[SKIPPED"):
            get_embedding(content)   # side-effect: vector stored in Rust → Supabase

    index_files(state.get("scan_id"), file_contents)

    # 3. Emit agent_complete ──────────────────────────────────────────────────
    new_events.append({
        "event":     "agent_complete",
        "agent":     "ingestor",
        "timestamp": _now(),
        "data": {
            "files_indexed":      len(file_contents),
            "total_lines":        sum(len(c.splitlines()) for c in file_contents.values()),
            "vector_db_entries":  len(file_contents),
        },
    })

    return {
        "repo_metadata":  {"url": trigger, "branch": target_branch},
        "file_tree":      list(file_contents.keys()),
        "file_contents":  file_contents,
        "ingestor_error": ingestor_error,
        # Return ONLY new events; the Annotated[List, operator.add] reducer in
        # state.py will append them to the accumulated sse_events list.
        "sse_events":     new_events,
    }

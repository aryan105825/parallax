import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict


class ParallaxState(TypedDict):
    # ── Input — never modified after set ─────────────────────────────────────
    scan_id: str
    trigger: str
    target_branch: str
    # FIX (bug 5 – stub): triggered_at is a required field on ParallaxState.
    # main.py was not populating it in initial_state; now it does.  The type
    # is kept here exactly as the spec requires.
    triggered_at: str

    # ── Agent 1 — Ingestor ────────────────────────────────────────────────────
    repo_metadata: Optional[Dict[str, Any]]
    file_tree: Optional[List[str]]
    file_contents: Optional[Dict[str, str]]   # {filepath: full_content}
    diff_summary: Optional[str]
    ingestor_error: Optional[str]

    # ── Agent 2A — Fast Analyst (7B) ──────────────────────────────────────────
    fast_findings: Optional[List[Dict[str, Any]]]
    fast_latency_ms: Optional[int]
    fast_model_version: Optional[str]
    fast_error: Optional[str]

    # ── Agent 2B — Deep Analyst (32B) ─────────────────────────────────────────
    deep_findings: Optional[List[Dict[str, Any]]]
    deep_latency_ms: Optional[int]
    deep_model_version: Optional[str]
    deep_error: Optional[str]

    # ── Agent 3 — Consensus ───────────────────────────────────────────────────
    consensus_findings: Optional[List[Dict[str, Any]]]
    conflicts: Optional[List[Dict[str, Any]]]
    consensus_summary: Optional[str]
    consensus_error: Optional[str]

    # ── Agent 4 — Fix Generator ───────────────────────────────────────────────
    fixed_findings: Optional[List[Dict[str, Any]]]
    fix_error: Optional[str]

    # ── Agent 5 — PR Agent ────────────────────────────────────────────────────
    pr_url: Optional[str]
    pr_number: Optional[int]
    pr_branch: Optional[str]
    pr_error: Optional[str]

    # ── Pipeline metadata ─────────────────────────────────────────────────────
    # FIX (bug 3 – broken): was plain List[Dict].  With two analysts running in
    # parallel via the Send API both nodes return sse_events concurrently.
    # LangGraph resolves conflicts using the reducer: operator.add concatenates
    # both branches' event lists instead of letting the slower branch silently
    # overwrite the faster one.  Sequential agents also benefit: each node only
    # needs to return the NEW events it generated; the reducer appends them onto
    # the accumulated list automatically.
    sse_events: Annotated[List[Dict[str, Any]], operator.add]

    status: str                 # pending | running | complete | error
    completed_at: Optional[str]

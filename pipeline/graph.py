"""
LangGraph orchestration for the Parallax six-agent pipeline.

FIX (bug 1 – broken): The original graph had two fundamental problems:

1. add_conditional_edges returning ["fast_analyst", "deep_analyst"]
   ─────────────────────────────────────────────────────────────────
   LangGraph conditional edges require the routing function to return a
   *string key* that maps to one node in the routing dict.  Returning a list
   of strings is not valid — LangGraph silently mis-routes or raises at
   runtime.  True fan-out requires the Send API (langgraph.constants.Send).

   Fix: dispatch_analysts() returns [Send("fast_analyst", state),
   Send("deep_analyst", state)].  LangGraph schedules both branches as
   concurrent tasks (thread-pool workers in the synchronous runner used by
   asyncio.to_thread in main.py).

2. wait_node → END breaks the sync path
   ─────────────────────────────────────
   The intent was: whichever analyst finishes first "waits" while the other
   completes, then both flow to consensus.  But wait_node → END means the
   slower branch terminates the graph entirely before consensus runs.

   Fix: Both analyst nodes have a direct edge to consensus.  When two edges
   point to the same target node LangGraph treats it as a join barrier —
   consensus only executes after BOTH fast_analyst AND deep_analyst have
   finished writing their results into shared state.  No polling, no dummy
   node, no race condition.
"""

from langgraph.graph import StateGraph, END
from langgraph.constants import Send

from state import ParallaxState
from agents.ingestor import ingestor_node
from agents.fast_analyst import fast_analyst_node
from agents.deep_analyst import deep_analyst_node
from agents.consensus import consensus_node
from agents.fix_generator import fix_generator_node
from agents.pr_agent import pr_agent_node


def dispatch_analysts(state: ParallaxState) -> list[Send]:
    """
    Fan-out node: spawns fast_analyst and deep_analyst as parallel Send tasks.

    Both receive a snapshot of the current state (post-ingestor) so each has
    access to file_contents, scan_id, sse_events, etc.  Their outputs are
    merged back into shared state via the sse_events reducer (operator.add)
    and by writing to non-overlapping keys (fast_findings vs deep_findings).
    """
    return [
        Send("fast_analyst", state),
        Send("deep_analyst", state),
    ]


def create_parallax_graph():
    builder = StateGraph(ParallaxState)

    # ── Nodes ──────────────────────────────────────────────────────────────────
    builder.add_node("ingestor",       ingestor_node)
    builder.add_node("fast_analyst",   fast_analyst_node)
    builder.add_node("deep_analyst",   deep_analyst_node)
    builder.add_node("consensus",      consensus_node)
    builder.add_node("fix_generator",  fix_generator_node)
    builder.add_node("pr_agent",       pr_agent_node)

    # ── Entry point ────────────────────────────────────────────────────────────
    builder.set_entry_point("ingestor")

    # ── Fan-out: ingestor → both analysts in parallel via Send API ─────────────
    builder.add_conditional_edges("ingestor", dispatch_analysts)

    # ── Join barrier: consensus waits until BOTH analysts are done ─────────────
    # LangGraph will not execute consensus until every node that has an edge
    # pointing to it has completed.  No wait_node or polling required.
    builder.add_edge("fast_analyst",  "consensus")
    builder.add_edge("deep_analyst",  "consensus")

    # ── Linear tail ───────────────────────────────────────────────────────────
    builder.add_edge("consensus",     "fix_generator")
    builder.add_edge("fix_generator", "pr_agent")
    builder.add_edge("pr_agent",      END)

    return builder.compile()

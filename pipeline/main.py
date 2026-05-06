"""
FastAPI entry point for the Parallax pipeline.

FIX (bug 5 – stub): triggered_at was not populated in initial_state despite
being a required field on ParallaxState.  Every agent that reads
state["triggered_at"] would raise a KeyError; TypedDict validation in
LangGraph would also reject the state at graph entry.

Fix: initial_state now includes
    "triggered_at": datetime.utcnow().isoformat() + "Z"
set at the moment the POST /scan request is received.
"""

import asyncio
import json
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import psutil
import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from prometheus_fastapi_instrumentator import Instrumentator

from graph import create_parallax_graph

app = FastAPI(title="Parallax Pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Prometheus instrumentation ────────────────────────────────────────────────
# Exposes GET /metrics in Prometheus text format.  Prometheus scrapes this
# endpoint every 15 s as configured in prometheus.yml (job: pipeline).
# instrument() registers the middleware; expose() mounts the /metrics route.
Instrumentator(
    should_group_status_codes=False,
    excluded_handlers=["/metrics", "/health"],
).instrument(app).expose(app, include_in_schema=False)

workflow = create_parallax_graph()

# In-memory state store (replace with Supabase for production)
scans_db: dict[str, dict] = {}

# Rate limiter
RATE_LIMIT        = int(os.getenv("PIPELINE_RATE_LIMIT_PER_MIN", "20"))
ip_request_counts: dict[str, list[float]] = defaultdict(list)


@app.middleware("http")
async def guardrails_middleware(request: Request, call_next):
    # 1. Memory floor
    if psutil.virtual_memory().available < 1_073_741_824:  # 1 GB
        return PlainTextResponse("Service Unavailable: Insufficient memory", status_code=503)

    # 2. Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    ip_request_counts[client_ip] = [t for t in ip_request_counts[client_ip] if now - t < 60]
    if len(ip_request_counts[client_ip]) >= RATE_LIMIT:
        return PlainTextResponse(
            "Too Many Requests", status_code=429, headers={"Retry-After": "60"}
        )
    ip_request_counts[client_ip].append(now)

    return await call_next(request)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@app.post("/scan")
async def start_scan(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except Exception:
        data = {}

    scan_id = str(uuid.uuid4())

    # FIX (bug 5): triggered_at is a required field in ParallaxState.
    # Without it LangGraph rejects the initial state and every agent that
    # reads state["triggered_at"] raises a KeyError.
    initial_state: dict = {
        "scan_id":       scan_id,
        "trigger":       data.get("trigger", ""),
        "target_branch": data.get("target_branch", "main"),
        "triggered_at":  _utcnow(),          # ← was missing
        "status":        "pending",
        "sse_events":    [],
    }
    scans_db[scan_id] = initial_state

    async def run_pipeline_with_timeout(sid: str, state: dict) -> None:
        timeout_s = int(os.getenv("PIPELINE_TIMEOUT_S", "300"))
        scans_db[sid]["status"] = "running"

        try:
            final_state = await asyncio.wait_for(
                asyncio.to_thread(workflow.invoke, state),
                timeout=timeout_s,
            )
            final_state["status"] = "complete"
            final_state["sse_events"] = list(final_state.get("sse_events", []))
            final_state["sse_events"].append({
                "event":     "pipeline_complete",
                "agent":     "system",
                "timestamp": _utcnow(),
                "data":      {"scan_id": sid, "pr_url": final_state.get("pr_url")},
            })
            scans_db[sid] = final_state

        except asyncio.TimeoutError:
            scans_db[sid]["status"] = "error"
            scans_db[sid].setdefault("sse_events", []).append({
                "event":     "pipeline_complete",
                "agent":     "system",
                "timestamp": _utcnow(),
                "data":      {"scan_id": sid, "error": "Pipeline execution timed out"},
            })
        except Exception as exc:
            scans_db[sid]["status"] = "error"
            scans_db[sid].setdefault("sse_events", []).append({
                "event":     "pipeline_complete",
                "agent":     "system",
                "timestamp": _utcnow(),
                "data":      {"scan_id": sid, "error": str(exc)},
            })

    background_tasks.add_task(run_pipeline_with_timeout, scan_id, initial_state)

    return {
        "scan_id":    scan_id,
        "status":     "pending",
        "stream_url": f"/scan/{scan_id}/stream",
    }


@app.get("/scan/{scan_id}/stream")
async def scan_stream(scan_id: str):
    if scan_id not in scans_db:
        raise HTTPException(status_code=404, detail="Scan not found")

    async def event_generator():
        last_index = 0
        while True:
            state = scans_db.get(scan_id)
            if not state:
                break

            events = state.get("sse_events", [])
            while last_index < len(events):
                ev = events[last_index]
                yield f"event: {ev['event']}\ndata: {json.dumps(ev)}\n\n"
                last_index += 1

            if state.get("status") in ("complete", "error"):
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/scan/{scan_id}")
async def get_scan_state(scan_id: str):
    if scan_id not in scans_db:
        raise HTTPException(status_code=404, detail="Scan not found")
    return scans_db[scan_id]


@app.get("/scan/{scan_id}/report")
async def get_scan_report(scan_id: str):
    if scan_id not in scans_db:
        raise HTTPException(status_code=404, detail="Scan not found")

    state = scans_db[scan_id]
    if state.get("status") not in ("complete", "error"):
        raise HTTPException(status_code=400, detail="Scan not yet complete")

    findings       = state.get("consensus_findings") or []
    recommendation = state.get("consensus_summary") or "PASS"

    report  = "# Parallax Security Audit\n\n"
    report += f"**Scan ID:** {scan_id}\n"
    report += f"**Merge Recommendation:** {recommendation}\n\n"
    report += "| ID | File | Type | Priority | CVSS | Source |\n"
    report += "|----|------|------|----------|------|--------|\n"
    for f in findings:
        report += (
            f"| {f.get('id','')} | {f.get('file','')} | {f.get('type','')} "
            f"| {f.get('priority','')} | {f.get('cvss','')} | {f.get('source','')} |\n"
        )
    report += "\n---\n*Scanned by Parallax · Dual-model parallel AI on AMD Instinct MI300X*\n"

    return PlainTextResponse(
        report,
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename=parallax_report_{scan_id}.md"},
    )


@app.get("/health")
async def health():
    fast_url     = os.getenv("AMD_FAST_MODEL_URL", "http://localhost:8001")
    deep_url     = os.getenv("AMD_DEEP_MODEL_URL", "http://localhost:8002")
    rust_url     = os.getenv("RUST_ENGINE_URL",    "http://localhost:3000")
    supabase_url = os.getenv("SUPABASE_URL",       "")

    deps: dict[str, str] = {}

    # ── vLLM + Rust engine ────────────────────────────────────────────────────
    for name, url in [("rust_engine", rust_url), ("fast_model", fast_url), ("deep_model", deep_url)]:
        try:
            r = requests.get(f"{url}/health", timeout=2)
            deps[name] = "ok" if r.status_code == 200 else "error"
        except Exception:
            deps[name] = "unreachable"

    # ── Supabase ──────────────────────────────────────────────────────────────
    # Ping the Supabase REST root; 200 or 401 (unauthenticated) both mean the
    # service is reachable — only a network error or 5xx counts as down.
    if supabase_url:
        try:
            r = requests.get(
                f"{supabase_url}/rest/v1/",
                timeout=3,
                headers={"apikey": os.getenv("SUPABASE_ANON_KEY", "")},
            )
            deps["supabase"] = "ok" if r.status_code < 500 else "error"
        except Exception:
            deps["supabase"] = "unreachable"
    else:
        deps["supabase"] = "not_configured"

    overall = "ok" if all(v in ("ok", "not_configured") for v in deps.values()) else "degraded"
    return {"status": overall, "dependencies": deps}

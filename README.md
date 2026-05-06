# Parallax

**Two minds, one codebase. Parallel AI code intelligence on AMD MI300X.**

Parallax runs two Qwen models simultaneously on AMD MI300X's 192 GB unified memory to deliver fast and deep code analysis in parallel — giving developers a ~500 ms quick scan and a ~5 s deep audit from a single request, streamed live to the browser. The final agent opens a real GitHub Pull Request with surgical fixes applied. No waiting. No sequential bottlenecks.

> **AMD Developer Hackathon — Track 1: AI Agents & Agentic Workflows**
> Author: Aryan Rajput · Deadline: May 11, 2026

---

## Table of Contents

- [Why AMD MI300X](#why-amd-mi300x)
- [Architecture](#architecture)
- [The Six-Agent Pipeline](#the-six-agent-pipeline)
- [Live Demo](#live-demo)
- [Benchmark Results](#benchmark-results)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [Observability](#observability)
- [Contributing](#contributing)

---

## Why AMD MI300X

Every other team at this hackathon runs a model on AMD GPUs. That is not a differentiator.

Parallax does something that is **structurally impossible without AMD hardware** — and makes that impossibility visible in the UI.

### The VRAM math

| Model | Precision | VRAM |
|---|---|---|
| Qwen2.5-Coder-7B-Instruct | bf16 | ~14 GB |
| Qwen2.5-Coder-32B-Instruct | bf16 | ~64 GB |
| **Total** | | **~78 GB** |

The AMD Instinct MI300X carries **192 GB of HBM3 unified memory**. Both models load simultaneously with 114 GB to spare.

On a single NVIDIA A100 (80 GB), you cannot fit both models at the same time. You would have to run them sequentially: ~500 ms + ~5 s = ~5.5 s wall time. On MI300X, both run in parallel: wall time = `max(500 ms, 5 s)` = **~5 s**. That 500 ms delta is the AMD value proposition made concrete.

### What this enables

- **Parallel inference** — the Rust engine fans one request out to both vLLM instances simultaneously using `tokio::join!`. Neither model waits for the other.
- **192 GB context** — the deep analyst holds the entire codebase embedding space in VRAM for cross-file RAG, something impossible on sub-100 GB alternatives.
- **The demo moment** — watching two independent analyses populate side by side in real time is something no other tool at this hackathon can show. It is only possible because of AMD.

### vLLM configuration on MI300X

```bash
# Fast model — Qwen2.5-Coder-7B — port 8001
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct \
  --port 8001 --dtype bfloat16 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.12 \
  --tensor-parallel-size 1

# Deep model — Qwen2.5-Coder-32B — port 8002
vllm serve Qwen/Qwen2.5-Coder-32B-Instruct \
  --port 8002 --dtype bfloat16 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.55 \
  --tensor-parallel-size 1
```

`0.12 + 0.55 = 0.67` — both instances share the GPU pool and leave 33 % headroom for KV cache growth.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser (Next.js 14)                                           │
│  localhost:3001                                                  │
│  DualAnalysisPanel — FastColumn | DeepColumn | ConsensusPanel   │
└───────────────────────┬─────────────────────────────────────────┘
                        │  POST /scan   GET /scan/{id}/stream (SSE)
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI Pipeline  (Python 3.11)                                │
│  localhost:8000                                                  │
│  LangGraph 6-agent orchestration + SSE event emission           │
└───────────┬─────────────────────────────────────────────────────┘
            │  POST /analyze      POST /embed
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Rust Engine  (Axum + Tokio)                                    │
│  localhost:3000                                                  │
│  Pre-allocated 25 × 4 MB buffer pool · Crossbeam channel        │
│  tokio::join! fan-out · Prometheus /metrics                     │
└──────────┬───────────────────────┬──────────────────────────────┘
           │                       │
           ▼                       ▼
┌──────────────────┐   ┌──────────────────────────────────────────┐
│ AMD vLLM #1      │   │ AMD vLLM #2                              │
│ port 8001        │   │ port 8002                                │
│ Qwen2.5-Coder-7B │   │ Qwen2.5-Coder-32B                       │
│ ~14 GB VRAM      │   │ ~64 GB VRAM                              │
│ ~500 ms          │   │ ~5 s                                     │
└──────────────────┘   └──────────────────────────────────────────┘

                        ┌─────────────────┐
                        │ Supabase        │
                        │ pgvector        │
                        │ parallax_embeds │
                        └─────────────────┘

                        ┌─────────────────┐
                        │ GitHub API      │
                        │ Branch + PR     │
                        └─────────────────┘

                        ┌─────────────────┐
                        │ Prometheus      │
                        │ port 9090       │
                        └─────────────────┘
```

The Rust engine is the only component that touches both AMD vLLM endpoints. It receives a single `POST /analyze` request from the Python pipeline and fans it out to both models simultaneously using `tokio::join!` — guaranteeing both results are collected, not just the faster one (`select!` would silently drop the slower model's output).

---

## The Six-Agent Pipeline

| # | Agent | Model | Purpose | Typical latency |
|---|---|---|---|---|
| 1 | **Ingestor** | — | Fetch repo files via GitHub API, embed each file, index into pgvector | ~2–5 s |
| 2A | **Fast Analyst** | Qwen2.5-Coder-7B | Broad first-pass vulnerability scan — prefers recall over precision | ~500 ms |
| 2B | **Deep Analyst** | Qwen2.5-Coder-32B | Precision audit with cross-file RAG context from pgvector | ~5 s |
| 3 | **Consensus** | Qwen2.5-Coder-32B | Merge both findings lists, resolve conflicts, assign OWASP/CWE/CVSS, compute merge recommendation | ~3 s |
| 4 | **Fix Generator** | Qwen2.5-Coder-32B | Generate surgical, syntactically valid fix code for every confirmed finding | ~4 s |
| 5 | **PR Agent** | — | Create branch, apply fixes via `difflib`, commit, open GitHub Pull Request | ~2 s |

Agents 2A and 2B run in parallel via LangGraph's `Send` API. The pipeline emits a `parallel_models_complete` SSE event when both finish, carrying the latency numbers the frontend uses to render the `BenchmarkBar`.

### Finding severity schema

Every consensus finding carries the following enrichment fields:

| Field | Description |
|---|---|
| `priority` | P0 (CVSS ≥ 9.0 or unauthenticated exploit), P1 (7.0–8.9), P2 (4.0–6.9), P3 (< 4.0) |
| `cvss` | Final CVSS 3.1 base score |
| `owasp` | OWASP Top 10 (2021) reference |
| `cwe` | CWE identifier |
| `source` | `consensus` · `deep_only` · `fast_only` · `conflict_resolved` |
| `fix_hint` | One-sentence direction for the fix |
| `fix_code` | Syntactically valid code replacement (added by Fix Generator) |

---

## Live Demo

> **Hugging Face Space:** *(link after deployment)*
> **Deployed frontend:** *(Vercel URL after deployment)*
> **Demo video:** *(YouTube / HF video after recording)*

### What the demo shows

1. Paste a GitHub repo URL (e.g. `https://github.com/digininja/DVWA`) into the input field.
2. The `DualAnalysisPanel` opens. Both columns begin spinning simultaneously.
3. The left column (Fast Analysis — 7B) populates first at ~487 ms.
4. The right column (Deep Analysis — 32B) populates at ~4 832 ms with cross-file CVSS scores.
5. The `BenchmarkBar` renders:

```
Fast (7B):   [████░░░░░░░░░░░░░░░░░░░░░░░░░░]  487 ms
Deep (32B):  [████████████████████████████████] 4,832 ms
Wall time:   [████████████████████████████████] 4,832 ms
Sequential:  [██████████████████████████████████████] 5,319 ms
AMD MI300X Parallel Savings: 487 ms faster
```

6. The Consensus panel appears with the authoritative finding list and a BLOCK / REVIEW / PASS banner.
7. A real GitHub PR link appears at the bottom.

---

## Benchmark Results

> **Note:** The numbers below are the target reference values from the architecture design. Run the benchmark suite after AMD vLLM instances are live and replace with your actual captured numbers before submission.

### k6 fan-out load test (`benchmarks/k6-fanout-test.js`)

Scenario: ramp to 50 VUs over 30 s → sustain 60 s → ramp down 10 s. Target: `POST /analyze` on the Rust engine with a 300-token code snippet.

```
scenarios: (100.00%) 1 scenario, 50 max VUs

✓ http_req_duration (p95) ......... < 6000 ms
✓ http_req_failed ................. 0.00%

http_req_duration ......: avg=4912ms  min=4731ms  med=4849ms  max=5341ms  p(90)=5201ms  p(95)=5287ms
http_reqs ..............: 412    total (4.12/s)
vus ....................: 50     max
```

**Key result:** P95 wall time for a dual-model parallel request = **5 287 ms** vs expected sequential baseline of **~5 800 ms** — demonstrating the parallel savings under load.

> Capture with: `k6 run --out json=benchmarks/results/k6-report.json benchmarks/k6-fanout-test.js`

### Criterion fan-out benchmark (`rust-engine/benches/gateway_bench.rs`)

Micro-benchmark of the Rust engine fan-out function with 50 samples over a 10 s measurement window.

```
gateway_throughput/concurrent_fanout
                        time:   [4.7812 s 4.8491 s 4.9204 s]
                        thrpt:  [0.2032 elem/s 0.2063 elem/s 0.2090 elem/s]
```

> Run with: `cargo bench --manifest-path rust-engine/Cargo.toml`
> Commit the HTML report from `target/criterion/` to `benchmarks/results/criterion-report/`.

### Full pipeline end-to-end (10-file repository)

| Metric | Value |
|---|---|
| Ingestor (10 files, embed + index) | ~3.1 s |
| Fast Analyst (7B) | ~487 ms |
| Deep Analyst (32B, with RAG) | ~4 832 ms |
| Analysts wall time (parallel) | ~4 832 ms |
| Consensus (32B) | ~2 900 ms |
| Fix Generator (32B) | ~3 600 ms |
| PR Agent (GitHub API) | ~1 200 ms |
| **Total end-to-end** | **~15.6 s** |

---

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) + [Docker Compose](https://docs.docker.com/compose/) v2
- AMD MI300X instance with ROCm 6.x and `vllm` installed (for real inference)
- GitHub personal access token with `repo` and `pull_requests` scopes
- Supabase project with the `pgvector` extension enabled

### 1. Clone and configure

```bash
git clone https://github.com/aryan105825/parallax
cd parallax
cp .env.example .env
```

Open `.env` and fill in every variable (see [Environment Variables](#environment-variables)). Nothing is hardcoded — all credentials live in `.env`.

### 2. Start the AMD vLLM instances (on your MI300X host)

```bash
bash scripts/setup-amd.sh
```

This launches both vLLM processes in the background, waits for them to initialize, and exits non-zero if either health check fails. Both endpoints must be reachable before starting the rest of the stack.

### 3. Seed the vector database

```bash
bash scripts/seed-vector-db.sh
```

Creates the `parallax_embeddings` table in Supabase with the correct pgvector schema.

### 4. Start all services

```bash
docker compose up --build
```

Services started:

| Service | URL |
|---|---|
| Rust engine | http://localhost:3000 |
| Python pipeline | http://localhost:8000 |
| Next.js frontend | http://localhost:3001 |
| Prometheus | http://localhost:9090 |

### 5. Run a scan

```bash
curl -s -X POST http://localhost:8000/scan \
  -H "Content-Type: application/json" \
  -d '{"trigger": "https://github.com/digininja/DVWA", "target_branch": "master"}' \
  | jq .
```

Open the returned `stream_url` in a browser, or navigate to `http://localhost:3001` and paste the repo URL into the input field.

### 6. Run the demo script (for recording)

```bash
bash scripts/demo-scan.sh
```

Streams SSE events to the terminal with timestamps. Run this in a split screen alongside `http://localhost:3001/scan/{id}` for the demo video.

---

## Environment Variables

All variables are required unless marked optional. None are hardcoded.

```bash
# AMD Developer Cloud — two separate vLLM endpoints
AMD_FAST_MODEL_URL=          # e.g. http://your-mi300x-host:8001
AMD_DEEP_MODEL_URL=          # e.g. http://your-mi300x-host:8002
AMD_API_KEY=                 # AMD Developer Cloud API key

# Model identifiers (sent in every vLLM request body)
FAST_MODEL=Qwen/Qwen2.5-Coder-7B-Instruct
DEEP_MODEL=Qwen/Qwen2.5-Coder-32B-Instruct

# Rust engine
RUST_ENGINE_URL=http://rust-engine:3000
RUST_ENGINE_MAX_SLOTS=25               # max concurrent requests (buffer pool size)
RUST_ENGINE_CHANNEL_DEPTH=20           # crossbeam channel depth
RUST_ENGINE_FANOUT_TIMEOUT_MS=8000     # per-model timeout before partial response

# Pipeline
PIPELINE_URL=http://pipeline:8000
PIPELINE_RATE_LIMIT_PER_MIN=20         # requests per IP per minute
PIPELINE_MAX_FILE_SIZE_MB=10           # files larger than this are skipped (not crashed)
PIPELINE_TIMEOUT_S=300                 # hard timeout for the full pipeline run

# GitHub
GITHUB_TOKEN=                          # personal access token, repo + pull_requests scope

# Supabase / pgvector
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_KEY=
VECTOR_TABLE=parallax_embeddings

# Frontend
NEXT_PUBLIC_PIPELINE_URL=http://localhost:8000
NEXT_PUBLIC_APP_NAME=Parallax

# Observability
PROMETHEUS_PORT=9090
```

---

## Project Structure

```
parallax/
├── README.md
├── docker-compose.yml
├── prometheus.yml                       ← Prometheus scrape config
├── .env.example
│
├── rust-engine/                         ← Axum fan-out gateway (Rust)
│   ├── Cargo.toml
│   ├── src/
│   │   ├── main.rs                      ← Axum router, AppState, startup
│   │   ├── buffer.rs                    ← Pre-allocated 25 × 4 MB RAII buffer pool
│   │   ├── fanout.rs                    ← Parallel model dispatch via tokio::join!
│   │   ├── queue.rs                     ← Bounded crossbeam channel
│   │   └── routes/
│   │       ├── analyze.rs               ← POST /analyze — dual-model fan-out
│   │       ├── embed.rs                 ← POST /embed — text embedding
│   │       ├── health.rs                ← GET /health
│   │       └── metrics.rs              ← GET /metrics (Prometheus)
│   └── benches/
│       └── gateway_bench.rs             ← Criterion throughput benchmark
│
├── pipeline/                            ← FastAPI + LangGraph orchestration (Python)
│   ├── requirements.txt
│   ├── main.py                          ← FastAPI app, endpoints, SSE, guardrails
│   ├── state.py                         ← ParallaxState TypedDict (single source of truth)
│   ├── graph.py                         ← LangGraph StateGraph with parallel Send dispatch
│   ├── llm_client.py                    ← vLLM client, retry, timeout, re-auth on 401
│   ├── agents/
│   │   ├── ingestor.py                  ← Agent 1: GitHub/paste ingestion + pgvector indexing
│   │   ├── fast_analyst.py              ← Agent 2A: 7B quick scan
│   │   ├── deep_analyst.py              ← Agent 2B: 32B audit with cross-file RAG
│   │   ├── consensus.py                 ← Agent 3: merge, conflict resolution, enrichment
│   │   ├── fix_generator.py             ← Agent 4: surgical fix code generation
│   │   └── pr_agent.py                  ← Agent 5: branch + difflib patch + GitHub PR
│   ├── prompts/
│   │   ├── fast_analyst.py
│   │   ├── deep_analyst.py
│   │   ├── consensus.py
│   │   ├── fix_generator.py
│   │   └── pr_agent.py
│   ├── tools/
│   │   ├── github_tools.py              ← GitHub API: file fetch, branch, PR creation
│   │   ├── git_tools.py                 ← difflib patch application
│   │   ├── embedding_client.py          ← Calls Rust engine POST /embed
│   │   └── vector_store.py             ← Supabase pgvector insert + cosine query
│   └── tests/
│       └── test_pipeline.py
│
├── frontend/                            ← Next.js 14 dual-column dashboard (TypeScript)
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx                 ← Landing page with code input
│   │   │   └── scan/[id]/page.tsx       ← Live dual-column results
│   │   ├── components/
│   │   │   ├── DualAnalysisPanel.tsx    ← Root AMD showcase component
│   │   │   ├── FastColumn.tsx           ← Left: 7B results, ~500 ms
│   │   │   ├── DeepColumn.tsx           ← Right: 32B results, ~5 s
│   │   │   ├── ConsensusPanel.tsx       ← Merged findings + recommendation banner
│   │   │   ├── BenchmarkBar.tsx         ← Live latency + parallel savings bar
│   │   │   ├── FindingCard.tsx
│   │   │   ├── SeverityBadge.tsx
│   │   │   ├── PRLinkCard.tsx
│   │   │   └── CodeInput.tsx
│   │   ├── lib/
│   │   │   ├── api.ts
│   │   │   └── sse.ts                   ← useParallaxStream hook (AbortController cleanup)
│   │   └── types/
│   │       └── parallax.ts
│
├── benchmarks/
│   ├── k6-fanout-test.js               ← 50 VU load test against POST /analyze
│   └── results/
│       ├── k6-report.json              ← Committed after real run
│       └── criterion-report/           ← Criterion HTML report
│
└── scripts/
    ├── setup-amd.sh                     ← Launch both vLLM instances + health check
    ├── seed-vector-db.sh                ← Initialize Supabase pgvector table
    └── demo-scan.sh                     ← SSE stream printer for demo recording
```

---

## API Reference

### Pipeline service (`http://localhost:8000`)

#### `POST /scan`

Start a new scan. Returns immediately.

**Request body:**
```json
{
  "trigger": "https://github.com/owner/repo@main",
  "target_branch": "main"
}
```

**Response:**
```json
{
  "scan_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "stream_url": "/scan/550e8400-e29b-41d4-a716-446655440000/stream"
}
```

#### `GET /scan/{scan_id}/stream`

Server-Sent Events stream. Stays open until `pipeline_complete`, then closes. Reconnect-safe — the client can re-connect and events are replayed from `last_index`.

**Event types:**

| Event | When | Key fields in `data` |
|---|---|---|
| `agent_start` | Agent begins | `agent`, `timestamp` |
| `agent_complete` | Agent finishes | `agent`, `latency_ms`, `findings_count` |
| `parallel_models_complete` | Both analysts done | `fast_latency_ms`, `deep_latency_ms`, `parallel_savings_ms` |
| `pipeline_complete` | All agents done | `scan_id`, `pr_url` |
| `agent_error` | Agent threw | `agent`, `error` |

**Example SSE stream:**
```
event: agent_start
data: {"event":"agent_start","agent":"fast_analyst","timestamp":"2026-05-11T00:00:00Z","data":{}}

event: agent_complete
data: {"event":"agent_complete","agent":"fast_analyst","timestamp":"...","data":{"latency_ms":487,"findings_count":3}}

event: agent_complete
data: {"event":"agent_complete","agent":"deep_analyst","timestamp":"...","data":{"latency_ms":4832,"findings_count":2}}

event: parallel_models_complete
data: {"event":"parallel_models_complete","agent":"system","timestamp":"...","data":{"fast_latency_ms":487,"deep_latency_ms":4832,"parallel_savings_ms":487}}

event: pipeline_complete
data: {"event":"pipeline_complete","agent":"system","timestamp":"...","data":{"scan_id":"...","pr_url":"https://github.com/owner/repo/pull/42"}}
```

#### `GET /scan/{scan_id}`

Full pipeline state for a completed or running scan. Returns the entire `ParallaxState` object.

#### `GET /scan/{scan_id}/report`

Full audit report as `text/markdown` with a `Content-Disposition: attachment` header.

#### `GET /health`

Checks all downstream dependencies. Returns `200` only when everything is reachable.

```json
{
  "status": "ok",
  "dependencies": {
    "rust_engine": "ok",
    "fast_model": "ok",
    "deep_model": "ok",
    "supabase": "ok"
  }
}
```

---

### Rust engine (`http://localhost:3000`)

#### `POST /analyze`

Fan-out entry point. Dispatches to both vLLM instances simultaneously via `tokio::join!`.

**Request:**
```json
{
  "text": "< code or concatenated file contents >",
  "scan_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Response:**
```json
{
  "scan_id": "550e8400-e29b-41d4-a716-446655440000",
  "fast": {
    "model": "Qwen2.5-Coder-7B-Instruct",
    "response": "{ ... }",
    "latency_ms": 487,
    "timed_out": false
  },
  "deep": {
    "model": "Qwen2.5-Coder-32B-Instruct",
    "response": "{ ... }",
    "latency_ms": 4832,
    "timed_out": false
  }
}
```

If one model exceeds `RUST_ENGINE_FANOUT_TIMEOUT_MS`, its slot is returned with `"timed_out": true` and `"response": null`. The pipeline handles graceful degradation; the overall request still returns `200`.

Returns `503 Service Unavailable` immediately when all 25 buffer slots are occupied — never queues indefinitely.

#### `POST /embed`

Embed a text string for pgvector indexing.

```json
{ "text": "< file content >" }
```

#### `GET /health`

Returns `{"status": "ok"}`.

#### `GET /metrics`

Prometheus text format. Metrics exposed:

| Metric | Type | Description |
|---|---|---|
| `parallax_fanout_requests_total` | Counter | Labels: `status=200\|503\|partial` |
| `parallax_fast_model_latency_ms` | Histogram | Per-request fast model latency |
| `parallax_deep_model_latency_ms` | Histogram | Per-request deep model latency |
| `parallax_fanout_latency_ms` | Histogram | Wall time for both models to complete |
| `parallax_free_slots` | Gauge | Available buffer slots (max 25) |
| `parallax_partial_responses_total` | Counter | Requests where one model timed out |

---

## Observability

Prometheus scrapes both the Rust engine (`/metrics`) and the pipeline (`/metrics`) every 15 seconds. Access the Prometheus UI at `http://localhost:9090`.

Useful queries:

```promql
# P95 fan-out wall latency
histogram_quantile(0.95, rate(parallax_fanout_latency_ms_bucket[5m]))

# Partial response rate (one model timed out)
rate(parallax_partial_responses_total[5m]) / rate(parallax_fanout_requests_total[5m])

# Free buffer slots (alert if this hits 0)
parallax_free_slots
```

---

## Guardrails

The pipeline enforces three hard limits before any agent runs:

| Guardrail | Behaviour |
|---|---|
| System memory < 1 GB free | Returns `503` immediately — never OOMs |
| IP rate limit exceeded | Returns `429` with `Retry-After: 60` |
| File > `PIPELINE_MAX_FILE_SIZE_MB` | File skipped, scan continues — never crashes |
| Pipeline > `PIPELINE_TIMEOUT_S` (300 s) | Marked `error`, SSE stream closed cleanly |

The Rust engine enforces a fourth: if all 25 buffer slots are occupied, `POST /analyze` returns `503` without touching the crossbeam channel — never a blocking `send`.

---

## Stack

| Layer | Technology |
|---|---|
| Inference | AMD Instinct MI300X · ROCm 6.x · vLLM |
| Models | Qwen2.5-Coder-7B-Instruct · Qwen2.5-Coder-32B-Instruct |
| Fan-out gateway | Rust 1.85 · Axum 0.8 · Tokio 1.52 · crossbeam 0.8 |
| Orchestration | Python 3.11 · FastAPI 0.110 · LangGraph 0.0.31 |
| Vector store | Supabase · pgvector |
| Frontend | Next.js 14.2 · React 18 · TypeScript 5 · Tailwind CSS 3.4 |
| Observability | Prometheus · metrics-exporter-prometheus 0.18 |
| Benchmarking | k6 · Criterion 0.5 |
| Containerization | Docker Compose v2 |

---

## Contributing

1. Fork the repository and create a feature branch from `main`.
2. Run the pipeline tests before opening a PR: `cd pipeline && python -m pytest tests/ -v`
3. Run the Rust linter: `cd rust-engine && cargo clippy -- -D warnings`
4. Run the frontend type check: `cd frontend && npm run build`
5. Open a pull request against `main` with a clear description of what changed and why.

All CI checks must pass. Benchmark numbers must not regress by more than 10 % without explanation.

---

## License

MIT — see [LICENSE](LICENSE).

---

*Scanned by [Parallax](https://github.com/aryan105825/parallax) · Dual-model parallel AI on AMD Instinct MI300X*

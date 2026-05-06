# Parallax — Production Startup Guide

> AMD Instinct MI300X · Dual-model parallel AI on ROCm + vLLM

---

## Prerequisites

| Requirement | Version | Check |
|---|---|---|
| Docker + Docker Compose v2 | 24+ / 2.x | `docker compose version` |
| ROCm | 6.x | `rocm-smi` |
| vLLM (ROCm build) | 0.4+ | `vllm --version` |
| Rust toolchain | 1.85+ | `rustc --version` |
| Python | 3.11+ | `python3 --version` |
| Node.js | 18+ | `node --version` |
| curl | any | `curl --version` |

---

## Step 0 — Clone & configure environment

```bash
git clone https://github.com/aryan105825/parallax.git
cd parallax
cp .env.example .env
```

Open `.env` and fill in every variable:

```bash
# AMD Developer Cloud — two separate vLLM endpoints
AMD_FAST_MODEL_URL=http://localhost:8001
AMD_DEEP_MODEL_URL=http://localhost:8002
AMD_API_KEY=<your AMD Developer Cloud key>

# Model identifiers (defaults match vLLM serve commands below)
FAST_MODEL=Qwen/Qwen2.5-Coder-7B-Instruct
DEEP_MODEL=Qwen/Qwen2.5-Coder-32B-Instruct

# Rust engine
RUST_ENGINE_URL=http://rust-engine:3000
RUST_ENGINE_MAX_SLOTS=25
RUST_ENGINE_FANOUT_TIMEOUT_MS=8000

# Pipeline
PIPELINE_URL=http://pipeline:8000
PIPELINE_RATE_LIMIT_PER_MIN=20
PIPELINE_MAX_FILE_SIZE_MB=10
PIPELINE_TIMEOUT_S=300

# GitHub — needs repo + pull_request scopes
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# Supabase — create a free project at supabase.com
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_KEY=eyJ...
VECTOR_TABLE=parallax_embeddings

# Frontend
NEXT_PUBLIC_PIPELINE_URL=http://localhost:8000
NEXT_PUBLIC_APP_NAME=Parallax
```

---

## Step 1 — Supabase schema (one-time)

Run this in the Supabase SQL editor (`project → SQL Editor → New query`):

```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Main embeddings table
CREATE TABLE IF NOT EXISTS parallax_embeddings (
    id          BIGSERIAL PRIMARY KEY,
    scan_id     TEXT          NOT NULL,
    file_path   TEXT          NOT NULL,
    content     TEXT          NOT NULL,
    embedding   VECTOR(768)   NOT NULL,
    created_at  TIMESTAMPTZ   DEFAULT NOW()
);

-- IVFFlat index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS parallax_embeddings_embedding_idx
    ON parallax_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- RPC function used by retrieve_similar() in vector_store.py
CREATE OR REPLACE FUNCTION match_parallax_embeddings(
    query_embedding VECTOR(768),
    match_count     INT DEFAULT 3
)
RETURNS TABLE (
    file_path  TEXT,
    content    TEXT,
    scan_id    TEXT,
    similarity FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        file_path,
        content,
        scan_id,
        1 - (embedding <=> query_embedding) AS similarity
    FROM parallax_embeddings
    ORDER BY embedding <=> query_embedding
    LIMIT match_count;
$$;
```

---

## Step 2 — Start AMD vLLM instances (on the MI300X host)

```bash
chmod +x scripts/setup-amd.sh
./scripts/setup-amd.sh
```

This script starts both vLLM processes and polls each endpoint with exponential
back-off (up to ~10 minutes for the 32B model). Once both print ✓ you are ready
for the next step. Logs are written to `vllm_fast.log` and `vllm_deep.log`.

**Memory allocation on MI300X:**

```
Qwen2.5-Coder-7B  → gpu_memory_utilization=0.12  ≈ 14 GB
Qwen2.5-Coder-32B → gpu_memory_utilization=0.55  ≈ 64 GB
─────────────────────────────────────────────────────────
Total                                              ≈ 78 GB of 192 GB used
Headroom for KV cache                              ≈ 114 GB
```

---

## Step 3 — Start all services via Docker Compose

```bash
docker compose up --build
```

Docker Compose starts services in dependency order:

```
rust-engine  →  healthcheck passes (port 3000 ready)
     ↓
pipeline     →  healthcheck passes (port 8000 ready)
     ↓
frontend     →  starts (port 3001)
prometheus   →  starts (port 9090)  — independent
```

The `service_healthy` condition in `docker-compose.yml` ensures the pipeline
never starts before the Rust engine binary is actually accepting connections.

**Service URLs after startup:**

| Service | URL |
|---|---|
| Frontend (Next.js) | http://localhost:3001 |
| Pipeline API (FastAPI) | http://localhost:8000 |
| Pipeline API docs | http://localhost:8000/docs |
| Rust Engine | http://localhost:3000 |
| Prometheus | http://localhost:9090 |

---

## Step 4 — Verify everything is healthy

```bash
# Check all four dependencies (rust_engine, fast_model, deep_model, supabase)
curl -s http://localhost:8000/health | python3 -m json.tool
```

Expected output:

```json
{
  "status": "ok",
  "dependencies": {
    "rust_engine": "ok",
    "fast_model":  "ok",
    "deep_model":  "ok",
    "supabase":    "ok"
  }
}
```

If any dependency shows `"unreachable"`, see the troubleshooting section below.

---

## Step 5 — Run a scan

### Via browser (recommended for demo)

1. Open http://localhost:3001
2. Paste a GitHub repo URL, e.g. `https://github.com/digininja/DVWA`
3. Watch both columns stream simultaneously — the Fast (7B) column populates
   at ~500 ms, the Deep (32B) at ~5 s
4. The BenchmarkBar shows parallel wall time vs sequential baseline
5. A real GitHub PR link appears at the bottom when complete

### Via API (for integration / testing)

```bash
# 1. Start a scan
SCAN=$(curl -s -X POST http://localhost:8000/scan \
  -H "Content-Type: application/json" \
  -d '{"trigger":"https://github.com/digininja/DVWA","target_branch":"main"}')
echo $SCAN

SCAN_ID=$(echo $SCAN | python3 -c "import sys,json; print(json.load(sys.stdin)['scan_id'])")

# 2. Stream SSE events
curl -N "http://localhost:8000/scan/$SCAN_ID/stream"

# 3. Get full state when complete
curl -s "http://localhost:8000/scan/$SCAN_ID" | python3 -m json.tool

# 4. Download markdown report
curl -o "report_$SCAN_ID.md" "http://localhost:8000/scan/$SCAN_ID/report"
```

---

## Step 6 — Run the test suite

```bash
# Pipeline unit tests
cd pipeline
pip install -r requirements.txt
python -m pytest tests/ -v

# Rust linter
cd ../rust-engine
cargo clippy -- -D warnings

# Rust unit tests
cargo test

# Criterion fan-out benchmark (requires no live vLLM — uses wiremock)
cargo bench --bench fanout_throughput

# Frontend type check
cd ../frontend
npm install
npm run build

# k6 load test (requires running Rust engine on port 3000)
k6 run --out json=benchmarks/results/k6-report.json benchmarks/k6-fanout-test.js

# End-to-end smoke test
pip install sseclient-py
python3 scripts/e2e_test.py
```

---

## Prometheus queries

Access Prometheus at http://localhost:9090 and try:

```promql
# P95 fan-out wall latency (parallel both models)
histogram_quantile(0.95, rate(parallax_fanout_latency_ms_bucket[5m]))

# Parallel savings rate — fast model latency saved per request
histogram_quantile(0.50, rate(parallax_fast_model_latency_ms_bucket[5m]))

# Partial response rate (one model timed out)
rate(parallax_partial_responses_total[5m])
  / rate(parallax_fanout_requests_total[5m])

# Free buffer slots — alert if this hits 0
parallax_free_slots

# Pipeline HTTP request rate by endpoint
rate(http_requests_total{job="pipeline"}[1m])
```

---

## Troubleshooting

### rust_engine: unreachable

```bash
docker compose logs rust-engine
# Check port binding
docker compose ps rust-engine
```

### fast_model / deep_model: unreachable

```bash
tail -f vllm_fast.log   # for 7B
tail -f vllm_deep.log   # for 32B

# Check ROCm sees the GPU
rocm-smi

# Verify vLLM endpoints directly
curl http://localhost:8001/health
curl http://localhost:8002/health
```

### supabase: unreachable

```bash
# Check SUPABASE_URL is set correctly in .env (must include https://)
grep SUPABASE_URL .env

# Test connectivity manually
curl -H "apikey: $SUPABASE_ANON_KEY" "$SUPABASE_URL/rest/v1/"
```

### Pipeline /metrics returns 404

Confirm `prometheus-fastapi-instrumentator==6.1.0` is installed inside the
container:

```bash
docker compose exec pipeline pip show prometheus-fastapi-instrumentator
```

### Docker race condition on first boot

If the pipeline starts before the Rust engine is ready, restart just the
pipeline service:

```bash
docker compose restart pipeline
```

The healthcheck + `condition: service_healthy` in `docker-compose.yml` prevents
this on subsequent starts.

---

## Production checklist before submission

- [ ] AMD vLLM instances confirmed running on MI300X (`rocm-smi` shows GPU load)
- [ ] `curl http://localhost:8000/health` shows all four deps `"ok"`
- [ ] Real k6 benchmark run captured in `benchmarks/results/k6-report.json`
- [ ] Real Criterion numbers captured and pasted into README Benchmark section
- [ ] Demo video recorded (FastColumn + DeepColumn streaming simultaneously)
- [ ] Hugging Face Space URL added to README Live Demo section
- [ ] Vercel URL added to README Live Demo section
- [ ] Demo video URL added to README Live Demo section

#!/bin/bash
# setup-amd.sh - ROCm + dual vLLM instance setup for Parallax

set -euo pipefail

AMD_FAST_MODEL_URL="${AMD_FAST_MODEL_URL:-http://localhost:8001}"
AMD_DEEP_MODEL_URL="${AMD_DEEP_MODEL_URL:-http://localhost:8002}"

echo "Starting Fast model (Qwen2.5-Coder-7B) on port 8001..."
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct \
  --port 8001 \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.12 \
  --tensor-parallel-size 1 > vllm_fast.log 2>&1 &
VLLM_FAST_PID=$!

echo "Starting Deep model (Qwen2.5-Coder-32B) on port 8002..."
vllm serve Qwen/Qwen2.5-Coder-32B-Instruct \
  --port 8002 \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.55 \
  --tensor-parallel-size 1 > vllm_deep.log 2>&1 &
VLLM_DEEP_PID=$!

# ── Wait for each endpoint with exponential back-off ─────────────────────────
# The 32B model can take 3-10 minutes to load into HBM3 depending on disk
# speed.  A fixed sleep will either be too short (fail early) or waste time.
wait_for_endpoint() {
  local name="$1"
  local url="$2"
  local max_attempts=60   # 60 × up to 10s = max ~10 min
  local attempt=0
  local delay=5

  echo "Waiting for $name at $url ..."
  while (( attempt < max_attempts )); do
    if curl -sf "$url/health" | grep -q '"status"'; then
      echo "  ✓ $name is ready (attempt $((attempt+1)))"
      return 0
    fi
    attempt=$(( attempt + 1 ))
    # Exponential back-off capped at 10s so we're not polling every 5s forever
    delay=$(( delay < 10 ? delay + 1 : 10 ))
    echo "  · $name not ready yet (attempt $attempt/$max_attempts), retrying in ${delay}s..."
    sleep "$delay"
  done

  echo "ERROR: $name did not become ready after $max_attempts attempts." >&2
  echo "       Check logs: tail -f ${name,,}.log" >&2
  return 1
}

wait_for_endpoint "fast_model" "$AMD_FAST_MODEL_URL"
wait_for_endpoint "deep_model" "$AMD_DEEP_MODEL_URL"

echo ""
echo "Both models loaded and healthy."
echo "  Fast (7B) : $AMD_FAST_MODEL_URL  [PID $VLLM_FAST_PID]"
echo "  Deep (32B): $AMD_DEEP_MODEL_URL  [PID $VLLM_DEEP_PID]"

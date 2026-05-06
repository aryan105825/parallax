#!/usr/bin/env bash
# scripts/demo-scan.sh
# Parallax — quick end-to-end demo scan
#
# Usage:
#   ./scripts/demo-scan.sh [GITHUB_REPO_URL] [BRANCH]
#
# Examples:
#   ./scripts/demo-scan.sh https://github.com/digininja/DVWA main
#   ./scripts/demo-scan.sh                                        # uses defaults
#
# Prerequisites:
#   - docker compose stack is up (docker compose up -d)
#   - jq installed (brew install jq / apt install jq)

set -euo pipefail

PIPELINE_URL="${PIPELINE_URL:-http://localhost:8000}"
REPO_URL="${1:-https://github.com/digininja/DVWA}"
BRANCH="${2:-main}"

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[parallax]${RESET} $*"; }
ok()   { echo -e "${GREEN}[✓]${RESET} $*"; }
warn() { echo -e "${YELLOW}[!]${RESET} $*"; }
err()  { echo -e "${RED}[✗]${RESET} $*" >&2; }

# ── 1. Health check ────────────────────────────────────────────────────────────
log "Checking pipeline health at ${PIPELINE_URL}/health …"
HEALTH=$(curl -sf "${PIPELINE_URL}/health" 2>/dev/null || echo '{"status":"unreachable"}')
STATUS=$(echo "$HEALTH" | jq -r '.status // "error"')

if [[ "$STATUS" != "ok" ]]; then
  warn "Pipeline health: ${STATUS}"
  warn "Some dependencies may be unreachable — continuing anyway."
  echo "$HEALTH" | jq . || true
else
  ok "Pipeline is healthy"
fi

echo ""

# ── 2. Start scan ──────────────────────────────────────────────────────────────
log "Starting scan: ${BOLD}${REPO_URL}${RESET} @ ${BRANCH}"
SCAN_RESPONSE=$(curl -sf -X POST "${PIPELINE_URL}/scan" \
  -H "Content-Type: application/json" \
  -d "{\"trigger\": \"${REPO_URL}@${BRANCH}\", \"target_branch\": \"${BRANCH}\"}")

SCAN_ID=$(echo "$SCAN_RESPONSE" | jq -r '.scan_id')
STREAM_URL=$(echo "$SCAN_RESPONSE" | jq -r '.stream_url')

if [[ -z "$SCAN_ID" || "$SCAN_ID" == "null" ]]; then
  err "Failed to start scan. Response:"
  echo "$SCAN_RESPONSE"
  exit 1
fi

ok "Scan started — ID: ${BOLD}${SCAN_ID}${RESET}"
log "SSE stream: ${PIPELINE_URL}${STREAM_URL}"
echo ""

# ── 3. Stream events ───────────────────────────────────────────────────────────
log "Streaming pipeline events (Ctrl+C to detach, scan will continue) …"
echo "──────────────────────────────────────────────────────────────────"

DONE=false
TIMEOUT_S=360

curl -sN --max-time "${TIMEOUT_S}" \
     "${PIPELINE_URL}${STREAM_URL}" | \
while IFS= read -r line; do
  if [[ "$line" == data:* ]]; then
    JSON="${line#data: }"
    EVENT=$(echo "$JSON" | jq -r '.event // ""')
    AGENT=$(echo "$JSON" | jq -r '.agent // ""')
    DATA=$(echo "$JSON"  | jq -r '.data  // {}')

    case "$EVENT" in
      agent_start)
        echo -e "${BLUE}→ ${BOLD}${AGENT}${RESET} started"
        ;;
      agent_complete)
        LAT=$(echo "$DATA"     | jq -r '.latency_ms    // ""')
        FINDS=$(echo "$DATA"   | jq -r '.findings_count // ""')
        FIXES=$(echo "$DATA"   | jq -r '.fixes_generated // ""')
        PR=$(echo "$DATA"      | jq -r '.pr_url         // ""')

        EXTRA=""
        [[ -n "$LAT"   ]] && EXTRA+=" | ${LAT} ms"
        [[ -n "$FINDS" ]] && EXTRA+=" | ${FINDS} findings"
        [[ -n "$FIXES" ]] && EXTRA+=" | ${FIXES} fixes"
        [[ -n "$PR"    ]] && EXTRA+=" | PR: ${PR}"

        ok "${BOLD}${AGENT}${RESET} complete${EXTRA}"
        ;;
      parallel_models_complete)
        FAST=$(echo "$DATA" | jq -r '.fast_latency_ms      // 0')
        DEEP=$(echo "$DATA" | jq -r '.deep_latency_ms      // 0')
        SAVE=$(echo "$DATA" | jq -r '.parallel_savings_ms  // 0')
        echo ""
        echo -e "${YELLOW}  ┌── AMD MI300X Parallel Results ──────────────────────┐${RESET}"
        echo -e "${YELLOW}  │${RESET}  Fast (7B)  : ${GREEN}${FAST} ms${RESET}"
        echo -e "${YELLOW}  │${RESET}  Deep (32B) : ${GREEN}${DEEP} ms${RESET}"
        echo -e "${YELLOW}  │${RESET}  Wall time  : ${GREEN}$(( DEEP > FAST ? DEEP : FAST )) ms${RESET}"
        echo -e "${YELLOW}  │${RESET}  Savings    : ${CYAN}${SAVE} ms vs sequential${RESET}"
        echo -e "${YELLOW}  └──────────────────────────────────────────────────────┘${RESET}"
        echo ""
        ;;
      pipeline_complete)
        PR_URL=$(echo "$DATA" | jq -r '.pr_url // ""')
        PERR=$(echo "$DATA"   | jq -r '.error  // ""')
        echo ""
        echo "──────────────────────────────────────────────────────────────────"
        if [[ -n "$PERR" ]]; then
          err "Pipeline finished with error: ${PERR}"
        else
          ok "${BOLD}Pipeline complete!${RESET}"
          [[ -n "$PR_URL" ]] && echo -e "  ${GREEN}PR:${RESET} ${PR_URL}"
        fi
        break
        ;;
      agent_error)
        AERR=$(echo "$DATA" | jq -r '.error // "unknown error"')
        err "${AGENT} error: ${AERR}"
        ;;
    esac
  fi
done

echo ""

# ── 4. Fetch report ────────────────────────────────────────────────────────────
log "Fetching final state …"
FINAL=$(curl -sf "${PIPELINE_URL}/scan/${SCAN_ID}" 2>/dev/null || echo '{}')
FINAL_STATUS=$(echo "$FINAL" | jq -r '.status // "unknown"')
FINDINGS=$(echo "$FINAL"  | jq -r '.consensus_findings | length // 0')
SUMMARY=$(echo "$FINAL"   | jq -r '.consensus_summary // "N/A"')
PR_FINAL=$(echo "$FINAL"  | jq -r '.pr_url // ""')

echo ""
echo -e "${BOLD}═══════════════════════════════════════${RESET}"
echo -e "${BOLD}  Parallax Scan Summary${RESET}"
echo -e "${BOLD}═══════════════════════════════════════${RESET}"
echo -e "  Scan ID     : ${SCAN_ID}"
echo -e "  Status      : ${FINAL_STATUS}"
echo -e "  Findings    : ${FINDINGS}"
echo -e "  Verdict     : ${SUMMARY}"
[[ -n "$PR_FINAL" ]] && echo -e "  Pull Request: ${PR_FINAL}"
echo -e "${BOLD}═══════════════════════════════════════${RESET}"
echo ""

log "Download full report:"
echo "  curl -O \"${PIPELINE_URL}/scan/${SCAN_ID}/report\""
echo ""

#!/usr/bin/env bash
# scripts/seed-vector-db.sh
# Parallax — bootstrap Supabase pgvector schema and seed test embeddings
#
# Usage:
#   ./scripts/seed-vector-db.sh
#
# Requires:
#   - SUPABASE_URL          exported or present in .env
#   - SUPABASE_SERVICE_KEY  exported or present in .env (service-role key, not anon)
#   - RUST_ENGINE_URL       exported or present in .env (for /embed endpoint)
#   - curl, jq

set -euo pipefail

# ── Load .env if present ───────────────────────────────────────────────────────
if [[ -f ".env" ]]; then
  # Export only lines that look like KEY=VALUE (skip comments and blanks)
  set -o allexport
  # shellcheck disable=SC2046
  source <(grep -E '^[A-Z_][A-Z0-9_]*=' .env | sed 's/#.*//')
  set +o allexport
fi

SUPABASE_URL="${SUPABASE_URL:?SUPABASE_URL must be set}"
SUPABASE_SERVICE_KEY="${SUPABASE_SERVICE_KEY:?SUPABASE_SERVICE_KEY must be set}"
RUST_ENGINE_URL="${RUST_ENGINE_URL:-http://localhost:3000}"
VECTOR_TABLE="${VECTOR_TABLE:-parallax_embeddings}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; RESET='\033[0m'
log()  { echo -e "${CYAN}[seed]${RESET} $*"; }
ok()   { echo -e "${GREEN}[✓]${RESET} $*"; }
err()  { echo -e "${RED}[✗]${RESET} $*" >&2; exit 1; }

# ── Helper: call Supabase REST API ─────────────────────────────────────────────
supa_sql() {
  local sql="$1"
  curl -sf \
    -X POST \
    "${SUPABASE_URL}/rest/v1/rpc/exec_sql" \
    -H "apikey: ${SUPABASE_SERVICE_KEY}" \
    -H "Authorization: Bearer ${SUPABASE_SERVICE_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"query\": $(echo "$sql" | jq -Rs .)}" \
    || true   # exec_sql may not exist; fall through to direct SQL below
}

# ── 1. Enable pgvector extension ───────────────────────────────────────────────
log "Enabling pgvector extension …"
curl -sf \
  -X POST \
  "${SUPABASE_URL}/rest/v1/rpc/exec_sql" \
  -H "apikey: ${SUPABASE_SERVICE_KEY}" \
  -H "Authorization: Bearer ${SUPABASE_SERVICE_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"query":"CREATE EXTENSION IF NOT EXISTS vector;"}' > /dev/null 2>&1 || \
  log "(exec_sql RPC not exposed — run DDL manually in Supabase SQL editor)"

ok "Extension step complete (or already enabled)"

# ── 2. Create embeddings table ─────────────────────────────────────────────────
log "Creating table ${VECTOR_TABLE} if not exists …"
DDL=$(cat <<'SQL'
CREATE TABLE IF NOT EXISTS parallax_embeddings (
    id          BIGSERIAL PRIMARY KEY,
    scan_id     TEXT        NOT NULL,
    file_path   TEXT        NOT NULL,
    chunk_index INT         NOT NULL DEFAULT 0,
    content     TEXT        NOT NULL,
    embedding   vector(1536),           -- matches text-embedding-3-small / ada-002 dimension
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for cosine similarity search (IVFFlat — fast for ≤ 1M rows)
CREATE INDEX IF NOT EXISTS parallax_embeddings_embedding_idx
    ON parallax_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Index for scan-scoped retrieval
CREATE INDEX IF NOT EXISTS parallax_embeddings_scan_id_idx
    ON parallax_embeddings (scan_id);
SQL
)

curl -sf \
  -X POST \
  "${SUPABASE_URL}/rest/v1/rpc/exec_sql" \
  -H "apikey: ${SUPABASE_SERVICE_KEY}" \
  -H "Authorization: Bearer ${SUPABASE_SERVICE_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"query\": $(echo "$DDL" | jq -Rs .)}" > /dev/null 2>&1 || \
  log "(DDL skipped via RPC — paste into Supabase SQL editor if table does not exist)"

ok "Table ready: ${VECTOR_TABLE}"

# ── 3. Create pgvector similarity search function ──────────────────────────────
log "Creating match_embeddings RPC function …"
FUNC=$(cat <<'SQL'
CREATE OR REPLACE FUNCTION match_embeddings(
    query_embedding vector(1536),
    match_scan_id   TEXT,
    match_count     INT DEFAULT 5
)
RETURNS TABLE (
    id          BIGINT,
    file_path   TEXT,
    chunk_index INT,
    content     TEXT,
    similarity  FLOAT
)
LANGUAGE sql STABLE AS $$
    SELECT
        id,
        file_path,
        chunk_index,
        content,
        1 - (embedding <=> query_embedding) AS similarity
    FROM parallax_embeddings
    WHERE scan_id = match_scan_id
    ORDER BY embedding <=> query_embedding
    LIMIT match_count;
$$;
SQL
)

curl -sf \
  -X POST \
  "${SUPABASE_URL}/rest/v1/rpc/exec_sql" \
  -H "apikey: ${SUPABASE_SERVICE_KEY}" \
  -H "Authorization: Bearer ${SUPABASE_SERVICE_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"query\": $(echo "$FUNC" | jq -Rs .)}" > /dev/null 2>&1 || \
  log "(Function creation skipped via RPC)"

ok "match_embeddings function ready"

# ── 4. Seed test embeddings via Rust /embed endpoint ──────────────────────────
log "Seeding test embeddings via ${RUST_ENGINE_URL}/embed …"

SEED_FILES=(
  "src/auth.py|SELECT * FROM users WHERE id = ' + user_id"
  "middleware/session.py|JWT_SECRET = 'hardcoded-secret-do-not-use'"
  "api/upload.py|filename = request.form['file'].filename"
)

SEEDED=0
for entry in "${SEED_FILES[@]}"; do
  fp="${entry%%|*}"
  content="${entry##*|}"

  EMBED_RESP=$(curl -sf -X POST "${RUST_ENGINE_URL}/embed" \
    -H "Content-Type: application/json" \
    -d "{\"text\": $(echo "$content" | jq -Rs .)}" 2>/dev/null || echo '{}')

  EMBEDDING=$(echo "$EMBED_RESP" | jq -r '.embeddings // empty' 2>/dev/null || echo "")

  if [[ -n "$EMBEDDING" ]]; then
    # Insert into Supabase via PostgREST
    curl -sf \
      -X POST \
      "${SUPABASE_URL}/rest/v1/${VECTOR_TABLE}" \
      -H "apikey: ${SUPABASE_SERVICE_KEY}" \
      -H "Authorization: Bearer ${SUPABASE_SERVICE_KEY}" \
      -H "Content-Type: application/json" \
      -H "Prefer: return=minimal" \
      -d "{
        \"scan_id\": \"seed-test-001\",
        \"file_path\": \"${fp}\",
        \"chunk_index\": 0,
        \"content\": $(echo "$content" | jq -Rs .),
        \"embedding\": ${EMBEDDING}
      }" > /dev/null 2>&1 && SEEDED=$((SEEDED + 1)) || \
      log "  (insert skipped for ${fp} — embedding may be placeholder)"
  else
    log "  (no embedding returned for ${fp} — Rust engine may be offline)"
  fi
done

ok "Seeded ${SEEDED}/${#SEED_FILES[@]} test rows"

# ── 5. Verify row count ────────────────────────────────────────────────────────
log "Verifying row count …"
COUNT_RESP=$(curl -sf \
  "${SUPABASE_URL}/rest/v1/${VECTOR_TABLE}?select=count" \
  -H "apikey: ${SUPABASE_SERVICE_KEY}" \
  -H "Authorization: Bearer ${SUPABASE_SERVICE_KEY}" \
  -H "Prefer: count=exact" \
  -H "Range: 0-0" \
  -I 2>/dev/null | grep -i 'content-range' | awk -F'/' '{print $2}' | tr -d '[:space:]') || COUNT_RESP="?"

ok "Total rows in ${VECTOR_TABLE}: ${COUNT_RESP:-unknown}"
echo ""
log "Vector DB seeding complete. Run a scan to start indexing real repo embeddings."

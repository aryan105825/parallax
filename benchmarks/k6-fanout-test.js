/**
 * benchmarks/k6-fanout-test.js
 * Parallax — dual-model parallel fan-out load test
 *
 * Scenario: ramp to 50 VUs over 30 s → sustain 60 s → ramp down 10 s
 * Target:   POST /analyze on the Rust engine with a 300-token code snippet
 *
 * Run:
 *   k6 run --out json=benchmarks/results/k6-report.json benchmarks/k6-fanout-test.js
 *
 * Thresholds:
 *   p95 wall time < 6 000 ms   (both models, parallel)
 *   error rate    = 0 %
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Counter, Rate } from 'k6/metrics';

// ── Custom metrics ────────────────────────────────────────────────────────────
const fastLatency  = new Trend('parallax_fast_latency_ms',  true);
const deepLatency  = new Trend('parallax_deep_latency_ms',  true);
const wallLatency  = new Trend('parallax_fanout_wall_ms',   true);
const partialCount = new Counter('parallax_partial_responses');
const errorRate    = new Rate('parallax_error_rate');

// ── Scenario config ───────────────────────────────────────────────────────────
export const options = {
  scenarios: {
    fanout_ramp: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 50 },  // ramp up
        { duration: '60s', target: 50 },  // sustain
        { duration: '10s', target: 0  },  // ramp down
      ],
    },
  },
  thresholds: {
    // P95 total wall time (both models) must stay under 6 s
    'http_req_duration{name:analyze}': ['p(95)<6000'],
    // Zero failed HTTP requests
    'http_req_failed{name:analyze}': ['rate==0'],
  },
};

// ── 300-token representative code snippet ─────────────────────────────────────
const CODE_SNIPPET = `
import os, sqlite3
from flask import Flask, request, jsonify

app = Flask(__name__)
DB = os.getenv("DB_PATH", "/tmp/app.db")

def get_conn():
    return sqlite3.connect(DB)

@app.route("/user")
def get_user():
    uid = request.args.get("id")
    conn = get_conn()
    cur = conn.cursor()
    # CWE-89: SQL injection — user input concatenated directly
    cur.execute("SELECT * FROM users WHERE id = " + uid)
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify({"id": row[0], "name": row[1], "email": row[2]})

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    username = data.get("username", "")
    password = data.get("password", "")
    conn = get_conn()
    cur = conn.cursor()
    # CWE-89: second injection point
    query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
    cur.execute(query)
    user = cur.fetchone()
    conn.close()
    if user:
        # CWE-798: hard-coded secret used as JWT key
        import jwt
        token = jwt.encode({"sub": user[0]}, "supersecret123", algorithm="HS256")
        return jsonify({"token": token})
    return jsonify({"error": "invalid credentials"}), 401

if __name__ == "__main__":
    # CWE-676: debug=True exposes Werkzeug debugger in production
    app.run(debug=True, host="0.0.0.0", port=5000)
`.trim();

// ── Rust engine URL (override via K6_RUST_ENGINE_URL env var) ─────────────────
const RUST_ENGINE_URL = __ENV.K6_RUST_ENGINE_URL || 'http://localhost:3002';

// ── Main VU loop ──────────────────────────────────────────────────────────────
export default function () {
  const payload = JSON.stringify({
    text: CODE_SNIPPET,
    scan_id: `k6-${__VU}-${__ITER}`,
  });

  const params = {
    headers: { 'Content-Type': 'application/json' },
    tags:    { name: 'analyze' },
  };

  const res = http.post(`${RUST_ENGINE_URL}/analyze`, payload, params);

  const ok = check(res, {
    'status is 200': (r) => r.status === 200,
    'has fast field': (r) => {
      try { return JSON.parse(r.body).fast !== undefined; } catch { return false; }
    },
    'has deep field': (r) => {
      try { return JSON.parse(r.body).deep !== undefined; } catch { return false; }
    },
  });

  errorRate.add(!ok);

  if (res.status === 200) {
    try {
      const body = JSON.parse(res.body);

      if (body.fast?.latency_ms) fastLatency.add(body.fast.latency_ms);
      if (body.deep?.latency_ms) deepLatency.add(body.deep.latency_ms);

      // Wall time = max of both (they ran in parallel)
      const wall = Math.max(
        body.fast?.latency_ms ?? 0,
        body.deep?.latency_ms ?? 0,
      );
      wallLatency.add(wall);

      // Count partial responses (one model timed out)
      if (body.fast?.timed_out || body.deep?.timed_out) {
        partialCount.add(1);
      }
    } catch (_) {
      // JSON parse failure — already captured by errorRate
    }
  }

  // Brief think-time so we don't hammer with zero delay
  sleep(0.1);
}

// ── End-of-test summary ───────────────────────────────────────────────────────
export function handleSummary(data) {
  const p95Wall = data.metrics['parallax_fanout_wall_ms']?.values?.['p(95)'] ?? 0;
  const p95Http = data.metrics['http_req_duration{name:analyze}']?.values?.['p(95)'] ?? 0;
  const totalReqs = data.metrics['http_reqs']?.values?.count ?? 0;
  const partials  = data.metrics['parallax_partial_responses']?.values?.count ?? 0;

  console.log('');
  console.log('══════════════════════════════════════════════');
  console.log('  Parallax fan-out load test — summary');
  console.log('══════════════════════════════════════════════');
  console.log(`  Total requests  : ${totalReqs}`);
  console.log(`  P95 HTTP dur    : ${p95Http.toFixed(0)} ms`);
  console.log(`  P95 wall time   : ${p95Wall.toFixed(0)} ms  (parallel both models)`);
  console.log(`  Partial resp    : ${partials}  (one model timed out)`);
  console.log('══════════════════════════════════════════════');

  return {
    'benchmarks/results/k6-summary.txt': textSummary(data, { indent: ' ', enableColors: false }),
    stdout: '\n',
  };
}

// k6 ships textSummary via handleSummary helpers — inline minimal fallback
function textSummary(data, _opts) {
  let out = 'k6 load test summary\n';
  for (const [name, metric] of Object.entries(data.metrics)) {
    out += `\n${name}\n`;
    for (const [k, v] of Object.entries(metric.values)) {
      out += `  ${k}: ${typeof v === 'number' ? v.toFixed(3) : v}\n`;
    }
  }
  return out;
}

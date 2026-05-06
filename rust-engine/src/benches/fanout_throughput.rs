/// Criterion micro-benchmark: Rust engine fan-out throughput.
///
/// Measures the wall-clock latency of issuing two concurrent `POST
/// /v1/chat/completions` requests — one to a mock "fast" (7B) backend and one
/// to a mock "deep" (32B) backend — joined with `tokio::join!`, exactly as the
/// production [`crate::fanout::call_model`] path does.
///
/// Why wiremock-rs instead of hitting `localhost:3000`
/// ---------------------------------------------------
/// The previous benchmark (`benches/gateway_bench.rs`) called the live Rust
/// engine, which required a running server and two live vLLM instances.  That
/// made CI flaky and meant the benchmark measured network RTT + vLLM cold-start
/// rather than the fan-out logic.  wiremock-rs starts an in-process HTTP server
/// on a random OS-assigned port so the bench is fully self-contained and
/// deterministic at any configured simulated latency.
///
/// What is being measured
/// ----------------------
/// `concurrent_fanout` — a `tokio::join!` of two HTTP calls to mock vLLM
/// backends.  The configured simulated latencies (500 ms fast, 2 000 ms deep)
/// mean the wall time should converge to ≈ 2 000 ms, demonstrating that
/// parallel dispatch costs `max(fast, deep)` not `fast + deep`.
///
/// Running the benchmark
/// ---------------------
/// ```bash
/// cargo bench --bench fanout_throughput
/// ```
use criterion::{criterion_group, criterion_main, Criterion};
use reqwest::Client;
use serde_json::json;
use std::time::Duration;
use wiremock::{
    matchers::{method, path},
    Mock, MockServer, ResponseTemplate,
};

// ── Mock helpers ──────────────────────────────────────────────────────────────

/// Spin up a wiremock server that mimics a vLLM `/v1/chat/completions` endpoint.
///
/// `delay` allows the caller to simulate model inference latency so the
/// benchmark reflects realistic timing without touching real GPU hardware.
async fn start_mock_vllm(delay: Duration) -> MockServer {
    let server = MockServer::start().await;

    let body = json!({
        "id": "chatcmpl-bench",
        "object": "chat.completion",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                // Minimal valid JSON so the fanout parser can deserialise it.
                "content": "{\"findings\":[]}"
            },
            "finish_reason": "stop"
        }],
        "usage": { "prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18 }
    });

    Mock::given(method("POST"))
        .and(path("/v1/chat/completions"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_json(body)
                .set_delay(delay),
        )
        // Mount permanently — the mock server lives for the duration of the bench.
        .mount(&server)
        .await;

    server
}

// ── Benchmark ─────────────────────────────────────────────────────────────────

fn fanout_throughput(c: &mut Criterion) {
    // Build a multi-threaded runtime so `tokio::join!` can drive both futures
    // concurrently on the same runtime the bench uses.
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("tokio runtime");

    // Start both mock vLLM servers once; they survive for the entire benchmark.
    // Simulated latencies mirror the target hardware numbers from the README:
    //   fast (7B)  ≈  500 ms
    //   deep (32B) ≈ 2000 ms  (reduced from 5 s to keep bench wall-time sane)
    let (fast_server, deep_server) = runtime.block_on(async {
        tokio::join!(
            start_mock_vllm(Duration::from_millis(500)),
            start_mock_vllm(Duration::from_millis(2_000)),
        )
    });

    let client = Client::new();
    // Capture the URIs — MockServer URIs include a random port assigned by the OS.
    let fast_uri = fast_server.uri();
    let deep_uri = deep_server.uri();

    let mut group = c.benchmark_group("gateway_throughput");
    // 10-second measurement window with 50 samples as specified.
    group.measurement_time(Duration::from_secs(10));
    group.sample_size(50);

    group.bench_function("concurrent_fanout", |b| {
        b.to_async(&runtime).iter(|| {
            let client    = client.clone();
            let fast_uri  = fast_uri.clone();
            let deep_uri  = deep_uri.clone();

            async move {
                // `scan_id` is required by the `/analyze` request schema and
                // must be propagated all the way to the vLLM call bodies so
                // tracing can correlate events across the fan-out legs.
                let scan_id = uuid::Uuid::new_v4().to_string();

                let fast_body = json!({
                    "model": "Qwen/Qwen2.5-Coder-7B-Instruct",
                    "messages": [{
                        "role": "user",
                        "content": "fn unsafe_query(input: &str) { db.execute(input); }"
                    }],
                    "temperature": 0.1,
                    "response_format": { "type": "json_object" },
                    // scan_id is threaded through so benchmarks match the
                    // real request shape produced by analyze_handler.
                    "scan_id": scan_id,
                });

                let deep_body = json!({
                    "model": "Qwen/Qwen2.5-Coder-32B-Instruct",
                    "messages": [{
                        "role": "user",
                        "content": "fn unsafe_query(input: &str) { db.execute(input); }"
                    }],
                    "temperature": 0.1,
                    "response_format": { "type": "json_object" },
                    "scan_id": scan_id,
                });

                let fast_req = client
                    .post(format!("{}/v1/chat/completions", fast_uri))
                    .json(&fast_body)
                    .send();

                let deep_req = client
                    .post(format!("{}/v1/chat/completions", deep_uri))
                    .json(&deep_body)
                    .send();

                // Mirror the production tokio::join! — both futures run
                // concurrently on the same thread-pool.
                let (fast_res, deep_res) = tokio::join!(fast_req, deep_req);

                // Consume responses so the runtime actually waits for the body.
                let _ = fast_res.map(|r| r.error_for_status());
                let _ = deep_res.map(|r| r.error_for_status());
            }
        });
    });

    group.finish();
}

criterion_group!(benches, fanout_throughput);
criterion_main!(benches);

use criterion::{criterion_group, criterion_main, Criterion};
use std::time::Duration;
use reqwest::Client;

/// Legacy benchmark — requires a live Rust engine on localhost:3000.
///
/// Prefer `fanout_throughput` (`src/benches/fanout_throughput.rs`) for CI and
/// offline runs; it uses wiremock-rs mocks and needs no running server.
async fn mock_fanout_request(client: &Client) {
    // NOTE: scan_id is a required field in the AnalyzeRequest schema.
    let _ = client
        .post("http://127.0.0.1:3000/analyze")
        .json(&serde_json::json!({
            "text": "Benchmark text input",
            "scan_id": uuid::Uuid::new_v4().to_string()
        }))
        .send()
        .await;
}

fn criterion_benchmark(c: &mut Criterion) {
    let mut group = c.benchmark_group("gateway_throughput");

    group.measurement_time(Duration::from_secs(10));
    group.sample_size(50);

    let runtime = tokio::runtime::Runtime::new().unwrap();
    let client = Client::new();

    group.bench_function("concurrent_fanout", |b| {
        b.to_async(&runtime).iter(|| mock_fanout_request(&client));
    });

    group.finish();
}

criterion_group!(benches, criterion_benchmark);
criterion_main!(benches);

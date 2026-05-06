mod buffer;
mod fanout;
mod queue;
mod routes;

use axum::{routing::{get, post}, Router};
use std::sync::Arc;
use tokio::net::TcpListener;
use dotenvy::dotenv;
use metrics_exporter_prometheus::{PrometheusBuilder, PrometheusHandle};

/// Shared application state injected into every Axum handler via [`axum::extract::State`].
///
/// All fields are cheap to clone — `Arc` for the pool, plain structs for the
/// rest — so Axum can clone state per-request at zero allocation cost.
#[derive(Clone)]
pub struct AppState {
    pub buffer_pool: Arc<buffer::BufferPool>,
    pub fanout_client: reqwest::Client,
    pub config: AppConfig,
    /// Handle to the Prometheus registry.  Stored here (rather than captured
    /// in a closure) so [`routes::metrics::metrics_handler`] can render it
    /// using the standard `State` extractor pattern.
    pub prometheus_handle: PrometheusHandle,
}

/// Runtime configuration values read from environment variables at startup.
#[derive(Clone)]
pub struct AppConfig {
    /// Base URL of the fast vLLM instance (e.g. `http://localhost:8001`).
    pub fast_model_url: String,
    /// Base URL of the deep vLLM instance (e.g. `http://localhost:8002`).
    pub deep_model_url: String,
    pub fast_model_id: String,
    pub deep_model_id: String,
    /// Per-request timeout for each vLLM call, in milliseconds.
    pub fanout_timeout_ms: u64,
    /// Base URL of the embedding vLLM instance (e.g. `http://localhost:8001`).
    /// Defaults to `AMD_FAST_MODEL_URL` when `EMBEDDING_MODEL_URL` is unset
    /// because Qwen-Coder-7B already supports the `/v1/embeddings` endpoint.
    pub embed_model_url: String,
    /// Model identifier sent in the `model` field of the embedding request.
    pub embed_model_id: String,
}

#[tokio::main]
async fn main() {
    dotenv().ok();

    // ── Prometheus setup ─────────────────────────────────────────────────────
    let builder = PrometheusBuilder::new();
    let recorder = builder.build_recorder();
    let prometheus_handle = recorder.handle();
    metrics::set_global_recorder(recorder).unwrap();

    // ── Environment variables ─────────────────────────────────────────────────
    let fast_model_url = std::env::var("AMD_FAST_MODEL_URL")
        .expect("AMD_FAST_MODEL_URL must be set");
    let deep_model_url = std::env::var("AMD_DEEP_MODEL_URL")
        .expect("AMD_DEEP_MODEL_URL must be set");
    let fast_model_id = std::env::var("FAST_MODEL")
        .unwrap_or_else(|_| "Qwen/Qwen2.5-Coder-7B-Instruct".to_string());
    let deep_model_id = std::env::var("DEEP_MODEL")
        .unwrap_or_else(|_| "Qwen/Qwen2.5-Coder-32B-Instruct".to_string());
    let max_slots = std::env::var("RUST_ENGINE_MAX_SLOTS")
        .unwrap_or_else(|_| "25".to_string())
        .parse::<usize>()
        .expect("RUST_ENGINE_MAX_SLOTS must be a positive integer");
    let timeout_ms = std::env::var("RUST_ENGINE_FANOUT_TIMEOUT_MS")
        .unwrap_or_else(|_| "8000".to_string())
        .parse::<u64>()
        .expect("RUST_ENGINE_FANOUT_TIMEOUT_MS must be a positive integer");

    // Embedding model can be a dedicated endpoint or reuse the fast model —
    // Qwen-Coder-7B-Instruct supports /v1/embeddings out of the box with vLLM.
    let embed_model_url = std::env::var("EMBEDDING_MODEL_URL")
        .unwrap_or_else(|_| fast_model_url.clone());
    let embed_model_id = std::env::var("EMBEDDING_MODEL")
        .unwrap_or_else(|_| fast_model_id.clone());

    let config = AppConfig {
        fast_model_url,
        deep_model_url,
        fast_model_id,
        deep_model_id,
        fanout_timeout_ms: timeout_ms,
        embed_model_url,
        embed_model_id,
    };

    let buffer_pool = Arc::new(buffer::BufferPool::new(max_slots));
    let fanout_client = reqwest::Client::new();

    let state = AppState {
        buffer_pool,
        fanout_client,
        config,
        prometheus_handle,
    };

    // ── Router ────────────────────────────────────────────────────────────────
    let app = Router::new()
        .route("/health",  get(routes::health::health_handler))
        // metrics_handler reads the handle from AppState — no closure capture needed.
        .route("/metrics", get(routes::metrics::metrics_handler))
        .route("/analyze", post(routes::analyze::analyze_handler))
        .route("/embed",   post(routes::embed::embed_handler))
        .with_state(state);

    let listener = TcpListener::bind("0.0.0.0:3000").await.unwrap();
    println!("Rust Engine listening on port 3000");
    axum::serve(listener, app).await.unwrap();
}

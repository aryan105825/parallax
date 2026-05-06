use axum::{extract::State, Json, http::StatusCode};
use serde::{Deserialize, Serialize};
use std::time::Instant;
use crate::AppState;

#[derive(Deserialize)]
pub struct AnalyzeRequest {
    pub text: String,
    pub scan_id: String,
}

#[derive(Serialize)]
pub struct AnalyzeResponse {
    pub scan_id: String,
    pub fast: Option<ModelResponse>,
    pub deep: Option<ModelResponse>,
}

#[derive(Serialize)]
pub struct ModelResponse {
    pub model: String,
    pub response: Option<String>,
    pub latency_ms: u64,
    pub timed_out: bool,
}

pub async fn analyze_handler(
    State(state): State<AppState>,
    Json(payload): Json<AnalyzeRequest>,
) -> Result<Json<AnalyzeResponse>, (StatusCode, String)> {
    let start_time = Instant::now();

    // 1. Acquire one buffer slot (or 503 immediately if none free).
    let _slot = state.buffer_pool.acquire().ok_or_else(|| {
        metrics::counter!("parallax_fanout_requests_total", "status" => "503").increment(1);
        (StatusCode::SERVICE_UNAVAILABLE, "No buffer slots available".to_string())
    })?;

    // 2. Spawn two tokio::tasks
    let fast_task = crate::fanout::call_model(
        state.fanout_client.clone(),
        state.config.fast_model_url.clone(),
        state.config.fast_model_id.clone(),
        payload.text.clone(),
        state.config.fanout_timeout_ms,
    );

    let deep_task = crate::fanout::call_model(
        state.fanout_client.clone(),
        state.config.deep_model_url.clone(),
        state.config.deep_model_id.clone(),
        payload.text.clone(),
        state.config.fanout_timeout_ms,
    );

    // 3. Collect both responses using tokio::join!
    let (fast_res, deep_res) = tokio::join!(fast_task, deep_task);

    let fast = fast_res.unwrap_or_else(|_| ModelResponse {
        model: state.config.fast_model_id.clone(),
        response: None,
        latency_ms: state.config.fanout_timeout_ms,
        timed_out: true,
    });

    let deep = deep_res.unwrap_or_else(|_| ModelResponse {
        model: state.config.deep_model_id.clone(),
        response: None,
        latency_ms: state.config.fanout_timeout_ms,
        timed_out: true,
    });

    let wall_latency = start_time.elapsed().as_millis() as f64;
    metrics::histogram!("parallax_fanout_latency_ms").record(wall_latency);

    metrics::histogram!("parallax_fast_model_latency_ms").record(fast.latency_ms as f64);
    metrics::histogram!("parallax_deep_model_latency_ms").record(deep.latency_ms as f64);

    if fast.timed_out || deep.timed_out {
        metrics::counter!("parallax_partial_responses_total").increment(1);
        metrics::counter!("parallax_fanout_requests_total", "status" => "partial").increment(1);
    } else {
        metrics::counter!("parallax_fanout_requests_total", "status" => "200").increment(1);
    }

    Ok(Json(AnalyzeResponse {
        scan_id: payload.scan_id,
        fast: Some(fast),
        deep: Some(deep),
    }))
}

/// Prometheus `/metrics` route handler.
///
/// Renders the current Prometheus registry snapshot in the standard text
/// exposition format (version 0.0.4).  The [`metrics_exporter_prometheus::PrometheusHandle`]
/// is stored in [`crate::AppState`] so this handler participates in Axum's
/// normal dependency-injection flow rather than capturing a handle via a
/// `move` closure in `main`.
use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
};

use crate::AppState;

/// GET /metrics — Prometheus text exposition (content-type `text/plain; version=0.0.4`).
///
/// Scrapes the global [`metrics`] registry through the handle embedded in
/// [`AppState`] and serialises all counters, histograms, and gauges registered
/// by the fan-out pipeline.
///
/// Metrics exposed:
///
/// | Name | Type | Labels |
/// |---|---|---|
/// | `parallax_fanout_requests_total` | Counter | `status=200\|503\|partial` |
/// | `parallax_fast_model_latency_ms` | Histogram | — |
/// | `parallax_deep_model_latency_ms` | Histogram | — |
/// | `parallax_fanout_latency_ms`     | Histogram | — |
/// | `parallax_free_slots`            | Gauge     | — |
/// | `parallax_partial_responses_total` | Counter | — |
pub async fn metrics_handler(State(state): State<AppState>) -> Response {
    let body = state.prometheus_handle.render();
    Response::builder()
        .status(StatusCode::OK)
        // Prometheus scraper requires this exact content-type header.
        .header(
            axum::http::header::CONTENT_TYPE,
            "text/plain; version=0.0.4; charset=utf-8",
        )
        .body(axum::body::Body::from(body))
        .expect("static response builder never fails")
        .into_response()
}

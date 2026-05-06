/// POST /embed — text embedding route.
///
/// Accepts a JSON body `{ "text": "..." }` and returns a vector of `f32`
/// embedding coefficients produced by the configured embedding model.
///
/// The handler calls the vLLM-compatible `POST /v1/embeddings` endpoint
/// (OpenAI embeddings format) specified by `EMBEDDING_MODEL_URL`.  Both
/// request and response are fully typed so the Python pipeline can deserialise
/// them without a schema lookup.
///
/// Error behaviour:
/// * `502 Bad Gateway` — upstream vLLM unreachable or returned a non-2xx status.
/// * `422 Unprocessable Entity` — response body did not contain `data[0].embedding`.
use axum::{extract::State, http::StatusCode, Json};
use serde::{Deserialize, Serialize};

use crate::AppState;

#[derive(Deserialize)]
pub struct EmbedRequest {
    pub text: String,
}

#[derive(Serialize)]
pub struct EmbedResponse {
    /// Full embedding vector returned by the model.
    pub embeddings: Vec<f32>,
    /// Model identifier echoed from the upstream response.
    pub model: String,
    /// Dimensionality of the returned vector (convenience field for the
    /// Python caller so it can validate before inserting into pgvector).
    pub dim: usize,
}

pub async fn embed_handler(
    State(state): State<AppState>,
    Json(payload): Json<EmbedRequest>,
) -> Result<Json<EmbedResponse>, StatusCode> {
    // Build the OpenAI-compatible embeddings request body.
    let request_body = serde_json::json!({
        "model": state.config.embed_model_id,
        "input": payload.text,
        "encoding_format": "float"
    });

    let upstream_response = state
        .fanout_client
        .post(format!("{}/v1/embeddings", state.config.embed_model_url))
        .json(&request_body)
        .send()
        .await
        .map_err(|e| {
            tracing::error!("embed upstream request failed: {e}");
            StatusCode::BAD_GATEWAY
        })?;

    if !upstream_response.status().is_success() {
        tracing::error!(
            "embed upstream returned status {}",
            upstream_response.status()
        );
        return Err(StatusCode::BAD_GATEWAY);
    }

    let resp_json: serde_json::Value = upstream_response.json().await.map_err(|e| {
        tracing::error!("embed upstream body parse failed: {e}");
        StatusCode::BAD_GATEWAY
    })?;

    // OpenAI embeddings response shape:
    //   { "data": [ { "embedding": [f32, ...], "index": 0 } ], "model": "...", ... }
    let embedding_array = resp_json["data"][0]["embedding"]
        .as_array()
        .ok_or_else(|| {
            tracing::error!("embed response missing data[0].embedding field");
            StatusCode::UNPROCESSABLE_ENTITY
        })?;

    let embeddings: Vec<f32> = embedding_array
        .iter()
        .filter_map(|v| v.as_f64().map(|f| f as f32))
        .collect();

    let dim = embeddings.len();

    // Echo the model name from the upstream response; fall back to the
    // configured model id so the Python caller always gets a non-null value.
    let model = resp_json["model"]
        .as_str()
        .unwrap_or(&state.config.embed_model_id)
        .to_string();

    Ok(Json(EmbedResponse {
        embeddings,
        model,
        dim,
    }))
}

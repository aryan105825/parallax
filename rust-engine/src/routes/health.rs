use axum::{Json, http::StatusCode};
use serde_json::{json, Value};

pub async fn health_handler() -> Result<Json<Value>, StatusCode> {
    Ok(Json(json!({ "status": "ok" })))
}

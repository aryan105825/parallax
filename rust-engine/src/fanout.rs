use reqwest::Client;
use serde_json::json;
use std::time::{Duration, Instant};
use tokio::time::timeout;

use crate::routes::analyze::ModelResponse;

pub async fn call_model(
    client: Client,
    url: String,
    model_id: String,
    text: String,
    timeout_ms: u64,
) -> Result<ModelResponse, ()> {
    let start = Instant::now();
    let body = json!({
        "model": model_id,
        "messages": [
            { "role": "user", "content": text }
        ],
        "temperature": 0.1,
        "response_format": { "type": "json_object" }
    });

    let request = client.post(format!("{}/v1/chat/completions", url)).json(&body).send();

    match timeout(Duration::from_millis(timeout_ms), request).await {
        Ok(Ok(response)) => {
            let latency_ms = start.elapsed().as_millis() as u64;
            if response.status().is_success() {
                let json_resp: serde_json::Value = response.json().await.map_err(|_| ())?;
                let content = json_resp["choices"][0]["message"]["content"]
                    .as_str()
                    .map(|s| s.to_string());
                Ok(ModelResponse {
                    model: model_id,
                    response: content,
                    latency_ms,
                    timed_out: false,
                })
            } else {
                Err(())
            }
        }
        _ => Err(()), // timeout or request error
    }
}

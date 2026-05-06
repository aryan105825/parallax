import requests
import os

def get_embedding(text: str) -> list:
    url = os.getenv("RUST_ENGINE_URL", "http://localhost:3000")
    try:
        resp = requests.post(f"{url}/embed", json={"text": text}, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("embeddings", [])
    except Exception:
        pass
    return []

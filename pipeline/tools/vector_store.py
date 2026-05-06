"""
vector_store.py — Supabase + pgvector integration for Parallax.

Table schema (must exist in Supabase before running):

    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS parallax_embeddings (
        id          BIGSERIAL PRIMARY KEY,
        scan_id     TEXT        NOT NULL,
        file_path   TEXT        NOT NULL,
        content     TEXT        NOT NULL,
        embedding   VECTOR(768) NOT NULL,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE INDEX ON parallax_embeddings
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100);

Implements:
  index_files      — embed every file and INSERT into parallax_embeddings
  retrieve_similar — cosine-similarity search over pgvector, returns top-k
"""

import os
import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supabase REST helpers
# ---------------------------------------------------------------------------

def _supabase_headers(use_service_key: bool = True) -> dict:
    key = (
        os.getenv("SUPABASE_SERVICE_KEY", "")
        if use_service_key
        else os.getenv("SUPABASE_ANON_KEY", "")
    )
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _supabase_url() -> str:
    base = os.getenv("SUPABASE_URL", "").rstrip("/")
    if not base:
        raise EnvironmentError("SUPABASE_URL environment variable is not set.")
    return base


def _table() -> str:
    return os.getenv("VECTOR_TABLE", "parallax_embeddings")


# ---------------------------------------------------------------------------
# Embedding helper — delegates to the Rust /embed endpoint
# ---------------------------------------------------------------------------

def _get_embedding(text: str) -> Optional[list[float]]:
    """
    Call the Rust engine's /embed endpoint.  Returns a list of floats or None
    on failure.  Importing here (not at module top) avoids circular imports.
    """
    try:
        from tools.embedding_client import get_embedding  # noqa: PLC0415
        vec = get_embedding(text)
        if vec:
            return vec
    except Exception as exc:
        logger.warning("Embedding call failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def index_files(scan_id: str, file_contents: dict[str, str]) -> None:
    """
    Embed every file in *file_contents* and upsert the vectors into Supabase
    pgvector.

    Rows are inserted in batches of 20 to stay well within Supabase's
    request-body size limit.
    """
    if not file_contents:
        return

    base_url = _supabase_url()
    endpoint = f"{base_url}/rest/v1/{_table()}"
    headers = _supabase_headers()
    batch: list[dict] = []

    for file_path, content in file_contents.items():
        if content.startswith("[SKIPPED"):
            continue

        embedding = _get_embedding(content)
        if embedding is None:
            logger.warning("Skipping vector index for %s — embedding returned None", file_path)
            continue

        batch.append(
            {
                "scan_id": scan_id,
                "file_path": file_path,
                "content": content[:8000],  # store a snippet; full content stays in state
                "embedding": embedding,
            }
        )

        if len(batch) >= 20:
            _flush_batch(endpoint, headers, batch)
            batch = []

    if batch:
        _flush_batch(endpoint, headers, batch)


def retrieve_similar(query_text: str, k: int = 3) -> list[dict]:
    """
    Return up to *k* file records whose embedding is closest (cosine) to
    *query_text*'s embedding.

    Each returned dict contains: {"file_path": str, "content": str, "scan_id": str}

    Falls back to [] if Supabase is unreachable or the embedding fails.
    """
    embedding = _get_embedding(query_text)
    if embedding is None:
        logger.warning("retrieve_similar: embedding failed, returning empty list")
        return []

    base_url = _supabase_url()
    # Supabase exposes pgvector cosine search via an RPC function.
    # The function `match_parallax_embeddings` must exist — see migrations/ or
    # the README for the CREATE FUNCTION statement.
    rpc_endpoint = f"{base_url}/rest/v1/rpc/match_parallax_embeddings"
    headers = _supabase_headers(use_service_key=True)
    # Remove "Prefer: return=minimal" for RPC — we need the response body
    headers.pop("Prefer", None)

    payload = {
        "query_embedding": embedding,
        "match_count": k,
    }

    try:
        resp = requests.post(rpc_endpoint, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        results = resp.json()
        # Expected shape: [{"file_path": ..., "content": ..., "scan_id": ...}, ...]
        if isinstance(results, list):
            return results[:k]
    except Exception as exc:
        logger.warning("retrieve_similar RPC failed: %s", exc)

    return []


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _flush_batch(endpoint: str, headers: dict, batch: list[dict]) -> None:
    try:
        resp = requests.post(
            endpoint,
            headers={**headers, "Prefer": "resolution=merge-duplicates"},
            json=batch,
            timeout=30,
        )
        if resp.status_code not in (200, 201, 204):
            logger.warning(
                "Supabase insert returned %s: %s", resp.status_code, resp.text[:200]
            )
    except Exception as exc:
        logger.warning("Supabase batch insert failed: %s", exc)

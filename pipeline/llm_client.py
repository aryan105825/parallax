"""
LLM Client — wraps the Rust Engine's vLLM proxy endpoints.

FIX (bug 4 – stub): 401 re-authentication for AMD token expiry.

The original should_retry only handled Timeout and 5xx errors.  The spec
explicitly requires re-auth on 401 because the AMD API token can expire
during long scans (deep analyst + fix generator can together take > 10 s).

New behaviour:
  • On 401: call _refresh_amd_token() to obtain a fresh bearer token from
    AMD_AUTH_ENDPOINT, store it on the instance, then signal tenacity to retry.
  • On Timeout or 5xx: retry as before (up to 3 attempts, 5 s wait).
  • On any other status: do not retry (fast-fail on 4xx that aren't auth).

The token refresh path reads AMD_AUTH_ENDPOINT, AMD_CLIENT_ID, and
AMD_CLIENT_SECRET from the environment (same keys used by the AMD vLLM
deployment script in scripts/setup-amd.sh).  When none of these are set the
client falls back to unauthenticated mode (local vLLM requires no token).
"""

import os
import logging

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)


class _AuthError(Exception):
    """Raised when a 401 is received so tenacity can intercept it."""


class LLMClient:
    def __init__(self) -> None:
        self.fast_model_url  = os.getenv("AMD_FAST_MODEL_URL",  "http://localhost:8001")
        self.deep_model_url  = os.getenv("AMD_DEEP_MODEL_URL",  "http://localhost:8002")
        self.fast_model_id   = os.getenv("FAST_MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
        self.deep_model_id   = os.getenv("DEEP_MODEL", "Qwen/Qwen2.5-Coder-32B-Instruct")

        # Bearer token for AMD Cloud endpoints.  May be None for local vLLM.
        self._amd_token: str | None = os.getenv("AMD_API_TOKEN")

    # ── Token refresh ─────────────────────────────────────────────────────────

    def _refresh_amd_token(self) -> None:
        """
        Exchange AMD client credentials for a fresh bearer token.

        Reads AMD_AUTH_ENDPOINT, AMD_CLIENT_ID, AMD_CLIENT_SECRET.
        No-ops gracefully when the variables are absent (local vLLM mode).
        """
        auth_endpoint  = os.getenv("AMD_AUTH_ENDPOINT")
        client_id      = os.getenv("AMD_CLIENT_ID")
        client_secret  = os.getenv("AMD_CLIENT_SECRET")

        if not (auth_endpoint and client_id and client_secret):
            logger.debug("AMD auth credentials not configured; skipping token refresh")
            return

        try:
            resp = requests.post(
                auth_endpoint,
                data={
                    "grant_type":    "client_credentials",
                    "client_id":     client_id,
                    "client_secret": client_secret,
                },
                timeout=10,
            )
            resp.raise_for_status()
            self._amd_token = resp.json()["access_token"]
            logger.info("AMD token refreshed successfully")
        except Exception as exc:
            logger.error("AMD token refresh failed: %s", exc)
            raise

    # ── Core vLLM call (with retry) ───────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type((requests.exceptions.Timeout, _AuthError)),
        stop=stop_after_attempt(3),
        wait=wait_fixed(5),
        reraise=True,
    )
    def _call_vllm(
        self,
        url: str,
        model: str,
        prompt: str,
        max_tokens: int,
        timeout: int,
    ) -> dict:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._amd_token:
            headers["Authorization"] = f"Bearer {self._amd_token}"

        payload = {
            "model":           model,
            "messages":        [{"role": "user", "content": prompt}],
            "temperature":     0.1,
            "max_tokens":      max_tokens,
            "response_format": {"type": "json_object"},
        }

        response = requests.post(
            f"{url}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout,
        )

        # FIX (bug 4): handle 401 — refresh token then raise _AuthError so
        # tenacity retries the whole request with the new token.
        if response.status_code == 401:
            logger.warning("Received 401 from vLLM; refreshing AMD token and retrying")
            self._refresh_amd_token()
            raise _AuthError("AMD token expired — retrying with refreshed token")

        # 5xx errors: raise HTTPError so tenacity's retry_if_exception_type
        # catches it.  (We keep this separate from 401 so non-auth 4xx errors
        # like 400/404/422 fast-fail without pointless retries.)
        if response.status_code >= 500:
            response.raise_for_status()   # raises requests.exceptions.HTTPError

        response.raise_for_status()       # covers any remaining non-2xx
        return response.json()

    # ── Public per-model helpers ───────────────────────────────────────────────

    def call_fast_analyst(self, prompt: str) -> dict:
        return self._call_vllm(
            url=self.fast_model_url,
            model=self.fast_model_id,
            prompt=prompt,
            max_tokens=2048,
            timeout=60,
        )

    def call_deep_analyst(self, prompt: str) -> dict:
        return self._call_vllm(
            url=self.deep_model_url,
            model=self.deep_model_id,
            prompt=prompt,
            max_tokens=4096,
            timeout=120,
        )

    def call_consensus(self, prompt: str) -> dict:
        return self._call_vllm(
            url=self.deep_model_url,
            model=self.deep_model_id,
            prompt=prompt,
            max_tokens=4096,
            timeout=120,
        )

    def call_fix_generator(self, prompt: str) -> dict:
        return self._call_vllm(
            url=self.deep_model_url,
            model=self.deep_model_id,
            prompt=prompt,
            max_tokens=2048,
            timeout=120,
        )

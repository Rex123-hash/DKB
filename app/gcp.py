"""Small, lazy Google Cloud boundary for DukanBook's AI providers.

Nothing in this module is imported by the browser and it does not decide when
Google Cloud is enabled.  The private source-code switch lives in app.config.
Application Default Credentials (ADC) are used throughout; API keys and
downloaded service-account JSON files are deliberately unsupported.
"""
from __future__ import annotations

import os
from functools import lru_cache

import requests

_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


@lru_cache(maxsize=1)
def _credentials():
    import google.auth

    credentials, discovered_project = google.auth.default(scopes=[_CLOUD_SCOPE])
    return credentials, discovered_project


def project_id() -> str:
    configured = (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    if configured:
        return configured
    _, discovered = _credentials()
    if discovered:
        return discovered
    raise RuntimeError("GOOGLE_CLOUD_PROJECT is required when GCP is enabled")


def access_token() -> str:
    from google.auth.transport.requests import Request

    credentials, _ = _credentials()
    if not credentials.valid or not credentials.token:
        credentials.refresh(Request())
    return str(credentials.token)


_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _post_with_retry(url: str, *, attempts: int = 3, **kwargs):
    """POST with short backoff on rate limits, outages, and read timeouts.

    Bill scanning is interactive: a shared-quota 429 or a slow read should cost
    a couple of seconds, not force the shopkeeper to photograph the bill again.
    """
    import time

    delay = 2.0
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.post(url, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
        else:
            if response.status_code not in _RETRY_STATUSES:
                return response
            last_error = None
            if attempt == attempts - 1:
                return response
        if attempt < attempts - 1:
            time.sleep(delay)
            delay *= 2
    if last_error is not None:
        raise last_error
    raise RuntimeError("request failed after retries")


def vertex_openai_provider() -> tuple[str, str, dict[str, str], float]:
    """OpenAI-compatible Vertex endpoint used by the existing tool loop."""
    location = os.environ.get("GCP_VERTEX_LOCATION", "global").strip() or "global"
    model = os.environ.get("GCP_VERTEX_MODEL", "gemini-3.6-flash").strip()
    base = (
        "https://aiplatform.googleapis.com/v1/projects/"
        f"{project_id()}/locations/{location}/endpoints/openapi"
    )
    return (
        f"{base}/chat/completions",
        model if model.startswith("google/") else f"google/{model}",
        {
            "Authorization": f"Bearer {access_token()}",
            "Content-Type": "application/json",
        },
        float(os.environ.get("GCP_VERTEX_TIMEOUT", "60")),
    )


def vertex_generate_content(
    *,
    parts: list[dict],
    response_schema: dict,
    temperature: float = 0,
) -> dict:
    """Call Gemini on Vertex AI with a structured JSON response schema."""
    location = os.environ.get("GCP_VERTEX_LOCATION", "global").strip() or "global"
    model = os.environ.get("GCP_VERTEX_MODEL", "gemini-3.6-flash").strip()
    endpoint = (
        "https://aiplatform.googleapis.com/v1/projects/"
        f"{project_id()}/locations/{location}/publishers/google/models/"
        f"{model}:generateContent"
    )
    response = _post_with_retry(
        endpoint,
        headers={
            "Authorization": f"Bearer {access_token()}",
            "Content-Type": "application/json",
        },
        json={
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                # Pydantic emits standard JSON Schema (including $defs/refs),
                # so use Vertex's JSON-schema field rather than its smaller
                # OpenAPI responseSchema subset.
                "responseJsonSchema": response_schema,
            },
        },
        timeout=float(os.environ.get("GCP_VERTEX_TIMEOUT", "120")),
    )
    response.raise_for_status()
    payload = response.json()
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise ValueError("Vertex AI returned no structured result") from exc
    import json

    return json.loads(text)


def document_ai_ocr(image_bytes: bytes, mime_type: str) -> str:
    """Optional OCR enrichment. Empty processor ID means vision-only mode."""
    processor_id = (os.environ.get("GCP_DOCUMENT_AI_PROCESSOR_ID") or "").strip()
    if not processor_id:
        return ""

    # Called over REST with the same ADC token as Vertex, so the deployed image
    # does not have to carry the google-cloud-documentai client library.
    import base64

    location = os.environ.get("GCP_DOCUMENT_AI_LOCATION", "us").strip() or "us"
    response = requests.post(
        f"https://{location}-documentai.googleapis.com/v1/projects/"
        f"{project_id()}/locations/{location}/processors/{processor_id}:process",
        headers={
            "Authorization": f"Bearer {access_token()}",
            "Content-Type": "application/json",
        },
        json={
            "skipHumanReview": True,
            "rawDocument": {
                "content": base64.b64encode(image_bytes).decode("ascii"),
                "mimeType": mime_type,
            },
        },
        timeout=float(os.environ.get("GCP_DOCUMENT_AI_TIMEOUT", "60")),
    )
    response.raise_for_status()
    return (response.json().get("document", {}).get("text") or "").strip()

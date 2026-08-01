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
    response = requests.post(
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

    from google.api_core.client_options import ClientOptions
    from google.cloud import documentai

    location = os.environ.get("GCP_DOCUMENT_AI_LOCATION", "us").strip() or "us"
    client = documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(
            api_endpoint=f"{location}-documentai.googleapis.com"
        )
    )
    name = client.processor_path(project_id(), location, processor_id)
    result = client.process_document(
        request=documentai.ProcessRequest(
            name=name,
            raw_document=documentai.RawDocument(
                content=image_bytes,
                mime_type=mime_type,
            ),
        )
    )
    return (result.document.text or "").strip()

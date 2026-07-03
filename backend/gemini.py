"""Thin Gemini client (google-genai SDK, Vertex AI backend).

One entry point — generate_json(): structured output against a responseSchema,
temperature 0, bounded timeout, one retry on transport/parse errors. All Gemini
traffic in the app flows through here so model, auth, and retry policy live in
exactly one place.

Auth is Application Default Credentials via Vertex AI — the same identity the
app already uses for Firestore and GCS (Cloud Run runtime SA in production,
gcloud ADC locally). There is no API key. Error text is truncated in case a
provider message echoes request data.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from . import config


class GeminiError(Exception):
    """The model call failed after retries, or returned unusable output."""


_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(
            vertexai=True,
            project=config.PROJECT_ID,
            location=config.VERTEX_LOCATION,
        )
    return _client


def audio_part(data: bytes, mime_type: str = "audio/mp4"):
    from google.genai import types
    return types.Part.from_bytes(data=data, mime_type=mime_type)


def generate_json(
    prompt: str,
    schema: dict,
    parts: Optional[list] = None,
    thinking_budget: int = 0,
    temperature: float = 0.0,
    max_output_tokens: Optional[int] = None,
    retries: int = 1,
) -> Any:
    """Run one structured-output generation and return the parsed JSON value.

    `parts` are non-text parts (e.g. audio) placed before the prompt.
    `thinking_budget=0` disables thinking (transcription); judge calls pass a
    positive budget. Retries once by default on transport errors, empty output,
    or JSON that fails to parse — a second identical attempt at temperature 0
    only helps for transient failures, which is exactly what it's for.
    """
    from google.genai import types

    cfg = types.GenerateContentConfig(
        temperature=temperature,
        candidate_count=1,
        response_mime_type="application/json",
        response_schema=schema,
        thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget),
        max_output_tokens=max_output_tokens,
        http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT_S * 1000),
    )
    contents = list(parts or []) + [prompt]

    last_err: Optional[Exception] = None
    for _ in range(retries + 1):
        try:
            resp = _get_client().models.generate_content(
                model=config.GEMINI_MODEL, contents=contents, config=cfg
            )
            text = resp.text
            if not text:
                raise GeminiError("empty model response")
            return json.loads(text)
        except GeminiError as e:
            last_err = e
        except json.JSONDecodeError as e:
            last_err = GeminiError(f"unparseable model output: {e}")
        except Exception as e:  # SDK/transport errors — retry once, then surface
            last_err = GeminiError(f"{type(e).__name__}: {str(e)[:300]}")
    raise last_err  # type: ignore[misc]

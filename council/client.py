"""OpenRouter HTTP client with retry, schema validation, and structured results."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from council.config import (
    APP_TITLE,
    HTTP_REFERER,
    MAX_RESPONSE_BYTES,
    MAX_RETRIES,
    OPENROUTER_URL,
    RETRY_BACKOFF_SECONDS,
    RETRYABLE_STATUS_CODES,
    TEMPERATURE,
    TIMEOUT_SECONDS,
    USER_AGENT,
)


class OpenRouterError(Exception):
    """Raised when the OpenRouter API returns an unrecoverable error."""

    def __init__(
        self, message: str, status_code: int | None = None, request_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


@dataclass(frozen=True)
class CallResult:
    """Validated result of a single completion call."""

    content: str
    cost: float
    tokens: int
    latency_s: float
    attempts: int
    request_id: str | None = None


class OpenRouterClient:
    """Stdlib-only OpenRouter chat completions client with retry and schema validation."""

    def __init__(self, api_key: str) -> None:
        if not api_key or not api_key.startswith("sk-or-"):
            raise ValueError("OpenRouter API key must start with 'sk-or-'")
        self._api_key = api_key

    def __repr__(self) -> str:
        # Defense against accidental debug print leaking the key
        return "OpenRouterClient(api_key='sk-or-***REDACTED***')"

    def call(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float = TEMPERATURE,
        metadata: dict[str, Any] | None = None,
    ) -> CallResult:
        """Sends a chat completion request, retrying on transient errors only.

        Optional `metadata` is forwarded inside the JSON body's `metadata` field;
        OpenRouter's Langfuse plugin reads `langfuse_session_id`, `langfuse_user_id`,
        and `langfuse_tags` from there to group traces into a single session.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if metadata:
            payload["metadata"] = metadata
        start = time.perf_counter()
        last_error: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                data, request_id = self._request(payload)
                self._validate_response(data, request_id)
                return CallResult(
                    content=data["choices"][0]["message"]["content"].strip(),
                    cost=float(data.get("usage", {}).get("cost") or 0.0),
                    tokens=int(data.get("usage", {}).get("total_tokens") or 0),
                    latency_s=round(time.perf_counter() - start, 2),
                    attempts=attempt,
                    request_id=request_id,
                )
            except urllib.error.HTTPError as exc:
                # Retry only on rate-limit + server-side transient; fail fast on 4xx auth/bad-request
                if exc.code not in RETRYABLE_STATUS_CODES:
                    body = exc.read().decode(errors="replace")[:500]
                    raise OpenRouterError(
                        f"Non-retryable HTTP {exc.code} for '{model}': {body}",
                        status_code=exc.code,
                    ) from exc
                last_error = exc
            except (urllib.error.URLError, json.JSONDecodeError) as exc:
                # Transport-layer transient errors: retry
                last_error = exc
            # Note: OpenRouterError from _validate_response / size cap propagates immediately —
            # it is a semantic API error, not a transient transport failure

            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                time.sleep(backoff)

        raise OpenRouterError(
            f"All {MAX_RETRIES} attempts failed for model='{model}': "
            f"{type(last_error).__name__ if last_error else 'unknown'}"
        )

    def _request(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "HTTP-Referer": HTTP_REFERER,
                "X-Title": APP_TITLE,
            },
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            request_id = resp.headers.get("x-request-id") or resp.headers.get(
                "openrouter-request-id"
            )
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise OpenRouterError(
                    f"Response exceeded {MAX_RESPONSE_BYTES} bytes cap (possible compromised endpoint)",
                    request_id=request_id,
                )
            return json.loads(raw), request_id

    @staticmethod
    def _validate_response(data: dict[str, Any], request_id: str | None) -> None:
        """Raises OpenRouterError if response shape is malformed or model refused."""
        if not isinstance(data, dict):
            raise OpenRouterError("Response is not a JSON object", request_id=request_id)
        if data.get("error"):
            raise OpenRouterError(f"API error: {data['error']}", request_id=request_id)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterError("Response missing 'choices' array", request_id=request_id)
        message = choices[0].get("message", {})
        if not isinstance(message.get("content"), str):
            # Distinguish model-refusal (content-policy decline) from malformed schema
            refusal = message.get("refusal")
            if refusal:
                raise OpenRouterError(
                    f"Model refused: {str(refusal)[:200]}",
                    request_id=request_id,
                )
            raise OpenRouterError("Response message has no string 'content'", request_id=request_id)

"""OpenRouter HTTP client with retry, schema validation, and structured results."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Final

from council.config import (
    APP_TITLE,
    HTTP_REFERER,
    MAX_RESPONSE_BYTES,
    MAX_RETRIES,
    OPENROUTER_URL,
    PROVIDER_ROUTING,
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
    # The completion id from the RESPONSE BODY (`gen-...`), which the OpenAPI spec
    # marks required on `ChatResult`. It is the documented key for
    # `GET /api/v1/generation?id=…`, whose `data.provider_name` says which provider
    # actually served the answer — the only way to investigate a degraded one.
    #
    # Not the same thing as `request_id` above, which comes from an HTTP header:
    # the E2E run of 2026-08-14 returned NEITHER `x-request-id` nor
    # `openrouter-request-id`, so that field was null on 7 records out of 7. The
    # unit tests never caught it because they mock headers the real API does not send.
    generation_id: str | None = None
    # Why a SUCCESSFUL call carries a reason for stopping: `length` means the provider
    # cut the answer at the token ceiling. The content is present, valid and incomplete
    # — it passes every check here, because every check here is about shape. Discarding
    # this field is how three voters shipped truncated answers marked [OK] for months.
    # The API says "I cut this one"; refusing to read it is a choice, and it was the
    # wrong one.
    finish_reason: str | None = None


# The three trace fields OpenRouter Broadcast reads. An allowlist, not a merge:
# copying a caller's dict wholesale would let it overwrite `model` or `provider`,
# and `provider` is the privacy guarantee.
_BROADCAST_FIELDS: Final[tuple[str, ...]] = ("user", "session_id", "trace")


def _build_payload(
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assembles the request body. PROVIDER_ROUTING is not optional: see config."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "provider": dict(PROVIDER_ROUTING),
    }
    for key in _BROADCAST_FIELDS:
        if metadata and key in metadata:
            payload[key] = metadata[key]
    return payload


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

        Optional `metadata` carries OpenRouter Broadcast trace fields. They are
        TOP-LEVEL keys of the request body — `user`, `session_id`, `trace` — not
        entries inside a `metadata` object: OpenRouter never reads `metadata` for
        this, which is why sessions were never grouped before 2026-07-26.

        Only the three documented keys are forwarded. Copying the dict wholesale
        would let a caller overwrite `model` or `provider`, and `provider` is the
        privacy guarantee.

        Every request carries PROVIDER_ROUTING: without it OpenRouter is free to pick
        any endpoint serving the model, so a model chosen for its zero-retention
        guarantee could be answered by a provider that retains.
        """
        payload = _build_payload(model, messages, max_tokens, temperature, metadata)
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
                    generation_id=data.get("id"),
                    finish_reason=data["choices"][0].get("finish_reason"),
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
            except OpenRouterError as exc:
                # A body-delivered error carries a status code only when OpenRouter named
                # one; the SAME list decides, because a 502 is a 502 whether it arrives in
                # the status line or in the payload. On 2026-08-31 it arrived in the payload
                # and killed a whole run at attempts=1: this clause is that hole.
                # Everything else here is a verdict, not a hiccup — a refusal, a truncation,
                # a malformed schema, the size cap — and carries no code, so it propagates.
                if exc.status_code not in RETRYABLE_STATUS_CODES:
                    raise
                last_error = exc

            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                time.sleep(backoff)

        # The type name alone does not say WHY. On 2026-08-14 a voter died here with
        # "HTTPError" and nothing else: 429 (the rate limit that kept Mistral out of
        # the council in July) and 503 (a provider outage) are the same word, and they
        # call for opposite decisions — change seat, or wait. The status code is the
        # whole diagnosis and it was already in hand.
        cause = type(last_error).__name__ if last_error else "unknown"
        # HTTPError spells it `code`, OpenRouterError spells it `status_code`. Reading only
        # the first would drop the diagnosis exactly for the body-delivered errors this
        # retry path was widened to cover.
        status = getattr(last_error, "code", None) or getattr(last_error, "status_code", None)
        # A body-delivered error carries the provider's own words and the request id. The
        # first draft of this widening kept neither, and so made a PERSISTENT 502 less
        # diagnosable than a 400 — dropping the two fields you take to OpenRouter to ask
        # what happened, in precisely the case that kills a run. Caught by the adversarial
        # review on 2026-08-31; every gate was green over it, coverage included.
        detail = str(last_error) if isinstance(last_error, OpenRouterError) else ""
        raise OpenRouterError(
            f"All {MAX_RETRIES} attempts failed for model='{model}': "
            f"{cause}{f' HTTP {status}' if status else ''}{f' — {detail}' if detail else ''}",
            status_code=status,
            request_id=getattr(last_error, "request_id", None),
        ) from last_error

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
            # OpenRouter answers HTTP 200 and puts the upstream provider's failure in the
            # body: {"error": {"message": "Internal server error", "code": 502}}. Carry the
            # code so `call` can route it through RETRYABLE_STATUS_CODES like any other 502.
            # Anything else — no code, or a code that is not an int — stays status-less and
            # therefore non-retryable: a fault we cannot name is not a fault we can time.
            err = data["error"]
            code = err.get("code") if isinstance(err, dict) else None
            # `isinstance(code, int)` is load-bearing and not decoration: `502.0 in
            # RETRYABLE_STATUS_CODES` is True, so dropping the guard would make a float
            # code retryable. TestErrorInsideBodyRetry pins it — the mutant survived 264
            # green tests until it did.
            # The [:500] matches the cap on the HTTPError branch above: this text is
            # attacker-influenced and now travels further than it used to, into the
            # exhaustion message as well.
            raise OpenRouterError(
                f"API error: {str(err)[:500]}",
                status_code=code if isinstance(code, int) else None,
                request_id=request_id,
            )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterError("Response missing 'choices' array", request_id=request_id)
        choice = choices[0]
        message = choice.get("message", {})
        content = message.get("content")
        # An empty string is as useless as a missing one: a blank voter is not a voter.
        if not isinstance(content, str) or not content.strip():
            # Distinguish model-refusal (content-policy decline) from malformed schema
            refusal = message.get("refusal")
            if refusal:
                raise OpenRouterError(
                    f"Model refused: {str(refusal)[:200]}",
                    request_id=request_id,
                )
            # Reasoning models spend max_tokens on internal thought and only then write
            # `content`. Run out of budget and the answer never arrives: HTTP 200, empty
            # content, a full `reasoning` field and finish_reason='length'.
            #
            # This diagnosis cost the project two model swaps. From May 2026 a voter kept
            # failing on long Italian queries and it was read as a language weakness — the
            # model was replaced twice on that theory. It was never the language: Italian
            # prompts simply make these models think longer, so they hit the ceiling more
            # often. The old error ("no string 'content'") described the symptom and hid
            # the cause. Name it, and the next person loses minutes instead of months.
            if message.get("reasoning") and choice.get("finish_reason") == "length":
                raise OpenRouterError(
                    "Reasoning model exhausted max_tokens before producing an answer "
                    "(finish_reason='length', 'reasoning' populated, 'content' empty). "
                    "Fix: raise max_tokens, send reasoning={'effort':'none'} where the "
                    "model supports it, or use a non-reasoning model for this seat.",
                    request_id=request_id,
                )
            # Last branch: 200 OK, no answer, and no cause we have a name for yet.
            # On 2026-08-10 the weekly sentinel died here (moonshotai/kimi-k2-0905) and
            # left nothing to diagnose with — the message described the hole, not its
            # shape. Carry the fields that tell the next reader which hole it was:
            # a truncation, a provider returning content as a list, a silent stop.
            raise OpenRouterError(
                "Response message has no usable 'content' "
                f"(finish_reason={choice.get('finish_reason')!r}, "
                f"native_finish_reason={choice.get('native_finish_reason')!r}, "
                f"content_type={type(content).__name__}, "
                f"reasoning={'present' if message.get('reasoning') else 'absent'}, "
                # The lookup key for `GET /api/v1/generation?id=…`: this branch is the
                # one with no name yet, so the next reader needs to be able to ask
                # OpenRouter which provider served it.
                f"generation_id={data.get('id')!r})",
                request_id=request_id,
            )

"""Three-stage council protocol: respond -> peer rank (blind) -> chairman synthesis."""

from __future__ import annotations

import re
from dataclasses import dataclass

from council.client import CallResult, OpenRouterClient, OpenRouterError
from council.config import (
    CHAIRMAN_MODEL,
    MAX_TOKENS_STAGE_1,
    MAX_TOKENS_STAGE_2,
    MAX_TOKENS_STAGE_3,
    RANK_REGEX,
    USER_ID,
    VOTER_MODELS,
    stage2_prompt,
    stage3_prompt,
)

_RANK_PATTERN = re.compile(RANK_REGEX, re.IGNORECASE | re.DOTALL)

# User identifier, sent as the top-level `user` field of the OpenRouter body and read
# from there by Langfuse. Single-user CLI tool, so the identifier is static.
# (It used to say `metadata.langfuse_user_id` — that was the bug, not the design.)
#
# It lives in `config.py` because `scripts/langfuse_check.py` filters Langfuse BY this value:
# two hand-kept copies drifting apart would not silence that check, it would make it warn
# every week forever about an ingestion that is working fine.
_USER_ID = USER_ID


# OpenRouter Broadcast caps `session_id` at 128 characters (Langfuse drops over 200).
# Only session_id is capped: `user` is a 16-char literal below, and truncating a
# constant defends against a case that cannot happen.
_TRACE_FIELD_MAX = 128


def _build_metadata(session_id: str | None, stage: str) -> dict[str, object] | None:
    """Builds the OpenRouter Broadcast trace fields that Langfuse consumes.

    These are TOP-LEVEL fields of the request body — `user`, `session_id`, `trace` —
    not entries inside `metadata`.

    This is the fix to a two-month-old bug. From May 2026 the code sent
    `metadata: {langfuse_session_id, langfuse_user_id, langfuse_tags}`, and the traces
    arrived at Langfuse but were never grouped into a session. The project notes record
    "7 patterns tested, none consistent" — none of the seven was right, because they all
    varied the contents of `metadata`, which OpenRouter never reads for this purpose.
    The names and the position were both wrong.

    Source: OpenRouter docs, Broadcast — Settings > Observability.
    """
    if not session_id:
        return None
    return {
        "user": _USER_ID,
        "session_id": session_id[:_TRACE_FIELD_MAX],
        "trace": {
            "trace_id": session_id,
            "trace_name": "llm-council",
            "span_name": stage,
        },
    }


# Sentinel CallResult for failed voters: allows the council to degrade gracefully
# (e.g. 2/3 voters when one refuses) instead of aborting the entire run.
_FAILED_RESULT = CallResult(
    content="[VOTER_FAILED]",
    cost=0.0,
    tokens=0,
    latency_s=0.0,
    attempts=0,
    request_id=None,
)


@dataclass(frozen=True)
class StageResult:
    """A voter's Stage 1 contribution with model attribution. Failed voters have error set."""

    model: str
    result: CallResult
    error: str | None = None  # Populated if the voter failed (refusal, network, validation)


@dataclass(frozen=True)
class RankingResult:
    """A voter's Stage 2 ranking with regex-parsed structure."""

    voter: str
    result: CallResult
    rank: tuple[str, str, str] | None
    reason: str
    is_valid: bool
    error: str | None = None  # Populated if the voter failed at the API level


def stage1_responses(
    client: OpenRouterClient,
    question: str,
    session_id: str | None = None,
) -> list[StageResult]:
    """Each voter answers the question independently; per-voter failures degrade gracefully."""
    messages = [{"role": "user", "content": question}]
    metadata = _build_metadata(session_id, stage="stage_1")
    results: list[StageResult] = []
    for model in VOTER_MODELS:
        try:
            r = client.call(model, messages, MAX_TOKENS_STAGE_1, metadata=metadata)
            results.append(StageResult(model=model, result=r))
        except OpenRouterError as exc:
            results.append(StageResult(model=model, result=_FAILED_RESULT, error=str(exc)))
    return results


def stage2_rankings(
    client: OpenRouterClient,
    question: str,
    stage1: list[StageResult],
    session_id: str | None = None,
) -> list[RankingResult]:
    """Each voter ranks the anonymous responses; failures + malformed parses both flagged."""
    responses_text = [s.result.content for s in stage1]
    prompt = stage2_prompt(question, responses_text)
    messages = [{"role": "user", "content": prompt}]
    metadata = _build_metadata(session_id, stage="stage_2")

    rankings: list[RankingResult] = []
    for voter in VOTER_MODELS:
        try:
            result = client.call(voter, messages, MAX_TOKENS_STAGE_2, metadata=metadata)
        except OpenRouterError as exc:
            rankings.append(
                RankingResult(
                    voter=voter,
                    result=_FAILED_RESULT,
                    rank=None,
                    reason="",
                    is_valid=False,
                    error=str(exc),
                )
            )
            continue
        match = _RANK_PATTERN.search(result.content)
        if match:
            rank_tuple = (
                match.group(1).upper(),
                match.group(2).upper(),
                match.group(3).upper(),
            )
            # REASON is optional in regex; group(4) may be None if missing
            reason = (match.group(4) or "").strip()
            rankings.append(
                RankingResult(
                    voter=voter,
                    result=result,
                    rank=rank_tuple,
                    reason=reason,
                    is_valid=True,
                )
            )
        else:
            rankings.append(
                RankingResult(
                    voter=voter,
                    result=result,
                    rank=None,
                    reason="",
                    is_valid=False,
                    error="regex_no_match (Stage 2 output did not match RANK regex)",
                )
            )
    return rankings


def stage3_synthesis(
    client: OpenRouterClient,
    question: str,
    stage1: list[StageResult],
    stage2: list[RankingResult],
    session_id: str | None = None,
) -> CallResult:
    """External chairman synthesizes the final answer from responses + rankings."""
    responses_text = [s.result.content for s in stage1]
    rankings_text = [r.result.content for r in stage2]
    prompt = stage3_prompt(question, responses_text, rankings_text)
    messages = [{"role": "user", "content": prompt}]
    metadata = _build_metadata(session_id, stage="stage_3_chairman")
    return client.call(CHAIRMAN_MODEL, messages, MAX_TOKENS_STAGE_3, metadata=metadata)


# ---------------------------------------------------------------------------
# Verdetti sulla run.
#
# Vivevano in `__main__.py` fino al 2026-08-14. Sono stati spostati qui perche'
# `__main__.py` e' escluso dal gate di mutation — esclusione motivata finche' quel
# file era SOLO presentazione (mutmut riscrive ogni stringa in tre modi e i messaggi
# di report seppellivano il segnale del protocollo). Ma queste due funzioni non
# stampano: decidono l'exit code, cioe' il contratto che la run schedulata legge.
# Logica di decisione dentro un file non mutato e' esattamente il buco che il gate
# esiste per chiudere, quindi si sposta il codice invece di negoziare la soglia.
# ---------------------------------------------------------------------------


def _is_truncated(result: CallResult) -> bool:
    """`length` = the provider stopped at the ceiling, not at the end of the thought.

    OpenRouter documents this on every completion and the council used to throw it away.
    On 2026-08-14 all three voters came back `length` and two of them were reported [OK]:
    stage 2 ranked half-answers and the chairman synthesised them. Nothing was red.
    """
    return result.finish_reason == "length"


def _collect_failures(
    s1: list[StageResult], s2: list[RankingResult]
) -> tuple[
    list[tuple[str, str, str]],
    list[tuple[str, str, str]],
    list[tuple[str, str]],
    list[tuple[str, str]],
]:
    """Splits the run's problems into the four kinds that mean different things.

    FAILED means the call did not produce an answer; MALFORMED means it answered but
    the ranking could not be parsed; TRUNCATED means it answered and the answer stops
    mid-thought. Conflating them hides which one to act on — and truncation is the one
    that looks like success, so it is the one that needs its own name most.
    """
    stage1_failed = [(chr(65 + i), s.model, s.error or "") for i, s in enumerate(s1) if s.error]
    stage1_truncated = [
        (chr(65 + i), s.model) for i, s in enumerate(s1) if not s.error and _is_truncated(s.result)
    ]
    stage2_failed = [
        (chr(65 + i), r.voter, r.error or "")
        for i, r in enumerate(s2)
        if r.error and "regex_no_match" not in r.error
    ]
    stage2_malformed = [
        (chr(65 + i), r.voter)
        for i, r in enumerate(s2)
        if not r.is_valid and (r.error and "regex_no_match" in r.error)
    ]
    return stage1_failed, stage2_failed, stage2_malformed, stage1_truncated

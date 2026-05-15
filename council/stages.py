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
    VOTER_MODELS,
    stage2_prompt,
    stage3_prompt,
)

_RANK_PATTERN = re.compile(RANK_REGEX, re.IGNORECASE | re.DOTALL)

# User identifier propagated to Langfuse via the `metadata.langfuse_user_id` field
# in the OpenRouter request body. Single-user CLI tool, so the identifier is static.
_USER_ID = "marco-bellingeri"


def _build_metadata(session_id: str | None, stage: str) -> dict[str, object] | None:
    """Constructs the metadata payload for Langfuse session linkage via OpenRouter."""
    if not session_id:
        return None
    return {
        "langfuse_session_id": session_id,
        "langfuse_user_id": _USER_ID,
        "langfuse_tags": ["council", stage],
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

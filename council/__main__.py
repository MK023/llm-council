"""CLI entry point: `python -m council "your question"`."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

from council import __version__
from council.client import OpenRouterClient, OpenRouterError
from council.config import MAX_QUESTION_LENGTH, MAX_TOTAL_TOKENS_PER_RUN
from council.observability import TraceContext, emit, hash_question
from council.stages import stage1_responses, stage2_rankings, stage3_synthesis


def load_env(env_path: Path) -> None:
    """Loads KEY=VALUE pairs from a `.env` file into os.environ (does not overwrite existing)."""
    if not env_path.exists():
        return
    # Security: warn if .env is world/group readable
    mode = env_path.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        print(
            f"WARNING: {env_path} is readable by group/others (mode={oct(mode)[-3:]}). "
            "Run: chmod 600 .env",
            file=sys.stderr,
        )
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)  # maxsplit=1 preserves '=' in value
        os.environ.setdefault(key.strip(), value.strip())


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"LLM council multi-model verification (v{__version__})",
    )
    parser.add_argument("question", help="The question or decision to evaluate")
    parser.add_argument(
        "--env",
        type=Path,
        default=Path.cwd() / ".env",
        help="Path to .env file (default: ./.env in current directory)",
    )
    return parser.parse_args(argv)


def validate_question(question: str) -> str:
    """Sanitizes and length-caps the user question."""
    cleaned = question.strip()
    if not cleaned:
        raise ValueError("Question cannot be empty")
    if len(cleaned) > MAX_QUESTION_LENGTH:
        raise ValueError(f"Question exceeds {MAX_QUESTION_LENGTH} char cap")
    return cleaned


def _check_token_ceiling(running_total: int, trace: TraceContext) -> None:
    """Raises RuntimeError if cumulative tokens exceed per-run ceiling (defense against runaway)."""
    if running_total > MAX_TOTAL_TOKENS_PER_RUN:
        emit(
            "token_ceiling_exceeded",
            trace,
            running_total=running_total,
            ceiling=MAX_TOTAL_TOKENS_PER_RUN,
        )
        raise RuntimeError(
            f"Cumulative tokens {running_total} exceeded per-run ceiling "
            f"{MAX_TOTAL_TOKENS_PER_RUN} — aborting to protect spend cap"
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    try:
        question = validate_question(args.question)
    except ValueError as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2

    load_env(args.env)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print(
            f"ERROR: OPENROUTER_API_KEY not set (looked in {args.env} and environment)",
            file=sys.stderr,
        )
        return 2

    try:
        client = OpenRouterClient(api_key)
    except ValueError as exc:
        print(f"API KEY ERROR: {exc}", file=sys.stderr)
        return 2

    trace = TraceContext(question_hash=hash_question(question))
    emit("query_start", trace, question_length=len(question))
    running_tokens = 0

    print(f"QUESTION: {question}\n")
    print("=" * 72)
    print("STAGE 1 — independent responses (authors anonymized)")
    print("=" * 72)
    try:
        s1 = stage1_responses(client, question)
    except OpenRouterError as exc:
        emit("stage1_failed", trace, error=str(exc), request_id=exc.request_id)
        print(f"STAGE 1 FAILED: {exc}", file=sys.stderr)
        return 1
    for i, s in enumerate(s1):
        running_tokens += s.result.tokens
        emit(
            "stage1_response",
            trace,
            voter_label=chr(65 + i),
            model=s.model,
            cost=s.result.cost,
            tokens=s.result.tokens,
            latency_s=s.result.latency_s,
            attempts=s.result.attempts,
            request_id=s.result.request_id,
            error=s.error,
        )
        status = "FAILED" if s.error else "OK"
        print(f"\n--- Response {chr(65 + i)} [{status}] ({s.model}) ---")
        if s.error:
            print(f"ERROR: {s.error}")
        else:
            print(s.result.content)
        print(
            f"[tok={s.result.tokens} cost=${s.result.cost:.6f} "
            f"lat={s.result.latency_s}s attempts={s.result.attempts}"
            f"{f' req={s.result.request_id}' if s.result.request_id else ''}]"
        )

    try:
        _check_token_ceiling(running_tokens, trace)
    except RuntimeError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return 4

    print("\n" + "=" * 72)
    print("STAGE 2 — peer rankings (blind, regex-validated)")
    print("=" * 72)
    try:
        s2 = stage2_rankings(client, question, s1)
    except OpenRouterError as exc:
        emit("stage2_failed", trace, error=str(exc), request_id=exc.request_id)
        print(f"STAGE 2 FAILED: {exc}", file=sys.stderr)
        return 1
    for i, r in enumerate(s2):
        running_tokens += r.result.tokens
        if r.error and "regex_no_match" not in r.error:
            status = "FAILED"
        elif not r.is_valid:
            status = "MALFORMED"
        else:
            status = "OK"
        emit(
            "stage2_ranking",
            trace,
            voter_label=f"V{i + 1}",
            voter_model=r.voter,
            is_valid=r.is_valid,
            rank=list(r.rank) if r.rank else None,
            cost=r.result.cost,
            tokens=r.result.tokens,
            latency_s=r.result.latency_s,
            request_id=r.result.request_id,
            error=r.error,
        )
        print(f"\n--- Voter {i + 1} [{status}] ({r.voter}) ---")
        if r.error and "regex_no_match" not in r.error:
            print(f"ERROR: {r.error}")
        else:
            print(r.result.content)
            if r.is_valid and r.rank:
                reason_text = r.reason if r.reason else "(empty — accepted by relaxed regex)"
                print(f"PARSED RANK: {' > '.join(r.rank)}  |  REASON: {reason_text}")
        print(f"[tok={r.result.tokens} cost=${r.result.cost:.6f} lat={r.result.latency_s}s]")

    try:
        _check_token_ceiling(running_tokens, trace)
    except RuntimeError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return 4

    print("\n" + "=" * 72)
    print("STAGE 3 — chairman synthesis (external to voter pool)")
    print("=" * 72)
    try:
        s3 = stage3_synthesis(client, question, s1, s2)
    except OpenRouterError as exc:
        emit("stage3_failed", trace, error=str(exc), request_id=exc.request_id)
        print(f"STAGE 3 FAILED: {exc}", file=sys.stderr)
        return 1
    running_tokens += s3.tokens
    emit(
        "stage3_synthesis",
        trace,
        cost=s3.cost,
        tokens=s3.tokens,
        latency_s=s3.latency_s,
        attempts=s3.attempts,
        request_id=s3.request_id,
    )
    print(f"\n{s3.content}")
    print(f"\n[tok={s3.tokens} cost=${s3.cost:.6f} lat={s3.latency_s}s]")

    total_cost = sum(s.result.cost for s in s1) + sum(r.result.cost for r in s2) + s3.cost
    total_latency = (
        sum(s.result.latency_s for s in s1) + sum(r.result.latency_s for r in s2) + s3.latency_s
    )
    stage1_failed = [(chr(65 + i), s.model, s.error) for i, s in enumerate(s1) if s.error]
    stage2_failed = [
        (chr(65 + i), r.voter, r.error)
        for i, r in enumerate(s2)
        if r.error and "regex_no_match" not in r.error
    ]
    stage2_malformed = [
        (chr(65 + i), r.voter)
        for i, r in enumerate(s2)
        if not r.is_valid and (r.error and "regex_no_match" in r.error)
    ]

    emit(
        "query_complete",
        trace,
        total_cost=total_cost,
        total_tokens=running_tokens,
        total_latency_s=round(total_latency, 2),
        stage1_failed_count=len(stage1_failed),
        stage2_failed_count=len(stage2_failed),
        stage2_malformed_count=len(stage2_malformed),
    )

    if stage1_failed or stage2_failed or stage2_malformed:
        print("\n" + "=" * 72)
        print("ERROR SUMMARY — calibration hints for future runs")
        print("=" * 72)
        if stage1_failed:
            print(f"\nStage 1 failures ({len(stage1_failed)}):")
            for label, model, err in stage1_failed:
                print(f"  Voter {label} | {model}")
                print(f"    -> {err}")
        if stage2_failed:
            print(f"\nStage 2 API failures ({len(stage2_failed)}):")
            for label, voter, err in stage2_failed:
                print(f"  Voter {label} | {voter}")
                print(f"    -> {err}")
        if stage2_malformed:
            print(f"\nStage 2 malformed rankings ({len(stage2_malformed)}):")
            for label, voter in stage2_malformed:
                print(f"  Voter {label} | {voter}: output did not match RANK regex")
        print("\nCalibration hints:")
        print("  - Refusal errors: rephrase prompt, switch voter, or remove triggering content")
        print("  - HTTP errors (4xx/5xx): check OpenRouter status, model availability, quota")
        print("  - Malformed Stage 2: prompt strengthening needed, or model deviation pattern")

    print("\n" + "=" * 72)
    print(
        f"TOTAL: cost=${total_cost:.6f} tokens={running_tokens} "
        f"latency={total_latency:.2f}s | "
        f"s1_failed={len(stage1_failed)}/{len(s1)} "
        f"s2_failed={len(stage2_failed)}/{len(s2)} "
        f"s2_malformed={len(stage2_malformed)}/{len(s2)}"
    )
    print("=" * 72)
    return 0 if not (stage1_failed or stage2_failed or stage2_malformed) else 3


if __name__ == "__main__":
    sys.exit(main())

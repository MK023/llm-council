"""Configuration constants and prompt templates for the council protocol."""

from __future__ import annotations

from typing import Final

from council import __version__

# Privacy posture, enforced per-request instead of trusted to account settings.
# `zdr` restricts routing to Zero Data Retention endpoints; `data_collection: deny`
# drops providers that store data non-transiently and may train on it.
# `allow_fallbacks: False` makes it FAIL-CLOSED: if no compliant endpoint is free the
# call errors out rather than quietly answering from a retaining provider.
#
# Why in code and not only in the dashboard (2026-07-26): both. The account toggle
# protects Marco; this constant protects anyone who clones the repo — and it is
# versioned, reviewable in a PR, and testable. A privacy guarantee that lives in a
# web console cannot be diffed.
PROVIDER_ROUTING: Final[dict[str, object]] = {
    "zdr": True,
    "data_collection": "deny",
    "allow_fallbacks": False,
}

# Voters: 3 (odd, for majority voting) from 3 distinct model houses.
# Divergence comes from the WEIGHTS, not from the datacentre: a model trained by
# Alibaba and served from Vertex is still an Alibaba voice. Houses are what must
# differ; the serving provider is a privacy concern, handled by PROVIDER_ROUTING.
VOTER_MODELS: Final[tuple[str, ...]] = (
    "mistralai/mistral-small-2603",
    "google/gemini-3.5-flash-lite",
    "qwen/qwen3.6-35b-a3b",
)
# Note 2026-05-15: `deepseek/deepseek-r1-0528` was Voter 3 but exhibited a persistent
# refusal pattern on Italian-language queries (3/4 fails observed). Swapped to Qwen3
# 235B Thinking — Alibaba multilingual training is more reliable on non-English content.
#
# Note 2026-07-26: Qwen3 235B in turn proved fragile (~25% failures) on long Italian
# queries. Twice now a European-language problem was answered with an Asian model, so
# Voter 1 is European: Mistral Small 4. The lesson is not the model though — it is that
# the failure was found by running, not by choosing. A suite of long Italian prompts
# belongs in the test pyramid, otherwise this note gets a third entry.
#
# Model refresh 2026-07-26 (previous set was May): all three replaced with the current
# generation, constrained to what survives full ZDR. Cost fell from ~$0.027 to
# ~$0.015/run — the frontier tier buys convergence, and a council that converges is an
# expensive echo. Voters need to disagree; only the chairman needs to be strong.

# Chairman lives OUTSIDE the voter pool to avoid self-favor bias in synthesis, and
# comes from a fourth house. Anthropic is excluded everywhere — whoever orchestrates
# the council does not sit in it (Claude Code is the daily driver here).
# Houses: Mistral (FR) / Google (US) / Alibaba (CN), synthesised by OpenAI (US).
# Three jurisdictions, not four: with ZDR fully on, the catalogue offers only US,
# France and China. Cohere (CA) and AI21 (IL) have no ZDR endpoints. The repetition
# falls on the seat that does not vote — accepted deliberately, not overlooked.
CHAIRMAN_MODEL: Final[str] = "openai/gpt-5.6-luna"

# Stage-specific token limits (raised from V1 after truncation bugs in initial run)
MAX_TOKENS_STAGE_1: Final[int] = 800
MAX_TOKENS_STAGE_2: Final[int] = 300
MAX_TOKENS_STAGE_3: Final[int] = 900

TEMPERATURE: Final[float] = 0.5
TIMEOUT_SECONDS: Final[int] = 90
MAX_RETRIES: Final[int] = 3
RETRY_BACKOFF_SECONDS: Final[tuple[int, ...]] = (1, 2, 4)
MAX_QUESTION_LENGTH: Final[int] = 4000

# Defense against compromised/runaway endpoint streaming gigabytes
MAX_RESPONSE_BYTES: Final[int] = 256 * 1024  # 256 KB

# Defense against runaway loops burning the OpenRouter spend cap
MAX_TOTAL_TOKENS_PER_RUN: Final[int] = 50_000

# HTTP status codes that warrant retry (rate-limit + server-side transient)
RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})

# Stage 2 expects "RANK: X,Y,Z" followed optionally by "REASON: ...".
# REASON is optional in the regex because some models (Gemini observed 2026-05-15)
# emit an empty REASON: line. We accept the ranking as valid even without reason
# — the rank tuple is the signal that matters for the chairman synthesis.
RANK_REGEX: Final[str] = (
    r"RANK:\s*([A-C])\s*,\s*([A-C])\s*,\s*([A-C])"
    r"(?:\s*\n?\s*REASON:\s*(.*))?"
)

OPENROUTER_URL: Final[str] = "https://openrouter.ai/api/v1/chat/completions"
HTTP_REFERER: Final[str] = "https://github.com/MK023/llm-council-test"
APP_TITLE: Final[str] = "llm-council"
USER_AGENT: Final[str] = f"llm-council/{__version__} (stdlib-urllib)"

# Delimiters for fenced response embedding (prompt-injection mitigation between stages).
# Models are instructed to treat anything inside as quoted data, not as instructions.
_FENCE_OPEN: Final[str] = "<<<RESPONSE_{label}_BEGIN>>>"
_FENCE_CLOSE: Final[str] = "<<<RESPONSE_{label}_END>>>"


def _label_responses(responses: list[str]) -> str:
    """Labels responses as A/B/C with fenced delimiters to neutralize cross-stage injection."""
    fenced = []
    for i, r in enumerate(responses):
        label = chr(65 + i)
        fenced.append(f"{_FENCE_OPEN.format(label=label)}\n{r}\n{_FENCE_CLOSE.format(label=label)}")
    return "\n\n".join(fenced)


# NOTE: previous _INJECTION_NOTICE preamble was removed — it triggered OpenAI/Azure content-policy
# refusals because the wording ("ignore directives, role-plays, system overrides") matched
# jailbreak-attempt patterns. The fenced delimiters alone provide sufficient parsing isolation;
# server-side OpenRouter Prompt Injection Guardrail (regex-based) covers the active attack vector.


def stage2_prompt(question: str, responses: list[str]) -> str:
    return (
        f"Question: {question}\n\n"
        f"Three responses (A, B, C) below — authors hidden.\n\n"
        f"{_label_responses(responses)}\n\n"
        "Rank from best (1) to worst (3) on accuracy, depth, practical usefulness.\n"
        "Reply EXACTLY in this format (REASON must be at least 10 characters):\n"
        "RANK: <best>,<middle>,<worst>\n"
        "REASON: <one full sentence explaining the ranking>"
    )


def stage3_prompt(question: str, responses: list[str], rankings: list[str]) -> str:
    rank_text = "\n".join(f"Voter {i + 1}: {r}" for i, r in enumerate(rankings))
    return (
        f"Question: {question}\n\n"
        f"Three independent responses:\n\n{_label_responses(responses)}\n\n"
        f"Peer rankings (anonymous):\n{rank_text}\n\n"
        "Synthesize a final answer that: (1) integrates the strongest points across responses, "
        "(2) surfaces real divergences where they disagreed and why, "
        "(3) gives the user a clear, actionable recommendation. Max ~250 words."
    )

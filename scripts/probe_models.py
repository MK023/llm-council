"""Measures candidate models on the REAL Stage 1 prompt, then asks who served them.

WHY THIS EXISTS. This council has replaced a voter twice on a diagnosis that was
wrong (May 2026: "it refuses Italian"; July 2026: same theory, second model), and
picked a third on a short probe it passed before failing the real prompt. The lesson
was written in config.py and then not made repeatable, so it had to be relearned on
2026-08-14, when all three voters came back truncated at `finish_reason='length'`
and nobody noticed because truncated content is still content.

So the measurement is a script, in CI, against the real question at the real budget
with the real routing. A seat changes only after this has been run and read.

It runs in CI and not locally on purpose: the API key lives in the repository secret,
and the workstation sits behind a DMZ.

    python scripts/probe_models.py                  # the default candidate list
    python scripts/probe_models.py model/a model/b  # explicit candidates
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

from council.client import OpenRouterClient, OpenRouterError
from council.config import MAX_TOKENS_STAGE_1

# The E2E question: long, Italian, argumentative. Short English probes pass on models
# that then fail here — that is exactly how kimi-k3 got a seat it could not hold.
QUESTION = (
    "Valuta in modo argomentato questo compromesso di ingegneria del software: "
    "conviene introdurre un livello di astrazione in più per rendere un componente "
    "sostituibile, oppure accettare l'accoppiamento e rimandare l'astrazione al momento "
    "in cui serve davvero una seconda implementazione? Considera manutenibilità, costo "
    "cognitivo e rischio di astrazione prematura, e concludi con una raccomandazione pratica."
)

DEFAULT_CANDIDATES = (
    "qwen/qwen3-235b-a22b-2507",
    "meta-llama/llama-3.3-70b-instruct",
    "mistralai/mistral-large-2512",
    "openai/gpt-4.1-mini",
    "deepseek/deepseek-chat",
    "moonshotai/kimi-k2-0905",
)

_GENERATION_URL = "https://openrouter.ai/api/v1/generation?id="


def generation_stats(generation_id: str, api_key: str) -> dict[str, object]:
    """`GET /api/v1/generation` — the only place the reasoning spend is visible.

    A provider may bill reasoning tokens without returning `message.reasoning` in the
    body: on 2026-08-14 Novita served kimi-k2-0905 with 800 reasoning tokens and an
    empty `content`, and the client reported `reasoning=absent` because it could only
    see the body. `native_tokens_reasoning` is the field that tells the truth.
    """
    req = urllib.request.Request(
        _GENERATION_URL + generation_id, headers={"Authorization": f"Bearer {api_key}"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get("data", {})


def probe(models: tuple[str, ...], api_key: str) -> int:
    client = OpenRouterClient(api_key)
    messages = [{"role": "user", "content": QUESTION}]
    header = "{:38s} {:14s} {:8s} {:>7s} {:>7s} {:>10s}".format(
        "model", "provider", "finish", "compl", "reason", "cost $"
    )
    print(f"budget: max_tokens={MAX_TOKENS_STAGE_1}\n{header}\n{'-' * len(header)}")
    truncated = 0
    for model in models:
        try:
            result = client.call(model, messages, MAX_TOKENS_STAGE_1)
        except OpenRouterError as exc:
            print(f"{model:38s} RIFIUTATO/FALLITO: {str(exc)[:80]}")
            truncated += 1
            continue
        stats = generation_stats(result.generation_id, api_key) if result.generation_id else {}
        finish = str(stats.get("finish_reason"))
        if finish == "length":
            truncated += 1
        print(
            "{:38s} {:14s} {:8s} {:>7s} {:>7s} {:>10.6f}".format(
                model,
                str(stats.get("provider_name"))[:14],
                finish,
                str(stats.get("native_tokens_completion")),
                str(stats.get("native_tokens_reasoning")),
                float(stats.get("total_cost") or 0.0),
            )
        )
    # A seat is defensible when the model finishes on its own (`stop`) and spends
    # nothing on reasoning. `length` means the answer was cut: readable, and wrong.
    print(f"\ncandidati troncati o falliti: {truncated}/{len(models)}")
    return 0


def main(argv: list[str]) -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("OPENROUTER_API_KEY assente", file=sys.stderr)
        return 2
    return probe(tuple(argv) or DEFAULT_CANDIDATES, api_key)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

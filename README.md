# llm-council

[![CI](https://github.com/MK023/llm-council/actions/workflows/ci.yml/badge.svg)](https://github.com/MK023/llm-council/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) ![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue) ![stdlib only](https://img.shields.io/badge/dependencies-none-brightgreen)

Multi-model anti-sycophancy verification council using OpenRouter as gateway.  
3 independent voters → blind peer ranking → external chairman synthesis.

## Why this exists

Single-model LLM responses suffer from **sycophancy bias** (RLHF tends to optimize for agreement, not truth). Asking the same question to N different models from different providers, then having them anonymously rank each other and a fourth model synthesize, mitigates the bias — divergences between models surface where a single model would have rubber-stamped your assumption.

## Architecture

```
   user question
        │
        ▼
┌─── STAGE 1 (parallel logic, serial execution) ───┐
│  Voter 1: deepseek/deepseek-v4-flash             │
│  Voter 2: google/gemini-3.5-flash-lite           │
│  Voter 3: moonshotai/kimi-k2-0905                │
│  → 3 independent responses (anonymized A/B/C)    │
└───────────────────────────────────────────────────┘
        │
        ▼
┌─── STAGE 2 (blind peer ranking) ─────────────────┐
│  Each voter sees A/B/C with authors hidden       │
│  → "RANK: x,y,z" + reason (regex-validated)      │
└───────────────────────────────────────────────────┘
        │
        ▼
┌─── STAGE 3 (synthesis by external chairman) ─────┐
│  Chairman: openai/gpt-5.6-luna                   │
│  (different house, never a reasoning model)      │
│  → final answer + divergence analysis            │
└───────────────────────────────────────────────────┘
```

Chairman lives **outside** the voter pool to avoid self-favor bias in synthesis.

## Setup

1. Create an OpenRouter account at https://openrouter.ai
2. Generate an API key with **spend cap** + **time expiry** (security baseline)
3. Create `.env` in the project root:
   ```
   OPENROUTER_API_KEY=sk-or-v1-...
   ```
4. Ensure Python 3.10+ is available

## Usage

```bash
python -m council "Should I accept the offer from Company X?"
```

The full council flow runs (~30-60s end-to-end, ~$0.02 cost). Output goes to stdout, structured JSON observability logs go to stderr.

## Optional: Langfuse observability

If you have a Langfuse account (self-hosted via `langfuse-devops-lab` or cloud), set:

```
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

The script will emit Langfuse-compatible structured events on stderr (a forwarder or the OpenRouter native Langfuse plugin can consume them).

## Run tests

```bash
python -m unittest discover tests/          # 85 tests, no network
python -m coverage run -m unittest discover tests/ && python -m coverage report
```

## Test contract

Declared before the thresholds, so they can be defended rather than lowered.

| | |
|---|---|
| **Shape** | **Pyramid.** This is a single process with rich domain logic — the three-stage protocol, the ranking parser, the exit contract. Complexity lives *inside* the units, so the centre of gravity is unit tests. Not a trophy (no composed UI) and not a honeycomb (no service boundaries). |
| **Coverage floor** | **90% global**, blocking. **90% on critical modules** (`client.py`, `config.py`, `stages.py`) — they carry the privacy posture and the protocol. A floor, never a goal: quality is measured by mutation, not by percentage. |
| **Mutation** | Manual, on every PR touching `client/config/stages`. Each new test is verified by breaking the code and watching it go red. A test that cannot fail is not a test. |
| **Security taxonomy** | OWASP Top 10 for LLM Applications — mapped in [SECURITY.md](SECURITY.md). Minimum tests present: provider routing (ZDR fail-closed), telemetry carries no content, model output never executed. |
| **Flaky policy** | None quarantined today. When it happens: the test leaves the required checks, stays in the suite, and is tracked in `FLAKY.md` with id, owner and ticket. A quarantined test is debt, not a passing test. |

**Not covered, on purpose:** no HTTP-level integration test — the suite never touches the
network. A weekly scheduled E2E against the live API is the declared gap; until then, a
model that starts refusing is caught by running the council, not by CI.

## Pipeline level

**Level 1 → 3 (partial).** Lint, tests on three Python versions, coverage gates, CodeQL,
and workflow auditing (zizmor) all block the merge. Actions pinned to SHA, `persist-credentials: false`,
branch protection with required checks and no direct push to `main`.

Deliberately absent: SBOM and signed attestation. This project has **zero runtime
dependencies** and ships **no artifact** — an SBOM here would list nothing and a signature
would sign nothing. The supply chain that does exist is the pipeline itself, and that is
what the SHA pinning and zizmor defend. A motivated Level 1 is professional; a cargo-cult
Level 4 is theatre.

## Security hardening

- API key in `.env` (gitignored), validated at client init (format check `sk-or-`)
- Input length capped at 4000 chars
- JSON response schema validated on every call
- Stage 2 output regex-enforced; malformed responses flagged in output
- Exponential backoff retry on `URLError` (max 3 attempts: 1s, 2s, 4s)
- Hard timeout 90s per HTTP call
- TLS cert chain validated by default (`urllib`)
- API key never logged or surfaced in error messages

## Cost reference

Measured on a real run, 2026-07-26 (not estimated):

| Component | Cost per query |
|---|---|
| Stage 1 (3 voters) | ~$0.004 |
| Stage 2 (3 blind rankings) | ~$0.004 |
| Stage 3 (chairman, GPT-5.6 Luna) | ~$0.005 |
| **Total per query** | **~$0.013** |

With a $5 OpenRouter budget that is **~380 queries**. Latency ~56s end to end.

Cheaper than the May configuration (~$0.027) despite newer models: the frontier tier
buys convergence, and a council that converges is an expensive echo. Voters are chosen
to disagree and to *answer* — see the measurements in `config.py`.

## When to use the council vs Claude alone

Use the council for **high-stakes decisions** where single-model bias has real cost:
- Career decisions (accept offer / decline / negotiate)
- Interview brief framing
- Strategic technical choices with months+ horizon

Do **not** use the council for trivial coding or routine questions — the latency and cost are not justified, and consensus on simple questions adds no signal.

## Known limitations

### Langfuse session linkage (best-effort)

Each council run generates 7 HTTP calls (3 Stage 1 + 3 Stage 2 + 1 Stage 3 Chairman).
The client attempts to group them into a single Langfuse session by passing
`metadata.langfuse_session_id` in the OpenRouter request body (the documented
Langfuse SDK convention).

**However**: empirical testing on 2026-05-15 across 7 different propagation
patterns (body field variants `langfuse_session_id` / `session_id` / `sessionId`,
plus HTTP headers `X-Langfuse-Session-Id` / `langfuse-session-id`) showed
**inconsistent server-side mapping** by the OpenRouter → Langfuse plugin for
raw HTTP gateways. Session linkage is therefore **best-effort, not guaranteed**.

**Authoritative correlation channel**: the client-side observability module
(`council/observability.py`) emits a structured JSON line on **stderr** for every
API call, including a per-run `trace_id` that uniquely groups the 7 calls of
a single council run. Grep for `trace_id` in application logs to definitively
correlate calls regardless of Langfuse-side session mapping.

**Future direction**: when self-hosted Langfuse is operational (e.g. via
`langfuse-devops-lab`), the session linkage will be re-implemented as a direct
Langfuse SDK side-channel, bypassing the OpenRouter plugin mapping ambiguity.

## License

MIT — see [LICENSE](LICENSE).

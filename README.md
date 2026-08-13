# llm-council

[![CI](https://github.com/MK023/llm-council/actions/workflows/ci.yml/badge.svg)](https://github.com/MK023/llm-council/actions/workflows/ci.yml) [![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=MK023_llm-council&metric=alert_status)](https://sonarcloud.io/summary/overall?id=MK023_llm-council) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) ![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue) ![stdlib only](https://img.shields.io/badge/dependencies-none-brightgreen)

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

The full council flow runs (~60s end-to-end, ~$0.013 cost). Output goes to stdout, structured JSON observability logs go to stderr.

## Optional: Langfuse observability

If you have a Langfuse account (self-hosted via `langfuse-devops-lab` or cloud), set:

```
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

The stderr JSON is for local inspection. Traces reach Langfuse through **OpenRouter Broadcast** (Settings > Observability): no code, no dependency. The client sends the `user` / `session_id` / `trace` fields Broadcast reads.

## Run tests

```bash
python -m unittest discover tests/          # no network, ever
python -m coverage run -m unittest discover tests/ && python -m coverage report
```

The suite carries no test count in prose. It was written down three times — here, in
the project notes and in the case study on marcobellingeri.dev — and all three said a
different, stale number. A count is a fact with a shelf life; the gate that enforces
it is not.

## Test contract

Declared before the thresholds, so they can be defended rather than lowered.

| | |
|---|---|
| **Shape** | **Pyramid.** This is a single process with rich domain logic — the three-stage protocol, the ranking parser, the exit contract. Complexity lives *inside* the units, so the centre of gravity is unit tests. Not a trophy (no composed UI) and not a honeycomb (no service boundaries). |
| **Coverage floor** | **100%**, lines and branches, blocking. Not a number chased for its own sake: the 17 lines missing at 94% were real untested behaviour — stage 2 total failure, the *second* token-ceiling check, and the fenced-delimiter defence that SECURITY.md claims for LLM01. On ~400 statements with no unreachable branches, 100% is defensible; on a large codebase the rule would go back to *clean as you code*. Still a floor: coverage says which lines run, not whether the assertions are worth anything. |
| **Mutation** | **Automated and blocking, weekly** — `.github/workflows/mutation.yml`, floor `MUTATION_FLOOR = 80`, declared in one place. Measured 2026-08-13: **463 mutants killed out of 568, 81.5%**, with coverage sitting at 100%. It opened the same day at 55.5% — 100% coverage and 253 survivors, which is the whole argument for this gate: every line ran, and almost half of them could change without a single assertion noticing. The 148 mutants killed since died to assertions on produced values (the HTTP request's headers and body, the retry backoff, the log record's fields, each stage's token budget and span name), not to any change in `council/`. Mutates `council/` minus `__main__.py`: measured over everything the score was 47.5% and ~70% of the survivors were string rewrites of report text in the printing layer, which no sensible assertion would catch. Never on the PR path — a slow gate in the PR loop is a gate people learn to ignore. Manual mutation stays the habit on every PR touching `client/config/stages`; the weekly run is the net, not the practice. |
| **Security taxonomy** | OWASP Top 10 for LLM Applications **2025** — mapped in [SECURITY.md](SECURITY.md), with MITRE ATLAS techniques alongside. The mapping is itself tested (`tests/test_security_doc.py`): every category needs an explicit verdict and every cited test must exist. Minimum tests present: provider routing (ZDR fail-closed), telemetry carries no content, model output never executed. |
| **Flaky policy** | None quarantined today. When it happens: the test leaves the required checks, stays in the suite, and is tracked in `FLAKY.md` with id, owner and ticket. A quarantined test is debt, not a passing test. |

**Live E2E, weekly.** `.github/workflows/e2e.yml` runs one real council on a schedule and
fails if the exit code is not 0 — including **3**, the degraded run. That is the whole point:
a voter that starts refusing produces a usable answer and a quiet 3, which is how a broken
voter stayed hidden for two months. Costs ~$0.013 per run. Never triggered by `pull_request`:
the repo is public and secrets must not reach a fork's workflow.

The unit suite still never touches the network — this is the one exception, and it lives on
a schedule instead of in the PR loop so it can never slow down the development cycle.

**And a watcher on the watcher.** A red run on Actions says the sentinel *failed*. Nothing
says the sentinel never *ran* — and GitHub disables scheduled workflows after 60 days of
repository inactivity, silently. The job posts a check-in to a Sentry cron monitor
(`llm-council-e2e`), the kind of alarm that fires on the **absence** of a signal. The
check-in never fails the build: a guard that kills what it guards is worse than no guard.

**Honest limit, verified 2026-08-13:** that monitor exists with the right schedule and
receives its check-ins, but it is **not alerting**. Sentry includes exactly one cron monitor
per plan and the seat is taken by another project; monitors past the quota are registered and
left inactive until reserved quota or a pay-as-you-go budget activates them. So the missed-run
case — the one this is for — is currently *instrumented but not alarmed*. Written down rather
than quietly implied, because a monitor everyone believes in and that never fires is worse than
no monitor at all.

## Pipeline level

**Level 1 → 3 (partial).** Lint, tests on three Python versions, coverage gates, CodeQL,
secret scanning, dependency review and workflow auditing (zizmor) all block the merge.
Actions pinned to SHA, `permissions: {}` at workflow level with each grant written per job,
`persist-credentials: false`, branch protection with required checks and no direct push to `main`.

What each gate blocks on, because a gate without a written policy is a future
`continue-on-error`:

| Gate | Blocks on | Notes |
|---|---|---|
| **Secret scan** (gitleaks) | any finding — zero tolerance | one allowlisted string, the Sonar project key, which is public by construction |
| **Tests** (3.10/3.11/3.12) | any failure | includes the stdlib-only invariant, see below |
| **Coverage** | below 100% lines+branches; below 90% on `client`/`config`/`stages` | a floor, not a quality claim |
| **Mutation score** | below 80% — **weekly, off the PR path** | the claim the coverage number cannot make |
| **Dependency review** | a vulnerable dependency entering the diff | nothing to review today, which is the point |
| **SonarQube Cloud** | quality gate red | zero suppressed rules |
| **Workflow lint** (zizmor) | any finding | it is what keeps the SHA pins pinned |

### The supply chain that actually exists

There are **zero runtime dependencies**, so `pip install llm-council` pulls nothing. That
promise is enforced by `tests/test_packaging.py`, which reads `pyproject.toml` — the manifest
that actually declares dependencies. Until 2026-08-13 the guard was `test ! -f requirements.txt`
in CI: a file this project would never create, so adding `dependencies = ["requests"]` passed
every check. Dependency review is the second net behind it.

The dev tools are a different story and are treated like one. CI pulls ruff, coverage and
zizmor from PyPI on every run, and mutmut and pytest once a week. They are pinned **by hash**,
not by version — a version pin still trusts the registry to serve the same bytes under that
name. The hashes are generated from the PyPI API by `scripts/pin_dev_deps.py`, never typed.

That pin has a maintenance cost, and it is declared rather than discovered later. Dependabot
watches the Actions and **not** those two files: it knows how to raise a version and not how
to regenerate a hash, so each bump PR would fail on a mismatch whose message names the symptom
instead of the missing step. Bumps here are manual, through the script. What that buys is a
registry that cannot serve different bytes under the same version; what it costs is that a CVE
in ruff, coverage, zizmor or mutmut waits until someone looks. Those are build-time tools on a
repo with no runtime dependencies and nothing published — the exposure is a linter on a runner,
not a chain reaching a user. It is the right trade only while both halves stay true.

Still deliberately absent: **SBOM and signed attestation**. Nothing is published and no
artifact is distributed — an SBOM would list the empty set and a signature would sign it.
A motivated Level 1 is professional; a cargo-cult Level 4 is theatre.

Also absent: **OIDC**. There is no cloud to authenticate to. The three secrets this repo holds
(`OPENROUTER_API_KEY`, `SONAR_TOKEN`, `SENTRY_CRON_CHECKIN_URL`) are third-party credentials
with no federation available.

## Security hardening

- API key in `.env` (gitignored), validated at client init (format check `sk-or-`)
- Input length capped at 4000 chars
- JSON response schema validated on every call
- Stage 2 output regex-enforced; malformed responses flagged in output
- Exponential backoff retry on transient failures only — `429`, `5xx`, `URLError`, malformed JSON (max 3 attempts: 1s, 2s, 4s). A `4xx` fails fast: retrying a bad request wastes quota and hides the bug
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

### Langfuse session linkage — solved 2026-07-26

Each council run makes 7 HTTP calls (3 Stage 1 + 3 Stage 2 + 1 chairman), and until
July they arrived at Langfuse ungrouped. The README used to describe this as
"best-effort, not guaranteed" after testing 7 propagation patterns in May.

**It was not best-effort — it was the wrong field.** All seven variants put the value
inside `metadata`, and OpenRouter never reads `metadata` for session grouping. The
documented Broadcast fields are **top-level** in the request body:

```json
{ "user": "...", "session_id": "...", "trace": { "trace_id", "trace_name", "span_name" } }
```

Sessions now group correctly. The lesson outlived the bug: seven experiments that all
vary the same wrong dimension look like thorough investigation and are not.

### What is still not covered

- **No self-hosted Langfuse ingestion.** Traces reach Langfuse through OpenRouter
  Broadcast, which requires no code and no dependency — the right trade for a
  stdlib-only project. A direct SDK integration would mean adding a dependency to
  gain features this tool does not use.
- **Telemetry carries no content, by design.** The stderr JSON and the Broadcast
  fields carry identifiers, costs and timings — never the question or the answers.
  That is a deliberate limit, enforced by `tests/test_stages.py::TestTelemetryPrivacy`.

## License

MIT — see [LICENSE](LICENSE).

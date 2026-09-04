# llm-council

[![CI](https://github.com/MK023/llm-council/actions/workflows/ci.yml/badge.svg)](https://github.com/MK023/llm-council/actions/workflows/ci.yml) [![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=MK023_llm-council&metric=alert_status)](https://sonarcloud.io/summary/overall?id=MK023_llm-council) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) ![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue) ![stdlib only](https://img.shields.io/badge/dependencies-none-brightgreen)

Multi-model anti-sycophancy verification council using OpenRouter as gateway.  
3 independent voters → blind peer ranking → external chairman synthesis.

What changed and why is in [CHANGELOG.md](CHANGELOG.md); the entries carry the reasoning,
not just the diff.

## Why this exists

Single-model LLM responses suffer from **sycophancy bias** (RLHF tends to optimize for agreement, not truth). Asking the same question to N different models from different providers, then having them anonymously rank each other and a fourth model synthesize, mitigates the bias — divergences between models surface where a single model would have rubber-stamped your assumption.

## Architecture

```
   user question
        │
        ▼
┌─── STAGE 1 (parallel logic, serial execution) ───┐
│  Voter 1: mistralai/mistral-small-3.2 (EU)       │
│  Voter 2: meta-llama/llama-3.3-70b    (US)       │
│  Voter 3: deepseek/deepseek-chat      (CN)       │
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
│  Chairman: openai/gpt-4.1-mini                   │
│  (different house, never a reasoning model)      │
│  → final answer + divergence analysis            │
└───────────────────────────────────────────────────┘
```

Chairman lives **outside** the voter pool to avoid self-favor bias in synthesis.

**The seats were rebuilt on 2026-08-14, on a measurement.** An E2E run came back with all
three voters at `finish_reason='length'`; two of them delivered a truncated answer that the
council reported as `[OK]`, because truncated content is still content and every check here
is about shape. The catalogue then said what nobody had read — `GET /api/v1/models` carries
a `reasoning` object, *"Omitted for non-reasoning models"* — and **three of the four seats
were reasoning models**, one of them (`gemini-3.5-flash-lite`) with reasoning `mandatory`,
including the chair the project's own rule forbids. The rule had been written in `config.py`
from the start; nothing checked it.

The replacements come from `scripts/probe_models.py`: the real Italian prompt, full ZDR
routing, `max_tokens=1200`, and the bar is `finish_reason: stop` with **zero** reasoning
tokens. Europe came back in the process — the July note had Mistral out on rate limits, and
a *smaller* Mistral answers, complies with ZDR, and is the cheapest of the three. The council
now spans EU/US/CN instead of two Chinese houses out of three, and stage 1 costs **less** than
before (~$0.0014 against ~$0.004) while producing longer answers.

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

The full council flow runs (~90s end to end, ~$0.005). Output goes to stdout, structured JSON observability logs go to stderr.

## Langfuse observability — there is nothing to configure here

**No environment variable in this project turns tracing on or off**, and the three that
looked like they did were removed on 2026-08-31.

Traces reach Langfuse through **OpenRouter Broadcast** (OpenRouter → Settings →
Observability): no code, no dependency, no key. The client sends the `user`, `session_id`
and `trace` fields Broadcast reads, and OpenRouter forwards them. The stderr JSON is a
separate thing — local inspection, and the only telemetry this code produces itself.

**What this means for anyone cloning the repo:** the council runs and traces nothing,
unless you switch Broadcast on in your own OpenRouter account. That is the whole
integration.

Two corrections to what this section used to say, both wrong for months:

- It told you to set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`. Setting them
  flipped one boolean in a local log line and changed nothing else. `LANGFUSE_HOST` was
  read by no line of code at all.
- It described the destination as "self-hosted via `langfuse-devops-lab`". That project
  uses **Langfuse Cloud, EU region**. Nothing here is self-hosted.

The cost of routing ingestion through an account setting is that **no unit test can prove
it works** — none of them can reach a checkbox in someone's dashboard. It was verified by
hand for the first time on 2026-08-31, and it does work. That it took until then to look is
the point, so it is no longer done by hand: the weekly E2E now runs
`scripts/langfuse_check.py`, which asks Langfuse whether the run that just happened actually
arrived, and prints the spend of the last 30 days beside it.

**It warns and never fails.** A guard that kills what it guards is worse than no guard, so
the council's own verdict always belongs to the step before. And it does not alarm on the
run it just watched — Langfuse documents up to fifteen minutes of ingestion delay for
third-party exporters, and OpenRouter Broadcast is one, so shouting "data loss" a minute
later would be shouting at a documented delay. The alarm sits on a question the
delay cannot touch: **has a single complete council run reached Langfuse in the last eight
days?** Eight, not seven, because GitHub's scheduler slipped by nearly seven hours on
2026-08-31 and a window equal to the schedule would fire on the wrong system.

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
| **Mutation** | **Automated and blocking, weekly** — `.github/workflows/mutation.yml`, floor `MUTATION_FLOOR = 85`, declared in one place. Measured 2026-08-13: **488 mutants killed out of 568, 85.9%**, with coverage sitting at 100%. It opened the same day at 55.5% — 100% coverage and 253 survivors, which is the whole argument for this gate: every line ran, and almost half of them could change without a single assertion noticing. Every mutant killed since died to an assertion on a produced value (the HTTP request's headers and body, the retry backoff, the log record's fields, each stage's token budget, span name and voter attribution), not to any change in `council/`. `stages.py` is down to **1 survivor out of 138**. What remains is the wording of diagnostic strings and four **equivalent** mutants in `observability.py` — tabled in `tests/test_observability.py`, unkillable by construction. Going higher would mean asserting the exact text of error messages: a gate on prose, not on behaviour. Mutates `council/` minus `__main__.py`: measured over everything the score was 47.5% and ~70% of the survivors were string rewrites of report text in the printing layer, which no sensible assertion would catch. Never on the PR path — a slow gate in the PR loop is a gate people learn to ignore. Manual mutation stays the habit on every PR touching `client/config/stages`; the weekly run is the net, not the practice. |
| **Security taxonomy** | OWASP Top 10 for LLM Applications **2025** — mapped in [SECURITY.md](SECURITY.md), with MITRE ATLAS techniques alongside. The mapping is itself tested (`tests/test_security_doc.py`): every category needs an explicit verdict and every cited test must exist. Minimum tests present: provider routing (ZDR fail-closed), telemetry carries no content, model output never executed. |
| **Flaky policy** | None quarantined today. When it happens: the test leaves the required checks, stays in the suite, and is tracked in `FLAKY.md` with id, owner and ticket. A quarantined test is debt, not a passing test. |

**Live E2E, weekly.** `.github/workflows/e2e.yml` runs one real council on a schedule and
fails if the exit code is not 0 — including **3**, the degraded run. That is the whole point:
a voter that starts refusing produces a usable answer and a quiet 3, which is how a broken
voter stayed hidden for two months. Costs ~$0.005 per run. Never triggered by `pull_request`:
the repo is public and secrets must not reach a fork's workflow.

The unit suite still never touches the network — this is the one exception, and it lives on
a schedule instead of in the PR loop so it can never slow down the development cycle.

**And a watcher on the watcher.** A red run on Actions says the sentinel *failed*. Nothing
*in this repo* says the sentinel never *ran* (something outside it does — see below) — and
GitHub disables scheduled workflows after 60 days of repository inactivity, silently. The job posts a check-in to a Sentry cron monitor
(`llm-council-e2e`), the kind of alarm that fires on the **absence** of a signal. The
check-in never fails the build: a guard that kills what it guards is worse than no guard.

**Honest limit, re-measured 2026-08-31 — and it is worse than this file used to say.** The
monitor exists with the right schedule and is **`disabled`**. It has never recorded a single
check-in. Sentry includes one cron monitor per plan and the seat is held by another project;
monitors past the quota are registered and left inactive.

The correction matters because the previous wording — *"receives its check-ins, but it is not
alerting"* — described something that does not happen. Queried directly: `status: disabled`,
*"No check-ins found"*, and `ok=0 error=0 missed=0` for the hour of 2026-08-31 in which the
E2E ran and its own log printed `check-in Sentry: error`. **The check-in is sent and thrown
away.** Not instrumented-but-silent: not instrumented.

**The missed-run case is guarded anyway — from outside this repo.** Since 2026-08-14,
`scripts/sentinella-cron.mjs` in the site repo (workflow `sentinella-cron.yml`, daily) asks
the GitHub API when each schedule last fired and, for whichever one has gone quiet, raises a
Sentry event, fails its own run, and opens a GitHub issue — in the site repo, since the token
is scoped there. `llm-council-e2e` is one of its entries, with a 10-day limit against a weekly
schedule. It lives there because that repo commits on 13 days out of the last 31, never close
to GitHub's 60-day cutoff, while this one goes in sprints: a guard that dies of the disease it
watches for is no guard. That guard is in turn watched, by reciprocal coverage inside the site
repo. The dead check-in above is a **second** net, kept because it costs nothing and works by
itself the day the seat frees up.

Its honest limit, so this paragraph does not repeat the mistake it corrects: that guard is
fail-open by construction. A GitHub API error for one entry is skipped with a warning and no
alarm, and without a DSN the Sentry call is a no-op. Its alarm path has never fired in
production — only in the script's own offline self-check.

**What nobody guards is the other case: started and *failed*.** Only the red run on Actions
says that, and only to whoever looks — which is why the E2E sat red for a week after
2026-08-24 unnoticed. The boundary is written down instead of quietly implied, because a
monitor everyone believes in and that never fires is worse than no monitor at all.

## Pipeline level

**Level 1 → 3 (partial).** Actions pinned to SHA, `permissions: {}` at workflow level with
each grant written per job, `persist-credentials: false`, and a ruleset on `main` that forbids
direct push, force-push and deletion, requires linear history, and accepts **squash only** as a
merge method — the history is squashed in practice, so any other method was a door nobody used
and everybody trusted. Required checks are **strict**: a branch behind `main` cannot merge until
it is updated, because a green run against a stale base says nothing about the merge result.
No bypass actors, admins included.

Twelve checks are **required** by that ruleset, which is the difference between a gate that
runs and a gate that blocks:

```
Lint (ruff) · Tests (Python 3.10 / 3.11 / 3.12 / 3.13 / 3.14) · Coverage
CodeQL · SonarQube Cloud · Workflow lint (zizmor)
Secret scan (gitleaks) · Dependency review
```

The last two versions arrived on **2026-09-04** with the matrix that produces them. A required
check is named, not matched by pattern, so widening the matrix without editing the ruleset
would have added two jobs that run and do not block — the exact failure this section describes
two paragraphs down. The ruleset was edited in the same change.

The last two were **added to the required list on 2026-08-14**, and until then this section
claimed they blocked the merge while they only ran: they arrived with the supply-chain work of
2026-08-13 and nobody added them to a ruleset last edited in July. A check that runs and does
not block is a check whose red is a matter of opinion. Found by reading the ruleset through the
API instead of trusting this paragraph — which is the only way that class of drift ever surfaces.

The same reading on **2026-08-19** produced the rest of what is described above: linear history,
squash-only merges, strict checks and the CodeQL alert threshold were absent, and `main` was the
least constrained branch across every repository that shares this pipeline. The required check
named `CodeQL` only ever asserted that the job finished — the `code_scanning` rule is what makes
its findings block. Twenty branches already merged were still on the remote, and
`delete_branch_on_merge` was off, which is why they accumulated.

What each gate blocks on, because a gate without a written policy is a future
`continue-on-error`:

| Gate | Blocks on | Notes |
|---|---|---|
| **Secret scan** (gitleaks) | any finding — zero tolerance | one allowlisted string, the Sonar project key, which is public by construction |
| **Tests** (3.10 → 3.14) | any failure | includes the stdlib-only invariant, see below |
| **Coverage** | below 100% lines+branches; below 90% on `client`/`config`/`stages` | a floor, not a quality claim |
| **Mutation score** | below 85% — **weekly, off the PR path** | the claim the coverage number cannot make |
| **Dependency review** | a vulnerable dependency entering the diff | nothing to review today, which is the point |
| **SonarQube Cloud** | quality gate red | zero suppressed rules |
| **Workflow lint** (zizmor) | any finding | it is what keeps the SHA pins pinned |
| **Code scanning** (CodeQL) | any error-level alert, or a security alert rated high or above | a ruleset rule, not just a required check: it reads the alerts, not the job's exit code |

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

Measured on a real run, 2026-08-14 (not estimated) — the per-stage figures come from the
telemetry of that run, not from a price list:

| Component | Cost per query |
|---|---|
| Stage 1 (3 voters) | ~$0.0016 |
| Stage 2 (3 blind rankings) | ~$0.0017 |
| Stage 3 (chairman, GPT-4.1 mini) | ~$0.0019 |
| **Total per query** | **~$0.0052** |

14.5k tokens, ~86s end to end. With a $5 OpenRouter budget that is **~960 queries**.

Down from ~$0.013 in July and ~$0.027 in May, while the answers got *longer*. Nothing was
optimised for price: the seats were rebuilt to exclude reasoning models, and the models
that answer without burning their budget on internal thought happen to be the cheap ones.
The frontier tier buys convergence, and a council that converges is an expensive echo —
voters are chosen to disagree and to *answer*. The measurements are in `config.py`.

Latency is the one number that got worse. A single run has been observed at 320s with a slow
provider, against the usual 86 to 95. The 90s per-call timeout and the retry budget are what
bound the worst case; there is no other limit on it.

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

### Truncation is not success — added 2026-08-14

`finish_reason` is now read on **every** successful call and carried out on `CallResult`.
A voter whose answer was cut at the ceiling is labelled `[TRUNCATED]` instead of `[OK]`,
counted in the TOTAL line as `s1_truncated=n/3`, listed in the ERROR SUMMARY with what to
change, and — the part that matters — it **degrades the run to exit 3**, so the weekly E2E
goes red.

That last point is the whole design. A cut answer is present, valid and incomplete: it
passes every check here, because every check here is about shape. On 2026-08-14 all three
voters came back `length`, two of them were reported `[OK]`, stage 2 ranked half-answers
and the chairman synthesised them. Nothing was red, and nothing had been red for months.

Models drift — they get more verbose, a provider changes how it serves them, a prompt gets
longer. The exit code is the contract the scheduled run reads, so the drift has to reach it.
A truncated **chairman** degrades the run too: that one is the final answer stopping
mid-sentence.

`stop` stays silent. A label that appears on every run is a label nobody reads.

### A voter answered, and the answer is degraded

The failure this protocol handles well is the **empty** one — see the reasoning-model
diagnosis above. The one it does not handle is an answer that arrives complete,
well-formed, validated, and subtly wrong: a mangled token in the middle of a sentence, a
thought that stops making sense. It passes every check, because every check is about
shape. Observed on the live E2E of 2026-08-14, where one voter wrote `rimforKeyare` in
place of an Italian verb.

**What the documentation says, and what it does not.** OpenRouter's provider routing page
carries exactly one sentence on the subject — *"Quantized models may exhibit degraded
performance for certain prompts, depending on the method used"* — and no page describes
corrupted tokens as a phenomenon or prescribes a response. So attributing any particular
garbled reply to quantisation is a **hypothesis, not a documented fact**, and this README
will not pretend otherwise.

**What you can do about it.** Ask who served the answer. The OpenAPI spec marks `id`
required on `ChatResult`, and `GET /api/v1/generation?id=<id>` returns `data.provider_name`
— the provider that actually answered, alongside `finish_reason`, `native_finish_reason`,
`latency` and `is_byok`. The council prints that id on every response line as `gen=…`:

```bash
curl -H "Authorization: Bearer $OPENROUTER_API_KEY" \
     "https://openrouter.ai/api/v1/generation?id=gen-3bhGkxlo4XFrqiabUM7NDtwDzWwG"
```

It is printed next to answers that look **fine**, on purpose. A degraded reply never
reaches an error message, so an id that only appeared in errors would be missing from the
single case that needs it.

**A field that was there and empty.** `request_id` comes from an HTTP header, and on that
same live run OpenRouter sent neither `x-request-id` nor `openrouter-request-id`: null on
7 telemetry records out of 7. The unit suite was green throughout, because it mocks headers
the real API does not send. It is kept — the response-size cap trips before the body is
parsed, and there the header is the only identifier that exists — but it is not the handle
to reach for.

**`provider.quantizations` — measured, and deliberately not set.** The catalogue exposes
`quantization` per endpoint (`GET /models/{author}/{slug}/endpoints`), and reading it settled
the question:

| model | endpoints |
|---|---|
| `gpt-4.1-mini` (chairman) | OpenAI `unknown` · Azure `unknown` · Azure `unknown` |
| `deepseek-chat` | StreamLake `unknown` · DeepInfra **`fp4`** · Novita `fp8` |
| `llama-3.3-70b` | 13 endpoints: `fp8`, `bf16`, `fp16`, 5× `unknown` |
| `mistral-small-3.2-24b` | DeepInfra `fp8` · Parasail `bf16` · Venice `fp8` |

The field is an **allowlist**, and **every** endpoint of the chairman declares `unknown`. Any
allowlist leaves the chair with zero compliant endpoints — and under `allow_fallbacks: false`
that is not a downgrade, it is a run with no final answer. The setting that promises better
quality would deterministically produce none.

The residual risk is named rather than silenced: `deepseek-chat` can land on DeepInfra at
**`fp4`**, the most aggressive level here, on an endpoint sitting at 81% uptime. Routing does
not hide it — `finish_reason` now degrades a truncated run and `generation_id` resolves
`provider_name` after the fact. Pin a provider only if it actually bites.

Run it yourself: `probe.yml` with `endpoints_only`, which costs nothing — it reads the
catalogue and makes no completion call.

## License

MIT — see [LICENSE](LICENSE).

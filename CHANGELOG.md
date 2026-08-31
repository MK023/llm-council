# Changelog

All notable changes to this project are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed — a rate limit is not a hiccup

- **The same seat fell the same way twice, and the retry loop waited three seconds.** On
  2026-08-24 and again on 2026-08-31 the weekly E2E lost
  `mistralai/mistral-small-3.2-24b-instruct` to HTTP 429 in Stage 2, after three attempts
  spanning **three** seconds — the loop sleeps only *between* attempts, so with
  `MAX_RETRIES=3` it spends `1s` and `2s` and never reaches the `4` in the tuple. A
  rate-limit window does not reopen in three seconds. OpenRouter documents the answer and
  the client was throwing it away: *"Respect the `Retry-After` header before retrying"*, on
  **429 and 503** both. The client read the error body and never the headers.
  The hint is now obeyed where OpenRouter documents sending one, **capped at 30s** — the
  number is chosen by a provider we reach without fallbacks, and an hour-long hint would
  park a run for an hour. A 429 with no hint waits a 20s fallback; 503 without a hint keeps
  the short backoff, because the long wait is a guess about a *window* and only a rate limit
  has one. An unparsable or negative value counts as absent: `time.sleep(-1)` raises, and
  that exception would escape the loop and end the run.
- **A 429 in the body waited 3s while the same 429 in the status line waited 40.** The same
  class PR #36 closed for 502 was left open for 429 the day after. Both channels now reach
  the rate-limit wait.
- **`e2e.yml` had no `timeout-minutes` at all**, so it inherited GitHub's six-hour default.
  The new waits are per-attempt: a provider answering 429 with a high hint stretches a run
  from ~3 to ~7 minutes, and nothing in the code stopped it. The job is now capped at 15
  minutes. A wait budget counted inside the client would have been a second accounting
  destined to drift from the first; a wall clock bounds every way of hanging, including the
  ones not yet imagined.

### Fixed

- **A retryable code delivered in the body was never retried.** On 2026-08-31 the weekly
  E2E lost its chairman — and with it the whole run — to
  `{"error": {"message": "Internal server error", "code": 502}}`, returned with **HTTP 200**.
  `attempts=1`. `502` has been in `RETRYABLE_STATUS_CODES` since #28, but that set was only
  ever read from `except HTTPError`, and a 200 never raises one. The intent and the mechanism
  had disagreed from the start, and the seam was invisible because both halves looked right
  in isolation: a documented retry list, and a comment declaring body errors semantic.
  `_validate_response` now carries the body's `code` onto `OpenRouterError`, and `call`
  routes it through the same set. Errors with no code — a refusal, a truncation, a malformed
  schema, the size cap — stay status-less and still fail fast, because **a fault we cannot
  name is not a fault we can time**. Three voters survive a hiccup; a chairman did not.
- **The retries were about to cost the diagnosis.** The first draft of the fix above raised
  `All 3 attempts failed … OpenRouterError HTTP 502` and dropped both the upstream provider's
  own words and the `request_id` — the two fields you take to OpenRouter to ask what happened.
  A persistent 502 would have become *less* diagnosable than a plain 400. Both are carried
  through now, and the body error is capped at 500 characters like the HTTPError branch beside
  it, since that text is attacker-influenced and now travels one hop further than it did.

### Testing

- **Three assertions that a green suite did not miss because it was wrong, but because it was
  not looking.** All of `client.py` was at 100% line-and-branch coverage over the fix above,
  and three mutants still survived it: deleting the `isinstance(code, int)` guard (`502.0 in
  RETRYABLE_STATUS_CODES` is `True`, so a float code would have bought three attempts),
  making the `MAX_RESPONSE_BYTES` raise retryable (an oversized body from a possibly
  compromised endpoint would have been pulled down three times instead of once), and dropping
  the diagnosis above. The size-cap property in particular had never been asserted at all — it
  held **by omission of a parameter**, and the tests around it used `return_value`, so they
  would have passed at three attempts too. Each is now pinned by a test that fails when the
  mutant is applied and passes when it is not, verified one at a time.

## [0.4.0] — 2026-08-14

Two nights, eighteen PRs (#13–#30). Same shape as 0.3.0: defences that existed on paper
and not in the code. What is new is where they were found. Every item below came out of
running something and reading the answer, and none of it out of re-reading a document.

### Fixed — the council was quietly shipping cut answers

- **Truncation was reported as success.** An E2E run came back with all three voters at
  `finish_reason='length'`; two of them delivered a half-answer labelled `[OK]`, Stage 2
  ranked those halves and the chairman synthesised them. Nothing went red, and nothing
  had for months: the API returns `finish_reason` on every call and the client threw it
  away. It is now carried on `CallResult`, printed as `[TRUNCATED]`, counted in the TOTAL
  line, explained in the ERROR SUMMARY, and it **degrades the run to exit 3** — because
  the exit code is what the scheduled run reads, and models get more verbose over time.
- **Three of the four seats were reasoning models**, including the chairman, which this
  project's own config forbids in capital letters. The catalogue said so all along:
  `GET /api/v1/models` carries a `reasoning` object, *"omitted for non-reasoning models"*,
  and `gemini-3.5-flash-lite` had it `mandatory: true`. The deny-list in the tests covered
  **only the chairman** while all three voters had drifted onto reasoning models, one of
  them with reasoning that cannot be switched off.
- **A model property and a provider behaviour are different things.** `kimi-k2-0905` is
  *not* a reasoning model, and still burned 800 tokens producing nothing: Novita served it
  that way. The response body carried no `message.reasoning`, so the error said
  `reasoning=absent` and pointed away from the cause. Only `native_tokens_reasoning` on
  `GET /api/v1/generation` told the truth.
- **The Stage 2 prompt showed a format the parser rejects.** It read
  `RANK: <best>,<middle>,<worst>`, and a voter answered `RANK: <A,B,C>` — it copied the
  angle brackets, which is a fair reading. The prompt and `RANK_REGEX` are one contract in
  two places; the test asserted the two halves separately and so pinned the mismatch
  instead of catching it. The example is now extracted *from the prompt* and fed to the
  parser, and it is `B,C,A` rather than `A,B,C`: an example anchors, and a voter that
  merely echoes it must stay visible.
- **The exhaustion error said only `HTTPError`.** `429` (rate limit) and `503` (provider
  outage) are the same word and call for opposite decisions — change the seat, or wait.
- **The retry set did not match the documented codes.** It retried `504`, which OpenRouter
  does not document, and ignored `524 EdgeNetworkTimeout` and `529 ProviderOverloaded`,
  which it emits precisely when a provider is under stress. Read from `openapi.json` and
  realigned, with `408` added.

### Changed — the seats, rebuilt on a measurement

| seat | model | measured |
|---|---|---|
| Voter EU | `mistralai/mistral-small-3.2-24b-instruct` | `stop`, 809 tok, 0 reasoning |
| Voter US | `meta-llama/llama-3.3-70b-instruct` | `stop`, 850 tok, 0 reasoning |
| Voter CN | `deepseek/deepseek-chat` | `stop`, 1059 tok, 0 reasoning |
| Chairman | `openai/gpt-4.1-mini` | `stop`, 823 tok, 0 reasoning |

- **Europe is back, and not through the BYOK key everyone assumed was the only route.**
  The July note had Mistral out on rate limits; a *smaller* Mistral answers, complies with
  ZDR, and is the cheapest of the three. Three weeks of assumption ended in four minutes
  of measurement.
- Cost per run **~$0.013 → ~$0.005**, with longer answers.
- `MAX_TOKENS_STAGE_1` **800 → 1400**. The longest answer that finished on its own is 1059
  tokens, so the ceiling now sits a third above it. The old 800 had been set *at* the day's
  measurement rather than above it.
- `provider.quantizations` **measured and deliberately not set**: the field is an allowlist
  and every endpoint of the chairman declares `unknown`, so any allowlist leaves the chair
  with zero compliant endpoints. Under `allow_fallbacks: false` that is not a downgrade,
  it is a run with no final answer.

### Added

- `scripts/probe_models.py` + `probe.yml` — measures a candidate on the **real** Stage 1
  prompt, at the real budget, with the real routing, then resolves who served it. The seat
  rule existed as a comment in `config.py` and was relearned the hard way twice; it is a
  command now.
- `CallResult.generation_id`, the documented key for `GET /api/v1/generation` — printed
  next to answers that look **fine**, because a degraded answer never reaches an error
  message. `request_id` stays but is null in practice: OpenRouter sends neither header.

### Testing & CI

- Mutation score **55.5% → 86.7%**, floor raised 55 → 80 → 85, always *after* the
  measurement. `stages.py` went from 25 survivors to 1.
- The mutation workflow now prints the **diff** of each survivor, not just its name —
  mutmut cannot run on this workstation, so a name alone left only guessing.
- Decision logic (`_is_truncated`, `_collect_failures`) **moved out of `__main__.py`**,
  which is excluded from mutation. The exclusion was honest while that file was only
  presentation; the code moved rather than the threshold.
- Required checks **8 → 10**: `gitleaks` and `dependency-review` ran on every PR without
  blocking anything, because they were added to the CI in August and never to a ruleset
  last edited in July. Adding a job and making it required are two different places.
- Supply chain: dev dependencies pinned **by hash**, gitleaks and dependency-review as
  gates, `permissions: {}` per workflow. Dependabot deliberately watches the Actions only,
  with the cost of that choice written down.

### Note on releases

Tagged on 2026-08-16, which is later than both entries describe.

`v0.4.0` points at the current `main`. `v0.3.0` was applied retroactively to `58d9275`,
the commit that carried the version bump; that commit is dated 2026-07-26 and matches the
heading above, so the code the tag points at is the right code. The tag object itself was
created in August, and `git tag -l --format='%(creatordate)'` will say so. Before this,
the published tags stopped at `v0.2.0` while the code said `0.3.0`: a changelog heading
reads like a release, and for three weeks two of them were not.

## [0.3.0] — 2026-07-26

Nine PRs in one session (#3–#11). The theme: three defences the project *declared*
but never *enforced*, found by running it instead of reading it.

### Security
- **Zero Data Retention is now enforced per request.** `PROVIDER_ROUTING` sends
  `{"zdr": true, "data_collection": "deny", "allow_fallbacks": false}` on every call —
  **fail-closed**: no compliant endpoint means an error, never a silent downgrade to a
  provider that retains. Before this the payload carried no `provider` block at all and
  the account toggle was off: the 0.2.0 entry below claims "ZDR routing compliance",
  which described the models, not the routing.
- **Path traversal on `--env` closed** (SonarCloud, High). Any `KEY=VALUE` file on disk
  could be read into `os.environ` — not just a file read, an environment injection, on a
  tool whose arguments are assembled by a model. Now: path resolved, must be a regular
  file under 64KB, and only four allowlisted keys are imported.
- **Prompt-injection fences carry a per-run nonce.** The delimiters were fixed strings
  in a public repo, so a voter could close its own block by typing the closing marker.
  Stage 3 rankings are now fenced too — they were interpolated raw while the responses
  beside them were fenced.
- OWASP mapping realigned to the **2025** taxonomy (the IDs are not interchangeable with
  2023: LLM05 went from Supply Chain to Improper Output Handling), plus MITRE ATLAS
  techniques. Supply Chain moved from "out of scope" to mitigated: it was true of the
  runtime and false of the CI.

### Fixed
- **The "Italian bug" was never about Italian.** Two voters were replaced in two months
  on the theory that they refused Italian queries. Both were *reasoning* models: they
  spend `max_tokens` thinking and only then write `content`, and an Italian prompt makes
  them think longer. The error message now names the cause instead of the symptom.
- **Langfuse sessions were never grouped** because the fields went into `metadata`
  instead of top-level `user` / `session_id` / `trace`. Seven patterns had been tested
  in May — all seven varied the contents of a field nobody reads.
- Voters and chairman replaced with models **measured against the real prompt** under
  full ZDR. Cost per run: ~$0.027 → **~$0.013**.
- A reasoning model may never be the chairman: a voter that runs out of budget leaves a
  2-of-3 council, the chairman doing it loses the entire run.

### Testing & CI
- Coverage **50% → 100%** (lines and branches), measured for the first time. Among the
  uncovered lines was `_label_responses`, the LLM01 defence the docs claim by name.
- Every new behaviour verified by mutation: break the code, watch the test go red.
  *(This entry originally read "33 → 122 tests". The number was wrong when it was
  written — the suite was at 151 — and it is the reason this project stopped writing
  test counts down: the same figure lived in four disconnected places with four
  different values. A count is a fact with a shelf life; the gate that enforces it
  is not.)*
- Branch protection with **8 required checks**; actions pinned to SHA with zizmor
  guarding the pinning; SonarCloud analysis from CI with the same coverage as the local
  gates; **weekly live E2E** that fails on exit 3 — the degraded run that a human spots
  and a cron does not.

## [0.2.0] — 2026-05-15

### Added
- Initial council protocol: 3-stage pipeline (independent responses → blind peer rank → external chairman synthesis)
- OpenRouter HTTP client with retry logic, JSON schema validation, response size cap (256KB), TLS verification, request_id capture, redacted `__repr__`
- Graceful per-voter degradation: a failed/refused voter does not abort the run (council continues with N-1)
- Structured observability via stderr JSON (Langfuse-compatible, includes `trace_id` correlating all 7 calls of a single council run)
- ZDR (Zero Data Retention) routing compliance: all 4 default models eligible via enterprise endpoints (Azure / Vertex / native)
- Anti-prompt-injection: fenced response delimiters in Stage 2/3 prompts (defense in depth with server-side OpenRouter Workspace Guardrail)
- Per-run token ceiling (50k) — protects spend cap against runaway loops
- Input length cap (4000 chars) on user question
- API key format validation at client init (rejects non-`sk-or-` prefix)
- ERROR SUMMARY block at end of each run with calibration hints per error class (refusal, HTTP error, malformed)
- OWASP LLM Top 10 pre-flight security checklist embedded in the Claude Code skill

### Default models (T2 balanced tier, ~$0.02/query)
- Voter 1: `openai/gpt-5.4-mini` (OpenAI, routed via Azure ZDR endpoint)
- Voter 2: `google/gemini-2.5-pro` (Google, routed via Vertex ZDR endpoint)
- Voter 3: `qwen/qwen3-235b-a22b-thinking-2507` (Alibaba Qwen, native ZDR, reasoning specialist)
- Chairman: `meta-llama/llama-4-maverick` (Meta open-weight, native ZDR, provider-distinct from all voters)

Anthropic models are intentionally excluded from both voter and chairman roles (strict no-self-vote rule).

### Tested
- 33 unit tests (input validation, env loading, API key format, RANK regex parsing, HTTP error handling with mocked `urlopen`)
- 3 end-to-end runs on real OpenRouter against live models with diverse query types (technical, career-decision, daily-driver choice)

### Known limitations
- **Langfuse session linkage is best-effort**: the client passes `metadata.langfuse_session_id`
  in the OpenRouter request body, but empirical testing (7 patterns) showed inconsistent
  server-side mapping by the OpenRouter → Langfuse plugin for raw HTTP gateways.
  Authoritative correlation for grouping the 7 calls of a single council run is the
  client-side `trace_id` emitted on stderr by `council/observability.py`.
  See README for details and future direction.

<!-- Le intestazioni usano la convenzione Keep a Changelog `## [x.y.z]`, che senza queste
     definizioni e' solo grafica: parentesi quadre che sembrano link e non lo sono. Sono
     state aggiunte il 2026-08-16, quando i tag hanno reso possibile il confronto vero. -->
[Unreleased]: https://github.com/MK023/llm-council/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/MK023/llm-council/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/MK023/llm-council/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/MK023/llm-council/releases/tag/v0.2.0

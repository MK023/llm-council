# Changelog

All notable changes to this project are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed — what the reviews of the fixes found

Both adversarial reviews came back **blocking**, and between them they caught four defects in
work whose entire subject was *"a claim wider than the mechanism behind it"*.

- **A tenth writer to stderr survived the conversion, and the test written to catch it could
  not see it.** The gate matched lines containing `file=sys.stderr` *and* starting with
  `print(` — and the survivor was a `print(` the formatter had split across lines, so neither
  of its lines satisfied both halves. It counted one writer, reported OK, and the one it
  missed interpolated `args.env`, which this project's own threat model calls
  attacker-influenced. **It is the same shape as the `test ! -f requirements.txt` guard
  already recorded in `CLAUDE.md`**: a check written against the surface form of the code
  instead of its meaning, green against the very defect it exists to catch. Parsed with `ast`
  now, and it also sees `sys.stderr.write`, which the old one was equally blind to.
- **The tests guarding the log level were invisible to the mutation gate.** They used
  `patch.dict(os.environ, …, clear=True)`, and mutmut's trampoline selects which mutant to
  activate by reading `MUTANT_UNDER_TEST` **from the environment** — so clearing it ran every
  mutant as the original and reported it survived. Eleven of fourteen tests in the file were
  attributed to nothing, the score read 87.8% instead of 88.0%, and the gate for the log-level
  finding defended nothing. The correct convention was already two files away in
  `tests/test_observability.py`: `clear=False` plus a `pop` of the single key.
- **`RETRY_BACKOFF_SECONDS = (0, 0)` and `(2, 2)` passed the whole suite.** The new tests
  pinned the length and the ordering, and the only test touching the values compared them
  against the tuple itself. The finding is measured in *seconds*, so the seconds are what had
  to be pinned — the same "fix the symbol, not the number" mistake, caught for the third time
  in two days.
- **The `github.ref` guard was tied to the branch NAME on every event.** On a rename the
  weekly sentinel would have stopped **silently** — a skipped job is not a red job, and the
  Sentry check-in lives inside that same job. It now applies only to `workflow_dispatch`.
  GitHub documents `GITHUB_REF` on a `schedule` trigger as the default branch, and that
  *"scheduled workflows will only run on the default branch"*, so the cron was never at risk;
  the guard was. And the comment beside it claimed a defence against a hostile collaborator
  that it does not provide — GitHub runs the workflow **as it exists in the dispatched ref**,
  so anyone with write access edits the `if:` on their own branch. It stops a careless
  dispatch; the real control is a ruleset or an Environment with required reviewers, and the
  comment says so now.

### Fixed — the six things that had been written down instead of fixed

They were reported and left alone under the project's own rule — *notice a problem nobody
asked you to solve: say it, do not fix it* — and closed on 2026-09-01 when asked to. None was
a regression; all six predate the work of 2026-08-31. Each one now has a test, because a debt
paid without a gate is a debt that comes back.

- **`COUNCIL_LOG_LEVEL` was a switch on the outside of the door.** Read straight from the
  environment and handed to `setLevel()`, it had two failure modes and the quiet one was
  worse: an unknown value raised `ValueError` and killed the process, while a valid-but-high
  value silenced **every** telemetry record — the run still succeeds, the log says nothing,
  and the new Langfuse check reads an empty file and reports that the council emitted nothing
  at all. It is now an allow-list capped at INFO: `DEBUG` is the only genuine choice, because
  every record is emitted at INFO and anything above is a silencer wearing the costume of a
  verbosity setting.
- **`question_hash` was 32 bits.** Widened to 64, and the argument is functional before it is
  defensive: the field exists to tell runs on the same question apart from runs on a different
  one, and eight hex characters are narrow enough for two unrelated questions to collide and
  quietly merge. The confidentiality angle is thin and now written down rather than implied —
  a truncated unsalted digest can be *confirmed* by anyone holding a candidate question, but
  in the one place these hashes are public the question is public too, because it is in
  `e2e.yml`. A salt would close that and destroy the correlation the field exists for.
  **Hashes from before this change do not correlate with hashes after it.**
- **A UTF-8 BOM made the API key vanish in silence.** `.env` was read as `utf-8`, the BOM
  stuck to the first key name, `strip()` does not remove it, the allow-list rejected the
  result, and the process exited 2 saying the key was "not set" when the truth was "present
  and silently discarded" — in a file that looks correct in every editor that hides the BOM.
- **`RETRY_BACKOFF_SECONDS` had a dead entry.** `(1, 2, 4)` with `MAX_RETRIES = 3` never
  reached the `4`, because the loop sleeps *between* attempts. That unreachable entry was read
  as truth: three documents stated the backoff totalled seven seconds when it totalled three,
  which is part of why the Mistral rate limit looked survivable for two Mondays. The tuple is
  now `(1, 2)` and a test pins its length to `MAX_RETRIES - 1`, so the two cannot drift apart
  in either direction.
- **Ten writers reached `council.stderr` and one of them was guarded.** `OpenRouterError`
  flattened its own message; the other nine interpolated exception text and filesystem paths
  into a file that `scripts/langfuse_check.py` now *parses*, one JSON object per line. A guard
  on one producer of a shared sink is not a guard on the sink. There is a single writer now,
  and a test fails if a second `print(…, file=sys.stderr)` ever reappears.
- **`e2e.yml` described a branch guard nobody had implemented.** Its header said cron and
  manual runs both happen on the main branch; `workflow_dispatch` accepts **any** ref. Both
  workflows that mount the repository's secrets now check `github.ref`. Enforced rather than
  reworded — the sentence is true because the job tests it, which is the whole lesson of the
  last two days.
- Also: the E2E printed the closing fence **twice**, because the explicit `echo` from #40 was
  left beside the trap that replaced it. Harmless, and two ways of doing one thing are two
  ways to drift.

### Security — "printed as text" had a consumer where it was false

- **In GitHub Actions a log line starting at column 0 with `::` is a command to the runner**,
  not text: `::error::`, `::add-mask::`, `::stop-commands::`. The weekly E2E prints three
  answers, three rankings and a chairman synthesis — **all written by language models** —
  into the log of a public repository. `SECURITY.md` claimed under LLM05 that model output
  "is printed as text and never executed", and for every consumer but this one it was true.
- **The answers are not sanitised, and must not be.** They are the tool's own product, they
  are multi-line markdown, and mutilating them would break what the council exists to
  produce. So the text is not filtered — the command channel is closed around it. `e2e.yml`
  wraps the run in `::stop-commands::` with a token from `openssl rand -hex 16`, *"randomly
  generated and unique for each run"* as GitHub's documentation requires: with a fixed token
  in a public repo, a model could turn commands back on itself. `$GITHUB_OUTPUT` is
  unaffected — it is a file, not a workflow command.
- **The fence itself now has a gate**, and this is the part the two reviews agreed on. The
  sanitising half had five tests; the half protecting the *answers* was fifteen lines of YAML
  that anyone could move or delete with every check green, while `SECURITY.md` asserted it in
  prose. `tests/test_workflow_fences.py` reads the workflows and pins the fence, the per-run
  token, the `:?` guard against an empty one, the traps, and the job wall clock. Writing it
  found three real defects in the same hour: `probe.yml` had no fence at all and no
  `timeout-minutes` — it prints third-party catalogue strings into the same public log — a
  missing `openssl` would have opened the fence with an *empty* token under `set +e`, and
  `trap … EXIT` **does not fire on SIGTERM**, measured, which is the case it was added for.
- **Error messages are flattened to a single line in `OpenRouterError`.** They carry the
  provider's own words, and those words now reach a second line-oriented parser:
  `council.stderr`, which `scripts/langfuse_check.py` reads one JSON object per line. A
  newline in that text is not cosmetic — it is a way to add a record, and the review of #39
  reproduced three generations that never happened. The flattening lives in the constructor
  and not at each `print`, because every message converges there and a guard per call site is
  a guard that gets missed at the next call site. C0 controls and DEL go with it: ESC drives
  terminal escape sequences, NUL truncates in some consumers, and a diagnostic is one line.

### Fixed — the monitor's own failure modes, found by reviewing it

- **"Always exit 0" was written as a list of exception types, which is not a promise.**
  `{"data": null}` and a `totalCost` arriving as a string — and Langfuse's own doc sample
  shows price fields as strings — both escaped as `TypeError`, exited non-zero, turned the
  E2E red and sent `status=error` to Sentry: a false alarm about the council, raised by the
  thing watching the council. The clause is now `except Exception`, which here is the
  correct choice rather than the lazy one, and still prints only the exception's *type* —
  its message could carry the request URL, and the URL is built from a secret.
- **Pagination had no iteration cap.** An API returning the same cursor looped forever and
  the job died on `timeout-minutes` — again, red attributed to the council.
- **`test_langfuse_check.py` would have broken the whole mutation gate.** It imports
  `scripts.langfuse_check` at module level, mutmut copies `council/` and `tests/` but not
  `scripts/`, collection dies on `ModuleNotFoundError` and the gate reports zero mutants
  tried while looking busy. `pyproject.toml` documents this trap verbatim from 2026-08-14
  and it happened again anyway; the comment now says a new `--ignore` line is needed each
  time a test imports from `scripts/`.
- **`"marco-bellingeri"` was a second hand-kept copy.** It is now `USER_ID` in `config.py`,
  imported by both writer and reader: two copies drifting would not silence this check, they
  would make it warn every week forever about an ingestion that is working perfectly.
- **`LANGFUSE_BASE_URL` had no scheme validation** while reaching `urlopen`, which speaks
  `file://` — reading local disk instead of the network — and `http://`, on which the Basic
  auth would travel in the clear. It must be `https://`.
- **The poll budget was 75 seconds and three documents said 90.** It sleeps *between*
  attempts, not after the last one. It is computed now instead of written by hand.

### Added — the half of the telemetry nothing was measuring

- **`scripts/langfuse_check.py`, run by the weekly E2E.** Ingestion into Langfuse does not
  start in this code: it goes through OpenRouter Broadcast, a checkbox in an account, and no
  unit test can reach a checkbox in someone's dashboard. So that half was measured by nothing
  at all — it worked, but by luck rather than by proof, and nobody had looked until
  2026-08-31. The script asks Langfuse whether the run that just happened arrived, and prints
  the 30-day spend beside it.
- **It warns and never fails**, always `exit 0`, and a missing stderr file or an unreachable
  API is a warning too. A guard that kills what it guards is worse than no guard: the
  council's verdict belongs to the step before.
- **The alarm is not on the run it just watched.** Langfuse documents up to **15 minutes** of
  ingestion delay for third-party exporters, and Broadcast is one; a 90-second poll shouting
  "data loss" would be shouting at a documented delay, which is how a monitor teaches everyone
  to ignore it. The alarm sits where the delay cannot reach: *no complete council run in the
  last **8** days*. Eight and not seven because GitHub's scheduler slipped nearly seven hours
  on 2026-08-31 — a window equal to the schedule fires on the wrong system.
- **Three details that came from measuring the live API, not from reading its docs.** Filter
  by `sessionId`, never `traceId`: when a stage fails OpenRouter mints its own trace id for
  that call, so one run lands as two traces sharing one session — filtering by trace counts
  6 of 7 and reports a loss that never happened. Compare `arrived >= sent`, never `==`: a
  failed attempt still produces a generation there while the telemetry records no
  `generation_id` for it. And `LANGFUSE_BASE_URL` has **no default**, because Langfuse Cloud
  has separate EU and US regions and the wrong one does not fail — it answers `200` with zero
  results, the most convincing false alarm available.

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

### Removed — a lamp wired to nothing

- **`langfuse_opt_in` is gone from every telemetry record.** It went `true` when
  `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` existed in the environment and `false`
  otherwise. Neither state said anything about whether a trace reached Langfuse: ingestion
  runs through **OpenRouter Broadcast**, an account setting outside this repo, and those
  keys are no part of it. A reader who saw `true` concluded telemetry was flowing. On
  2026-08-31 it *was* flowing — 44 runs recorded since 2026-08-13, correctly grouped by
  session — and **nobody had ever checked**. The field had made an unverified fact look
  verified, which is worse than showing nothing.
- **The `--env` allow-list is down to `OPENROUTER_API_KEY`.** The three `LANGFUSE_*` names
  were admitted for an integration this codebase never contained, and `LANGFUSE_HOST` was
  read by no line at all. An allow-list is a statement about what the program uses; three
  unused names made it describe a program nobody had written. The security property is
  unchanged and narrower.
- **`observability.py` no longer claims an "opt-in Langfuse backend".** It writes JSON to
  stderr. That is the whole module.

### Fixed — documentation that had been wrong for months

- The README told you to set two Langfuse keys under a heading that implied they enabled
  tracing. They enabled one boolean in a local log line. It also described the destination
  as "self-hosted via `langfuse-devops-lab`" — that project uses **Langfuse Cloud, EU
  region**, and nothing here is self-hosted.
- **The README said the Sentry cron monitor "receives its check-ins, but it is not alerting".**
  It does not receive them. Queried directly on 2026-08-31: `status: disabled`, *"No check-ins
  found"*, and `ok=0 error=0 missed=0` for the very hour in which the E2E ran and its own log
  printed `check-in Sentry: error`. **The check-in is sent and thrown away.** Not
  instrumented-but-silent — not instrumented. Found by the review, which noticed that this
  commit was removing a false indicator from the logs while leaving the identical false
  indicator in the most-read file in the repo.
- **And the conclusion drawn from that measurement was itself too wide — corrected here.** The
  entry above first went on to say *"the missed-run case is unguarded"*. It is not: since
  2026-08-14 `scripts/sentinella-cron.mjs` in the site repo (workflow `sentinella-cron.yml`,
  daily) polls the GitHub API and alarms when `llm-council-e2e` has been quiet more than 10
  days. Sentry was queried, `disabled` was seen, and nothing outside this repo was looked at —
  the same defect the rest of this release closes, committed while closing it. The unguarded
  case is the **other** one, started and *failed*, which is how the E2E sat red for a week
  after 2026-08-24.
- **`SECURITY.md` still said "only four allowlisted keys are imported"** under LLM06, citing a
  test class this very commit modifies — so the document had been opened and the number had
  not. `test_security_doc.py` checks that a cited test exists, never that a number in prose is
  still true. The claim now names the key instead of counting.
- **`CLAUDE.md` claimed mutmut cannot run on this workstation** (*"libcst ships no wheel for
  Apple x86_64"*). It runs: 693 mutants, **87.9%** against the floor of 85, Python 3.12.7,
  x86_64. What is true is narrower — under 3.10.14, which is what bare `python` resolves to
  outside the project directory, mutmut is simply not installed. A missing package under one
  interpreter had been recorded as a property of the hardware, and that made the one gate this
  project most depends on look impossible to run locally.
- `CLAUDE.md` said nothing about observability at all, so the trap that matters most was
  undocumented: **the ingestion half does not live in this repo**, and no test can reach a
  checkbox in someone's dashboard. It now also records that the Sentry cron monitor
  `llm-council-e2e` is **disabled and has never checked in** — free plan, one monitor per
  account, seat taken — so the long comment in `e2e.yml` describes a guard that is not
  running. That is why the E2E stayed red for a week with nobody noticing.

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

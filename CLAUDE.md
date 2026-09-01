# CLAUDE.md — llm-council

> Project memory, loaded every turn. Short and dense. General rules — PRs, baseline security,
> the two MUST models, branching from `origin/main`, what counts as proof — live in Marco's
> global `CLAUDE.md` and in `~/.claude/rules/`. Here only what is specific to this repo. A
> section that grows becomes a file with a path pointer.

<!--
CRITERIO DI QUESTO FILE, 25/08/2026 — primo CLAUDE.md di questo repo, scritto sul modello di
~/GitHub/agentic-os/CLAUDE.md. Aprilo con Read: il criterio completo, la procedura di
trasloco e il collaudo stanno nei suoi commenti in testa.

Tre caselle, ogni riga sta in una sola:
  su Marco .................... ~/.claude/CLAUDE.md
  su come si fa un progetto ... ~/.claude/rules/lavorare-su-un-progetto.md
  su QUESTO repo .............. questo file

Cio' che NON e' entrato qui, di proposito: la struttura di `council/` e `tests/` (la da'
`ls`), l'elenco dei gate (li dice `.github/workflows/`), il perche' dell'anti-sycophancy
(sta in README.md e SECURITY.md). Qui restano solo i trabocchetti e le decisioni chiuse —
cio' che leggere il codice NON insegna, e che si e' pagato per scoprire.

Questi commenti sono gratis: rimossi prima dell'iniezione in contesto. La garanzia vale
SOLO per i file CLAUDE.md, non per rules/ o SKILL.md.

LA LEZIONE DELLA PRIMA STESURA, che vale per ogni repo su cui si ripete questo lavoro.
La prima versione e' stata scritta dalla pagina Atlas del progetto invece che dal codice,
e una revisione avversaria ha trovato QUATTRO affermazioni false su 39 verificate. Tutte
e quattro venivano dalla stessa cosa: la pagina era aggiornata al 14/08 ma la sezione
copiata era ferma alla mattina del 13/08, e il copia si e' fermato una sezione troppo
presto. La piu' cara diceva che `finish_reason` non era esposto e che il difetto era
"ancora aperto, ed e' la radice" — chiuso da undici giorni, con il codice a due righe di
distanza.

Regola che ne esce: una knowledge base invecchia, il codice no. Dove divergono ha ragione
il repo, e ogni affermazione su un controllo si verifica ESEGUENDO prima di scriverla —
`python3 -c "from council.config import ..."` costa due secondi. Un CLAUDE.md e' caricato
a ogni turno: una riga falsa li' dentro non e' un refuso, e' un'istruzione sbagliata data
a ogni sessione futura.
-->

## What this is

Anti-sycophancy verification council: three independent voters answer, blind-rank each other,
and an **external** chairman synthesises. Python **stdlib-only** — `urllib.request`,
`unittest`, no runtime dependencies at all — talking to OpenRouter. Wrapped as a Claude Code
skill (`/llm-council`). Public repo, MIT.

The point is structured disagreement on high-stakes decisions, so **a council that converges
is an expensive echo**. Voters are meant to dissent; only the chairman has to be strong.

## The seats — measured, never chosen by pedigree

| seat | model | why |
|---|---|---|
| Voter EU | `mistralai/mistral-small-3.2-24b-instruct` | cheapest of the three |
| Voter US | `meta-llama/llama-3.3-70b-instruct` | |
| Voter CN | `deepseek/deepseek-chat` | |
| Chairman | `openai/gpt-4.1-mini` | **outside** the voter pool |

- **Anthropic is strict-excluded** from both pools: whoever orchestrates the council does not
  vote. Marco has a BYOK key and deliberately does not use it here.
- **Never seat a reasoning model**, chairman least of all: it spends `max_tokens` thinking and
  then writes nothing. A truncated voter leaves a 2/3 council; a truncated chairman loses the
  whole run. `config.py` says so in capitals for the chair, and `tests/test_routing.py`
  (`KNOWN_BUDGET_BURNERS`) is what actually checks all four seats — on 2026-08-14 **three of
  four were reasoning models anyway**, because the deny-list only covered the chairman. A rule
  applied to one seat out of four is applied nowhere.
- **A model's catalogue entry is not the provider's behaviour**: `kimi-k2-0905` has `reasoning`
  omitted in `GET /api/v1/models` and still burned 715-800 tokens producing nothing, because
  *Novita* serves it that way. Only `native_tokens_reasoning` on `GET /api/v1/generation` told
  the truth.
- Changing a seat means running `scripts/probe_models.py` first — real Italian prompt, full ZDR
  routing, and the budget stage 1 actually ships (`MAX_TOKENS_STAGE_1`, 1400 today;
  `PROBE_MAX_TOKENS` overrides it). **Measure at the budget you will ship, never below it.**
  `HTTP 404 "No endpoints found"` is **not** a bug: it is
  `allow_fallbacks: false` refusing a non-compliant endpoint. The privacy posture costs
  candidates, and that is the trade.

## Commands

```bash
python -m unittest discover tests/ -v
python -m coverage run -m unittest discover tests/ && python -m coverage report
python -m coverage report --include="council/client.py,council/config.py,council/stages.py" --fail-under=90
ruff check council/ tests/ scripts/ && ruff format --check council/ tests/ scripts/
zizmor .github/workflows/
mutmut run && mutmut export-cicd-stats && python scripts/mutation_gate.py mutants/mutmut-cicd-stats.json 85   # gira in locale SOLO su 3.12: vedi sotto
python scripts/pin_dev_deps.py                             # never hand-edit the -dev/-mutation lockfiles
```

Dev dependencies are **hash-pinned** and generated from the PyPI API, never written by hand.

## What NOT to do (closed decisions — don't reopen without new data)

- **No runtime dependencies. Ever.** The guard is `tests/test_packaging.py`, which reads the
  real manifest. It used to be `test ! -f requirements.txt` in CI — a file this project would
  never create, since the manifest is `pyproject.toml`: adding `dependencies = [...]` passed
  every gate.
- **No SBOM, no signed attestation.** Zero runtime dependencies and **nothing is distributed**:
  an SBOM would list nothing and a signature would sign nothing. The supply chain that exists
  is the pipeline, defended by SHA pinning plus zizmor. A motivated Level 1 is professional; a
  cargo-cult Level 4 is theatre.
- **Dev-deps are outside Dependabot on purpose**, and the cost is written beside the choice:
  `--require-hashes` plus a bot that raises versions without regenerating hashes. A CVE in
  ruff, coverage, zizmor or mutmut waits for a human. Valid only while the repo publishes
  nothing and has no runtime deps — **the condition is in the file so the day it falls is
  visible**.
- **Never write the test count anywhere.** It once lived in four disconnected places, each
  stale by a different amount. Declare the property a gate defends, not a number with an
  expiry date.
- **The mutation floor moves only after a measurement**, and stays *below* the current score: a
  floor equal to the score turns the next unlucky mutant into a red build. It opened at 55 on
  2026-08-13 and is now **85** — `MUTATION_FLOOR` in `mutation.yml`, declared in one place.

## Gotchas paid for

- **A truncation reads exactly like success, and did for months.** On 2026-08-14 all three
  voters came back at `finish_reason='length'`, two of them marked `[OK]`; stage 2 ranked half
  answers and the chairman synthesised them. **Closed the same day**: `finish_reason` is now
  carried on `CallResult` for every successful call (`client.py`), `_is_truncated` labels it
  `[TRUNCATED]` (`stages.py`), and a truncated run degrades to `exit 3` — the code `e2e.yml`
  fails on. Do not reopen it as a gap: read `council/client.py` before believing any document
  that says otherwise, this one included.
- **Coverage 100% is not evidence.** The gate's first run scored **55.5% with every line
  covered** — 253 survivors. It is **85.9% now** (488/568, measured 2026-08-13) **without a
  line of `council/` changing**: every mutant since died to a new assertion, which is the proof
  the gate was needed. The assertions checked that a line *ran*, not what it *produced*.
  (Over *all* of `council/` the same day it was 47.5%, ~70% of those survivors being string
  rewrites in the printing layer — which is why `__main__.py` is excluded from mutation.)
- **Aggregating a tool's output by symbolic name: check the groups sum to the total.** mutmut
  names class-method mutants with a different separator (`council.client.xǁOpenRouterClientǁcall`),
  so aggregating on the free-function pattern reported `client.py` as the most solid module when
  it was the least covered. The check that would have caught it costs nothing: **the per-module
  survivor counts must add up to the run total.**
- **The prompt showed a shape the parser rejects** (`RANK: <best>,<middle>,<worst>` → a voter
  copied the angle brackets), and the test asserted the two halves separately, freezing the
  mismatch instead of catching it. The example is now extracted **from** the prompt and fed to
  the parser, and it is `B,C,A`: with a non-identity order, a voter that just copies it is
  visible instead of blending into a real consensus.
- **A recorded limit without a measurement expires in silence.** "Europe is out on rate limits,
  we need a BYOK key" stood for three weeks and was false: a *smaller* Mistral answered fine.
  One measurement closed it.

## The mutation gate — runs locally, proves nothing locally

*"CI ONLY: libcst ships no wheel for Apple x86_64"* was wrong: it runs on 3.12.7, ~690
mutants, ~88%. Under **3.10.14** — bare `python` outside this directory — mutmut is just not
installed, and that missing package had been recorded as a property of the hardware. So:
`$(pyenv root)/versions/3.12.7/bin/python -m mutmut run`, after checking `python -V`.

**A green local run does not predict CI.** Local is mutmut 3.6.0 + pytest 8.3.4; CI pins
3.7.0 + 9.1.1. On 2026-09-01 local passed at 88.1% while CI aborted before trying a mutant.
Iterate with it; never conclude from it — reproduce faithfully in a throwaway venv pinned to
CI's versions instead of guessing, which cost two wrong fixes before it cost ten minutes.

Two ways it aborts, both paid for. **A test importing from `scripts/` or reading outside
`council/`** needs an `--ignore` line: mutmut copies neither, collection dies, and the gate
reports zero while looking busy. And **never set `propagate = False` on the council logger** —
pytest's plugin then attaches ITS handlers directly (measured: `_LiveLoggingNullHandler`,
`_FileHandler`, 2× `LogCaptureHandler`), `test_prompt_isolation` finds five where it demands
one, and mutmut aborts. Isolate a logger with `_pristine_logger` from `test_observability.py`,
never by patching `logging.getLogger` — `obs.logging` *is* the stdlib module.

## Observability — the half that does not live in this repo

- **Nothing here sends a trace to Langfuse.** `observability.py` writes JSON to stderr and
  stops. Ingestion is **OpenRouter Broadcast**, a checkbox in the account; the code only
  contributes the `user` / `session_id` / `trace` fields. **No environment variable turns it
  on.** Three that looked like they did — plus a `langfuse_opt_in` field that went true when
  they existed — were removed: a lamp wired to nothing made an unverified fact look checked.
- **`scripts/langfuse_check.py`** (from `e2e.yml`) is the only thing measuring that half:
  warning-only, always exit 0, alarms on *"no complete run in 8 days"* — never on the run it
  just watched, since ingestion may lag 15 minutes.
- **Group by `sessionId`, never `traceId`.** Measured 2026-08-31: when a stage fails,
  OpenRouter gives that call its **own** trace id, so one run lands as two traces with one
  shared `sessionId`. Filtering by trace counts 6 of 7 and cries data loss. And a failed
  attempt still produces a generation there while the telemetry records no `generation_id`
  for it — so compare with `>=`, never `==`.
- **Costs are there; unit prices are not.** `totalCost` is populated (OpenRouter sends the
  real figure); `totalPrice` is null — no Langfuse price sheet for these models. Reading the
  wrong one says "Langfuse does not track cost", which is false.
- **The Sentry cron monitor `llm-council-e2e` is `disabled` and has never checked in.** Free
  plan, one active monitor per account, seat held by `supabase-keepalive`. The long comment in
  `e2e.yml` therefore describes a guard that is **not running** — but *"nobody would notice a
  missed run"*, written here on 2026-08-31, was **false and is corrected**: since 2026-08-14
  `scripts/sentinella-cron.mjs` in `marcobellingeri.dev` (workflow `sentinella-cron.yml`,
  daily) polls the GitHub API and, when `llm-council-e2e` has been quiet more than 10 days,
  raises a Sentry event **and** opens an issue in the site repo. That guard is fail-open: an
  API error for one entry is skipped with a warning, without a DSN the Sentry call is a no-op,
  and its alarm path has never fired in production. **The blind spot is the other case —
  started and *failed*** — visible only as red on Actions, which is how the E2E sat red for a
  week unseen. Reached the wrong conclusion by querying Sentry, seeing `disabled`, and never
  looking outside this repo: a claim wider than the measurement under it. Re-measured
  2026-09-01, the `disabled` half still holds — `No check-ins found`, all stats zero.
- Secrets: **Doppler** (`llm-council`, config `prd`) and GitHub Actions. `doppler run --`, never a printed value.
- **`council.stderr` is parsed, not just read.** One writer — `_stderr()` flattens every line
  terminator, since a newline there *adds a record* to what `langfuse_check.py` counts. And
  `COUNCIL_LOG_LEVEL` is capped at INFO: above it telemetry goes silent and the check sees an
  empty run.

## Security

- **ZDR per request, fail-closed** — the provider block in `config.py`, sent on every payload:
  `{"zdr": true, "data_collection": "deny", "allow_fallbacks": false}`. It used to live only in
  comments and an account toggle that was off.
- **`--env` is an allow-list**, not a loader: resolved path, regular file under 64KB, and only
  four permitted keys. Anything else was *environment injection* on a tool whose arguments an
  LLM assembles.
- **Anti-injection fences carry a per-run nonce** (`secrets.token_hex(8)`); fixed markers in a
  public repo let a voter close its own block. Stage 3 rankings are fenced too.
- `SECURITY.md` maps OWASP Top 10 for LLM **2025** (the IDs are not interchangeable with 2023),
  and `tests/test_security_doc.py` fails if a heading is stale, a category has no verdict, or a
  test cited as proof does not exist.

## References (read on demand)

`README.md` (the protocol, the full gate list, the pipeline/test contract) · `SECURITY.md`
(OWASP LLM 2025 and MITRE ATLAS verdicts) · `CHANGELOG.md` · `.github/workflows/` (the gates
themselves — `probe.yml` runs the seat measurement in CI, `e2e.yml` weekly against the real API
and fails on **exit 3**, the degraded run a human notices and a cron does not).

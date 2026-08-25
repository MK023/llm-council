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
| Voter EU | `mistralai/mistral-small-3.2-24b` | cheapest of the three |
| Voter US | `meta-llama/llama-3.3-70b-instruct` | |
| Voter CN | `deepseek/deepseek-chat` | |
| Chairman | `openai/gpt-4.1-mini` | **outside** the voter pool |

- **Anthropic is strict-excluded** from both pools: whoever orchestrates the council does not
  vote. Marco has a BYOK key and deliberately does not use it here.
- **Never seat a reasoning model**, chairman least of all: it spends `max_tokens` thinking and
  then writes nothing. A truncated voter leaves a 2/3 council; a truncated chairman loses the
  whole run. `config.py` forbids it in capitals — and on 2026-08-14 **three of four seats were
  reasoning models anyway**, because the deny-list in the tests only covered the chairman. A
  rule applied to one seat out of four is applied nowhere.
- **A model's catalogue entry is not the provider's behaviour**: `kimi-k2-0905` has `reasoning`
  omitted in `GET /api/v1/models` and still burned 715-800 tokens producing nothing, because
  *Novita* serves it that way. Only `native_tokens_reasoning` on `GET /api/v1/generation` told
  the truth.
- Changing a seat means running `scripts/probe_models.py` first — real Italian prompt, full ZDR
  routing, 1200 tokens. `HTTP 404 "No endpoints found"` is **not** a bug: it is
  `allow_fallbacks: false` refusing a non-compliant endpoint. The privacy posture costs
  candidates, and that is the trade.

## Commands

```bash
python -m unittest discover tests/ -v
python -m coverage run -m unittest discover tests/ && python -m coverage report
python -m coverage report --include="council/client.py,council/config.py,council/stages.py" --fail-under=90
ruff check council/ tests/ scripts/ && ruff format --check council/ tests/ scripts/
zizmor .github/workflows/
mutmut run && mutmut export-cicd-stats                     # then: scripts/mutation_gate.py
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
- **Never write the test count anywhere.** It once lived in four disconnected places (site 33,
  README 122, Atlas 126, reality 151). Declare the property a gate defends, not a number with
  an expiry date.
- **The mutation floor moves only after a measurement**, and stays *below* the current score: a
  floor equal to the score turns the next unlucky mutant into a red build. It opened at 55 on
  2026-08-13 and is now 80.

## Gotchas paid for

- **`finish_reason` is not exposed on successful responses — a truncation is invisible.** This
  is still open, and it is the root. On 2026-08-14 all three voters came back truncated, two of
  them marked `[OK]`; stage 2 ranked them and the chairman synthesised them. It had looked
  healthy for months.
- **Coverage 100% is not evidence.** First mutation run: 47.5% with the lines fully covered.
  Then 81.5% — **without changing a line of `council/`**: 148 mutants died to new assertions,
  which is the proof the gate was needed. The assertions checked that a line *ran*, not what it
  *produced*.
- **Aggregating a tool's output by symbolic name: check the groups sum to the total.** mutmut
  names class-method mutants with a different separator (`council.client.xǁOpenRouterClientǁcall`),
  so aggregating on the free-function pattern reported `client.py` as the most solid module when
  it was the least covered — 116 survivors, not 6. 143 against 253 was there to see.
- **The prompt showed a shape the parser rejects** (`RANK: <best>,<middle>,<worst>` → a voter
  copied the angle brackets), and the test asserted the two halves separately, freezing the
  mismatch instead of catching it. The example is now extracted **from** the prompt and fed to
  the parser, and it is `B,C,A`: with a non-identity order, a voter that just copies it is
  visible instead of blending into a real consensus.
- **A recorded limit without a measurement expires in silence.** "Europe is out on rate limits,
  we need a BYOK key" stood for three weeks and was false: a *smaller* Mistral answered fine.
  Four minutes of measuring closed it.

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

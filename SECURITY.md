# Security Policy

## Threat model

This is a **single-user CLI tool** invoked locally (or wrapped by a Claude Code skill on the same machine). Threat model assumptions:

- Single user with full machine access (no multi-tenancy)
- No network exposure (does not run as a server, no inbound ports)
- No persistence of secrets beyond a local gitignored `.env`
- No autonomous agency (script invoked manually with explicit user question)

## Hardened against

Mapped to the [OWASP Top 10 for LLM Applications **2025**](https://genai.owasp.org/llm-top-10/).

> **Renumbered 2026-07-26.** This file previously used the 2023 list, where the IDs mean
> different things: 2023's LLM05 was Supply Chain, 2025's LLM05 is Improper Output Handling.
> Anyone cross-referencing an ID against the current list was being misled. Two categories
> are new (LLM07, LLM08) and two were dropped (Insecure Plugin Design, Model Theft).

| OWASP ID | Risk | Status in this project |
|---|---|---|
| **LLM01** | Prompt Injection | **Mitigated.** Fenced delimiters isolate voter output before it re-enters Stage 2/3 prompts — a voter's answer is untrusted input to the other voters. Server-side OpenRouter Prompt Injection Guardrail (regex, Flag mode). Here the *indirect* path is the real one: model output is fed back into model input. Exercised by `tests/test_prompt_isolation.py`, including a voter that tries to instruct the others and one that forges a fence boundary. Until 2026-07-26 this mitigation had **no test** — it was the project's stated LLM01 defence, never once run against an attack. |
| **LLM02** | Sensitive Information Disclosure | **Mitigated.** Per-request ZDR routing with `data_collection: deny`, fail-closed. Server-side PII redaction (email/phone/SSN/card). Telemetry carries identifiers only — never the question or the answers, enforced by `tests/test_stages.py::TestTelemetryPrivacy`. The fields added in 2026-08 (`generation_id`, `finish_reason`) are of the same kind: an opaque completion id and a stop reason, both useless to anyone who cannot already query the account. The API key is redacted in `__repr__`, never logged, and a test asserts it never reaches stdout/stderr. |
| **LLM03** | Supply Chain | **Mitigated — no longer out of scope.** Zero runtime dependencies, stdlib only, enforced by `tests/test_packaging.py`, which reads `pyproject.toml` — the manifest that actually declares dependencies. Until 2026-08-13 the guard was `test ! -f requirements.txt` in CI: a file this project would never create, so adding `dependencies = [...]` passed every gate. The real supply chain here is the *pipeline*: GitHub Actions run third-party code on every push. Actions pinned to SHA with version comments, `persist-credentials: false`, zizmor auditing the workflows as a blocking gate, Dependabot on the actions. Previously this row read "out of scope, stdlib-only" — true of the runtime, false of the CI. |
| **LLM04** | Data and Model Poisoning | **Out of scope.** We are a consumer of hosted models, not a trainer; no fine-tuning, no embedding store under our control. |
| **LLM05** | Improper Output Handling | **Mitigated.** Model output is printed as text and never executed: no `eval`, no shell, no SQL, no HTML rendering. Stage 2 rankings are regex-validated and a non-matching output is flagged invalid, never guessed — verified by `test_unparseable_output_is_flagged_not_guessed`. |
| **LLM06** | Excessive Agency | **Mitigated.** No autonomous rerun, no self-escalation, no config rewriting. The CLI runs once per invocation with an explicit question. Absorbs 2023's Insecure Plugin Design: the Claude Code skill invokes only the local module, with no external download or execution. **`--env` is bounded** (2026-07-26): the path is resolved and must be a regular file under 64KB, and only four allowlisted keys are imported. Before this, any `KEY=VALUE` file on disk could be read into the process environment — and since a model assembles this tool's arguments, that path was model-influenced. Covered by `tests/test_cli.py::TestEnvPathIsBounded`. |
| **LLM07** | System Prompt Leakage | **Not applicable, by design.** The project has no confidential system prompt: `stage2_prompt` and `stage3_prompt` live in `config.py` in a public MIT repository. Nothing to leak — and that is the point. A prompt is not a security control; if secrecy of the prompt mattered, the design would be wrong. |
| **LLM08** | Vector and Embedding Weaknesses | **Out of scope.** No RAG, no vector database, no embeddings. Nothing is retrieved: the council reasons only over the question and the voters' own answers. |
| **LLM09** | Misinformation | **Mitigated — this is the project's subject.** The whole point is countering single-model sycophancy. Divergences between voters are surfaced explicitly, the chairman output is framed as "recommendation, not verdict", and a degraded run exits **3**, not 0 — the caller is told that the contradiction was weaker than intended. Since 2026-08-14 that includes a **truncated** answer (`finish_reason='length'`), not only a fallen voter: a cut answer is present, valid and incomplete, so it passes every check about shape while saying less than it appears to. Ranking half-answers and synthesising them is misinformation produced by the tool itself. See `tests/test_cli.py::TestExitContract` and `tests/test_cli.py::TestTruncationIsNotSuccess`. |
| **LLM10** | Unbounded Consumption | **Mitigated.** Input capped at 4000 chars, per-stage token limits, a 50k cumulative ceiling that aborts the run (exit 4), 256KB response cap, 90s hard timeout, retry only on transient errors, plus a $5 spend cap and time expiry on the OpenRouter key. Since 2026-08-31 the retry wait can also be set by the provider via `Retry-After`: it is **capped at 30s** and validated to a non-negative integer, because a value chosen outside this trust boundary reaches `time.sleep`, and the scheduled run is bounded independently by `timeout-minutes: 15` on the job. See "Network & transport" below for the full wait policy. Was "LLM04 Model DoS" in the 2023 list. |

### MITRE ATLAS

The techniques that apply to a hosted-model consumer, and where they land here:

| ATLAS technique | Relevance | Where it is addressed |
|---|---|---|
| **AML.T0051** — LLM Prompt Injection | Direct and indirect | LLM01 above; the indirect variant is structural to a council (voter output becomes voter input) |
| **AML.T0057** — LLM Data Leakage | The question is sensitive by construction | LLM02: ZDR routing, no content in telemetry |
| **AML.T0053** — LLM Plugin Compromise | The skill wrapper | LLM06: local module only, no dynamic loading |
| **AML.T0049** — Exploit Public-Facing Application | The CI, not the CLI | LLM03: pinning + zizmor; the pipeline is the exposed surface, the tool has no inbound port |
| **AML.T0048** — External Harms | Bad advice acted upon | LLM09: divergences surfaced, exit 3 on degraded runs, "recommendation, not verdict" |

Not applicable: everything requiring model access or training influence (extraction, inversion, poisoning) — we neither host nor train a model.

## Provider routing & data retention

Every request carries an explicit routing constraint, in the JSON body:

```json
"provider": { "zdr": true, "data_collection": "deny", "allow_fallbacks": false }
```

- `zdr: true` — routes only to Zero Data Retention endpoints.
- `data_collection: "deny"` — excludes providers that store data non-transiently and may train on it.
- `allow_fallbacks: false` — **fail-closed**: if no compliant endpoint is available the call
  errors out. It never silently answers from a retaining provider.

**Why this lives in code.** The same guarantee can be set account-wide in the OpenRouter
dashboard, and it should be — but an account setting protects one user, is invisible to
anyone who clones this repo, and cannot be reviewed in a diff. `PROVIDER_ROUTING` in
`council/config.py` is versioned, testable, and travels with the code. Both, not either.

**Honest history (2026-07-26).** Before this change the constraint existed *only* as an
intention: the code sent no `provider` block, so OpenRouter was free to pick any endpoint
serving the model — the chairman model alone is offered by five different providers. The
account-level ZDR toggle was also found switched off. Earlier revisions of this file and of
`config.py` described "ZDR-eligible" models, which was true of the *models* and not of the
*routing*. The guarantee is now enforced per request and covered by tests
(`tests/test_routing.py`), and this paragraph stays as the record of what was actually
running before.

**Retention is not training.** These are separate controls. This project denies both, but
they fail differently: retention exposes data to a breach or a subpoena at the provider,
training embeds it in weights that outlive the request. For a tool that is asked about
careers and personal decisions, the second is the one that cannot be undone.

## Network & transport

- TLS 1.2+ via stdlib `urllib`; certificate chain validated by default
- Retry logic: exponential backoff only on the codes OpenRouter **documents** as transient —
  `408`, `429`, `500`, `502`, `503`, `524` (edge timeout), `529` (provider overloaded) — plus
  `URLError` and a malformed body. Everything else the API documents is a verdict about the
  request (`400`, `401`, `402`, `403`, `404`, `413`, `422`) and fails fast, to avoid wasting
  quota and masking the bug. *Corrected 2026-08-14: this line used to say "5xx, fail-fast on
  4xx", which was both too broad and too narrow — the set retried `504`, which OpenRouter does
  not document, and skipped `524`/`529`, which it emits exactly when a provider is under stress.*
- **Wait length: two mechanisms, and they are not the same one.** The short backoff
  `(1, 2, 4)` applies to every transient code. On `429` and `503` OpenRouter documents that it
  *"may include a standard HTTP `Retry-After` response header"*, and that hint is obeyed —
  **capped at 30s**, because the number is chosen by a party we route to without fallbacks and
  an hour-long hint would otherwise park a run for an hour. A `429` with no hint waits a
  20s fallback instead: a rate-limit window does not reopen in three seconds, which is all
  the short backoff actually spends. Anything that is not a plain non-negative integer is
  treated as absent — a negative would reach `time.sleep` and raise, ending the run.
  *Added 2026-08-31, after the weekly E2E lost the same seat to a Stage 2 `429` twice.*
- **Job wall clock.** `e2e.yml` carries `timeout-minutes: 15`. It had none, so it inherited
  GitHub's six-hour default: the waits above are per-attempt, and a provider answering `429`
  with a high hint stretches a run without any ceiling in the code stopping it. A budget
  counted in the client would be a second accounting destined to drift from the first.
- Response body size cap: **256KB** hard limit (defense against compromised-endpoint streaming)
- Hard request timeout: **90s** per HTTP call

## API key handling

- Stored in `.env` (gitignored, never committed)
- Validated at client init: must start with `sk-or-` (OpenRouter format)
- Redacted in `__repr__` to prevent accidental debug-print leak
- Never logged in error messages, never surfaced in stack traces
- Recommended: create the key with **spend cap** + **time expiry** set on OpenRouter dashboard

## Reporting

This is a personal project for portfolio purposes. If you find security issues, open a GitHub issue. For sensitive disclosures, contact the repository owner directly.

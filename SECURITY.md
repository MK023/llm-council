# Security Policy

## Threat model

This is a **single-user CLI tool** invoked locally (or wrapped by a Claude Code skill on the same machine). Threat model assumptions:

- Single user with full machine access (no multi-tenancy)
- No network exposure (does not run as a server, no inbound ports)
- No persistence of secrets beyond a local gitignored `.env`
- No autonomous agency (script invoked manually with explicit user question)

## Hardened against

Mapped to [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/):

| OWASP ID | Risk | Mitigation in this project |
|---|---|---|
| LLM01 | Prompt Injection | Server-side OpenRouter Workspace Guardrail (OWASP regex, Flag mode) + client-side fenced delimiters in Stage 2/3 prompts |
| LLM02 | Insecure Output Handling | Chairman synthesis is plain markdown text; never executed as code, shell, or SQL |
| LLM04 | Denial of Service | Input length cap (4000 chars), per-run token ceiling (50k), `$5` OpenRouter spend cap, 1h key time-expiry |
| LLM06 | Sensitive Info Disclosure | Server-side PII redaction (Email/Phone/SSN/Credit card) in Redact mode on OpenRouter Workspace; per-request ZDR routing with `data_collection: deny`, fail-closed (see below) |
| LLM07 | Insecure Plugin Design | Skill invokes only the local Python module; no external download/execution; API key from `.env` only |
| LLM08 | Excessive Agency | No autonomous rerun, escalation, or config modification based on output |
| LLM09 | Overreliance | Chairman output framed as "recommendation, not verdict" in skill instructions; divergences between voters surfaced explicitly |

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
- Retry logic: exponential backoff only on retryable errors (`429`, `5xx`, `URLError`); fail-fast on `4xx` auth/bad-request to avoid quota waste and mask bugs
- Response body size cap: **256KB** hard limit (defense against compromised-endpoint streaming)
- Hard request timeout: **90s** per HTTP call

## Out of scope

- **LLM03** (Training Data Poisoning) — we are a consumer, not a trainer
- **LLM05** (Supply Chain Vulnerabilities) — stdlib-only, no external pip dependencies
- **LLM10** (Model Theft) — no proprietary model; we are a gateway consumer

## API key handling

- Stored in `.env` (gitignored, never committed)
- Validated at client init: must start with `sk-or-` (OpenRouter format)
- Redacted in `__repr__` to prevent accidental debug-print leak
- Never logged in error messages, never surfaced in stack traces
- Recommended: create the key with **spend cap** + **time expiry** set on OpenRouter dashboard

## Reporting

This is a personal project for portfolio purposes. If you find security issues, open a GitHub issue. For sensitive disclosures, contact the repository owner directly.

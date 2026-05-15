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
| LLM06 | Sensitive Info Disclosure | Server-side PII redaction (Email/Phone/SSN/Credit card) in Redact mode on OpenRouter Workspace |
| LLM07 | Insecure Plugin Design | Skill invokes only the local Python module; no external download/execution; API key from `.env` only |
| LLM08 | Excessive Agency | No autonomous rerun, escalation, or config modification based on output |
| LLM09 | Overreliance | Chairman output framed as "recommendation, not verdict" in skill instructions; divergences between voters surfaced explicitly |

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

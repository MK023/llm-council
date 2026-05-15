# Changelog

All notable changes to this project are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

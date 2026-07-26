# Changelog

All notable changes to this project are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- **33 → 122 tests**, each new behaviour verified by mutation: break the code, watch the
  test go red.
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

# ADR 0001: Ports and adapters, with retries and timeouts in exactly one layer

Status: accepted · Date: 2026-10-26

## Context

AgentOps talks to an LLM provider (Groq or Gemini, plus a fake for CI and UI work), Postgres, a local embedding
model and an MCP subprocess. The same model is called from chat streaming, from two report nodes (`analyze`,
`draft`) and from proposal drafting. Each provider SDK has its own retry logic. If retries are stacked across SDK,
adapter and use-case layers, a single outage turns into a multiplied burst of attempts, and a deadline nobody can
predict. There is also a free-tier constraint: the Gemini API terms allow only Paid Services when the client is
offered to users in the EEA, UK or Switzerland, and free-tier content may be used for product improvement.

## Decision

- Application code depends only on `Protocol` ports in `agentops/domain/` (`ChatModel`,
  `ConversationRepository`, `Retriever`, `Embedder`, `DocumentRepository`, `ReportRunRepository`,
  `ProposalRepository`, `IssueReader`, `IssueWriter`). Adapters live in `agentops/infrastructure/`, and they are
  wired in `main.py`, `documents_wiring.py` and `tracker_wiring.py`.
- Provider adapters (`GroqChatModel`, `GeminiChatModel`) only translate SDK errors into `LLMError` subclasses. SDK
  retries are disabled (`max_retries=0`, `HttpRetryOptions(attempts=1)`).
- `ResilientChatModel` (`infrastructure/llm/resilience.py`) wraps every adapter, the fake included. It is the
  only place with a timeout (`LLM_TIMEOUT_S`, an inactivity timeout before the first chunk and between chunks) and
  with retries (`LLM_MAX_RETRIES`, exponential backoff with jitter, `Retry-After` honoured up to 10 s). It never
  retries after the first streamed token. Use cases add only *semantic* bounds: the whole-run `REPORT_TIMEOUT_S`,
  and one corrective retry for unparsable JSON (`ask_json`).
- Groq (via the `openai` SDK and its OpenAI-compatible endpoint) is the default provider because of the Gemini
  terms above. There is no automatic fallback between providers.

## Alternatives considered

- **Call SDKs directly from services.** Less code, but it couples prompts and use cases to one vendor, and it makes
  the deterministic fakes used by tests and `make eval` much harder to build.
- **Use the SDKs' built-in retries.** Each SDK behaves differently, and none of them knows when a stream has
  already emitted tokens, after which a retry would duplicate text.
- **A framework abstraction (LangChain chat models).** That would bring in a much larger dependency for one
  streaming method. LangGraph is used only where it adds value, in the report graph.
- **Provider fallback chain.** It would silently change which vendor sees the data, and it would make answers
  depend on which quota ran out.

## Consequences

- One `RetryPolicy` and one `LLM_TIMEOUT_S` describe all model calls, and `test_resilience.py` covers them.
- Swapping providers is a config change (`LLM_PROVIDER`, `LLM_MODEL`). Any OpenAI-compatible host can reuse
  `GroqChatModel` with another `base_url`.
- A wall-clock budget per call does not exist. A slow but steady stream can run past `LLM_TIMEOUT_S` in total; for
  reports, the run-level timeout covers this.
- The layering itself (`domain` imports nothing outward, `application` imports no infrastructure) is kept by
  convention and review, not by an import-linter. Only the write boundary has a structural test (ADR 0005).

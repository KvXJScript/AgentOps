# AgentOps

> Work in progress. A full-stack AI portfolio project: RAG with verifiable citations, a bounded LangGraph workflow,
> an MCP integration with human approval, running on free model tiers.

**Status:** Phase 1 is done: streaming chat with persisted history and a replaceable LLM provider (Groq by default,
Gemini optional). See [`docs/implementation-status.md`](docs/implementation-status.md) for what is verified.

## Stack

- **Backend:** FastAPI, SQLAlchemy (async), Postgres + pgvector, structlog. Layout: `api → application → domain ← infrastructure`.
- **Frontend:** Next.js (App Router), TypeScript, Tailwind, shadcn/ui. A server-side proxy keeps credentials off the browser.

## Run locally

```bash
docker compose up -d db
cp .env.example backend/.env        # set GROQ_API_KEY, or LLM_PROVIDER=fake to run without a key
cd backend && uv sync && make migrate && make dev     # :8000
cd frontend && npm install && npm run dev             # :3000
```

API contract: [`docs/api-contract.md`](docs/api-contract.md).

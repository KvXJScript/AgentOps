# AgentOps — frontend

Next.js 16 (App Router) · TypeScript strict · Tailwind v4 · shadcn/ui (radix-nova).

```bash
cp .env.example .env.local      # BACKEND_URL=http://localhost:8000 (server-only)
npm install
npm run dev                     # http://localhost:3000
```

The browser only talks to this app. `src/app/api/v1/[...path]/route.ts` proxies
`/api/v1/*` to `BACKEND_URL` and streams responses (SSE) through unbuffered.

| Script | |
|---|---|
| `npm run dev` / `build` / `start` | Next.js |
| `npm run lint` | ESLint (next/core-web-vitals + TS) |
| `npm run typecheck` | `next typegen && tsc --noEmit` |
| `npm test` | Vitest: SSE parser, API client error mapping, proxy handler |
| `npm run mock:backend` | **Dev-only** in-memory mock of the API contract on `MOCK_PORT` (default 8000). Message triggers: `/rate`, `/fail`, `/timeout`, `/noconf`, `/long`. Not part of the app build. |

Layout: `src/lib/api` (contract types, client, SSE parser, error copy),
`src/lib/server/proxy.ts` (proxy logic, unit-tested), `src/hooks` (chat state,
readiness polling, stick-to-bottom scroll), `src/components` (chat UI, sidebar).

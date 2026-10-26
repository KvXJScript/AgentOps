"use client";

import { useId, useState } from "react";
import { ChevronRightIcon, Loader2Icon, RotateCwIcon, ScanSearchIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { ApiError, describeError } from "@/lib/api/errors";
import { describeLocation, markerNumber } from "@/lib/api/citations";
import type { Citation, MessageRetrieval, RetrievedChunk } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type State =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: MessageRetrieval }
  | { status: "error"; error: ApiError };

function Stat({ label, value, mono = false }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] font-medium tracking-wide text-muted-foreground uppercase">{label}</dt>
      <dd className={cn("truncate text-xs font-medium", mono && "font-mono")} title={typeof value === "string" ? value : undefined}>
        {value}
      </dd>
    </div>
  );
}

/** Similarity bar on a 0..1 scale with the min_score threshold drawn as a tick. */
function ScoreBar({ score, minScore }: { score: number; minScore: number }) {
  const pct = Math.max(0, Math.min(1, score)) * 100;
  const tick = Math.max(0, Math.min(1, minScore)) * 100;
  const above = score >= minScore;
  return (
    <div className="flex items-center gap-2">
      <span className={cn("w-10 shrink-0 text-right font-mono text-xs tabular-nums", !above && "text-muted-foreground")}>
        {score.toFixed(3)}
      </span>
      <div className="relative h-1.5 w-16 shrink-0 rounded-full bg-muted sm:w-24" aria-hidden>
        <div
          className={cn("h-full rounded-full", above ? "bg-brand" : "bg-muted-foreground/40")}
          style={{ width: `${pct}%` }}
        />
        <span className="absolute -top-0.5 h-2.5 w-px bg-foreground/60" style={{ left: `${tick}%` }} />
      </div>
    </div>
  );
}

function ChunkRow({
  chunk,
  minScore,
  marker,
}: {
  chunk: RetrievedChunk;
  minScore: number;
  marker: string | null;
}) {
  const [open, setOpen] = useState(false);
  const location = describeLocation(chunk.location);
  const below = chunk.score < minScore;
  const cited = marker !== null;
  return (
    <li className={cn("grid grid-cols-[2rem_1fr] gap-x-2 px-3 py-2.5", below && "opacity-60")}>
      <span className="pt-0.5 font-mono text-xs text-muted-foreground tabular-nums" aria-label={`Rank ${chunk.rank}`}>
        #{chunk.rank}
      </span>
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <ScoreBar score={chunk.score} minScore={minScore} />
          {cited ? (
            <span className="inline-flex h-5 items-center gap-1 rounded-full border border-brand/30 bg-brand/10 px-2 text-[11px] font-medium">
              <span className="font-mono text-brand">S{markerNumber(marker)}</span> cited
            </span>
          ) : (
            <span className="inline-flex h-5 items-center rounded-full border px-2 text-[11px] text-muted-foreground">
              not cited
            </span>
          )}
          {below && <span className="text-[11px] text-muted-foreground">below threshold</span>}
        </div>
        <p className="truncate text-xs">
          <span className="font-medium">{chunk.document_title}</span>
          {location && <span className="text-muted-foreground"> · {location}</span>}
        </p>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className={cn(
            "block w-full rounded text-left text-xs leading-5 whitespace-pre-wrap text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
            !open && "line-clamp-2",
          )}
        >
          {chunk.excerpt || <span className="italic">No excerpt</span>}
        </button>
        <p className="font-mono text-[10px] text-muted-foreground/70">
          chunk {chunk.chunk_id.slice(0, 8)} · chars {chunk.location.char_start}–{chunk.location.char_end}
        </p>
      </div>
    </li>
  );
}

function InspectorBody({ data, citations }: { data: MessageRetrieval; citations: readonly Citation[] }) {
  const markerByChunk = new Map<string, string>();
  for (const c of citations) if (!markerByChunk.has(c.chunk_id)) markerByChunk.set(c.chunk_id, c.marker);
  const cited = new Set(data.cited_chunk_ids);
  const chunks = [...data.chunks].sort((a, b) => a.rank - b.rank);
  const above = chunks.filter((c) => c.score >= data.min_score).length;
  const docs = new Set(chunks.map((c) => c.document_id)).size;

  return (
    <div className="space-y-0">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-b px-3 py-2.5 sm:grid-cols-5">
        <Stat label="top_k" value={data.top_k} mono />
        <Stat label="min_score" value={data.min_score.toFixed(2)} mono />
        <Stat label="retrieved" value={`${chunks.length} from ${docs} doc${docs === 1 ? "" : "s"}`} />
        <Stat label="above / cited" value={`${above} / ${cited.size}`} mono />
        <Stat label="embedding" value={data.embedding_model} mono />
      </dl>
      {chunks.length === 0 ? (
        <p className="px-3 py-4 text-xs text-muted-foreground">
          No passages were retrieved — no ready documents matched the scope of this question.
        </p>
      ) : (
        <ol className="divide-y">
          {chunks.map((c) => (
            <ChunkRow
              key={c.chunk_id}
              chunk={c}
              minScore={data.min_score}
              marker={cited.has(c.chunk_id) ? (markerByChunk.get(c.chunk_id) ?? `S${c.rank}`) : null}
            />
          ))}
        </ol>
      )}
      <p className="border-t px-3 py-2 text-[11px] text-muted-foreground">
        Cosine similarity, higher is better. The tick marks <span className="font-mono">min_score</span>; without
        at least one passage above it the model is not called. Excerpts only — prompts are not exposed.
      </p>
    </div>
  );
}

/** Per-message debug view backed by `GET /messages/{id}/retrieval` (loaded on first open). */
export function RetrievalInspector({ messageId, citations }: { messageId: string; citations: readonly Citation[] }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<State>({ status: "idle" });
  const panelId = useId();

  const load = async () => {
    setState({ status: "loading" });
    try {
      setState({ status: "ready", data: await api.getMessageRetrieval(messageId) });
    } catch (err) {
      setState({
        status: "error",
        error: err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) }),
      });
    }
  };

  return (
    <div className="mt-3">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next && state.status === "idle") void load();
        }}
        className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 -ml-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <ChevronRightIcon className={cn("size-3.5 transition-transform", open && "rotate-90")} aria-hidden />
        <ScanSearchIcon className="size-3.5" aria-hidden />
        Sources &amp; retrieval
      </button>
      {open && (
        <div id={panelId} className="mt-1.5 overflow-hidden rounded-xl border bg-card" role="region" aria-label="Retrieval details">
          {state.status === "loading" || state.status === "idle" ? (
            <p className="flex items-center gap-2 px-3 py-4 text-xs text-muted-foreground" aria-busy="true">
              <Loader2Icon className="size-3.5 animate-spin" aria-hidden /> Loading retrieval trace…
            </p>
          ) : state.status === "error" ? (
            <div className="flex items-center justify-between gap-3 px-3 py-3 text-xs" role="alert">
              <span>
                <span className="font-medium">
                  {state.error.code === "not_found" ? "No retrieval trace for this message" : describeError(state.error).title}
                </span>
                <span className="ml-1.5 font-mono text-muted-foreground">{state.error.code}</span>
              </span>
              {state.error.code !== "not_found" && (
                <Button size="xs" variant="outline" onClick={() => void load()}>
                  <RotateCwIcon /> Retry
                </Button>
              )}
            </div>
          ) : (
            <InspectorBody data={state.data} citations={citations} />
          )}
        </div>
      )}
    </div>
  );
}

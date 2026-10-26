"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDownIcon, FileTextIcon, FileXIcon, Loader2Icon, MapPinIcon } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { ApiError, describeError } from "@/lib/api/errors";
import { describeLocation, markerNumber } from "@/lib/api/citations";
import type { Citation } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type FullText =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; text: string }
  | { status: "gone" }
  | { status: "error"; error: ApiError };

function CitationDetails({ citation }: { citation: Citation }) {
  const [full, setFull] = useState<FullText>({ status: "idle" });
  const location = describeLocation(citation.location);
  const deleted = citation.document_deleted || full.status === "gone";

  const loadFull = async () => {
    setFull({ status: "loading" });
    try {
      const chunk = await api.getChunk(citation.document_id, citation.chunk_id);
      setFull({ status: "ready", text: chunk.text });
    } catch (err) {
      if (err instanceof ApiError && err.code === "not_found") setFull({ status: "gone" });
      else
        setFull({
          status: "error",
          error: err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) }),
        });
    }
  };

  return (
    <div className="space-y-2.5">
      <div className="flex items-start gap-2">
        <span
          className={cn(
            "mt-px inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-md px-1 font-mono text-[11px] font-semibold",
            deleted ? "bg-muted text-muted-foreground" : "bg-brand/12 text-brand",
          )}
          aria-hidden
        >
          {markerNumber(citation.marker)}
        </span>
        <div className="min-w-0 flex-1">
          <p className={cn("text-sm leading-5 font-medium break-words", deleted && "text-muted-foreground line-through decoration-muted-foreground/50")}>
            {citation.document_title}
          </p>
          {location && (
            <p className="mt-0.5 flex items-start gap-1 text-xs text-muted-foreground">
              <MapPinIcon className="mt-0.5 size-3 shrink-0" aria-hidden />
              <span className="break-words">{location}</span>
            </p>
          )}
        </div>
      </div>

      {deleted && (
        <p className="flex items-start gap-1.5 rounded-md border border-dashed px-2 py-1.5 text-xs text-muted-foreground">
          <FileXIcon className="mt-px size-3.5 shrink-0" aria-hidden />
          <span>
            <span className="font-medium text-foreground">Source deleted.</span> The excerpt below was saved with this
            answer; the original document is no longer available.
          </span>
        </p>
      )}

      <blockquote className="scrollbar-thin max-h-56 overflow-y-auto rounded-md border-l-2 border-brand/40 bg-muted/50 py-1.5 pr-2 pl-2.5 text-[13px] leading-5 whitespace-pre-wrap text-foreground/90">
        {full.status === "ready" ? full.text : citation.excerpt || <span className="text-muted-foreground italic">No excerpt stored.</span>}
      </blockquote>

      <div className="flex items-center justify-between gap-2">
        {!deleted && full.status !== "ready" ? (
          <Button
            variant="ghost"
            size="xs"
            className="-ml-1.5"
            onClick={() => void loadFull()}
            disabled={full.status === "loading"}
          >
            {full.status === "loading" ? <Loader2Icon className="animate-spin" /> : <ChevronDownIcon />}
            Show full passage
          </Button>
        ) : (
          <span />
        )}
        {!deleted && (
          <Link
            href="/documents"
            className="inline-flex items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
          >
            <FileTextIcon className="size-3" aria-hidden /> Documents
          </Link>
        )}
      </div>
      {full.status === "error" && (
        <p role="alert" className="text-xs text-destructive">
          {describeError(full.error).title}
        </p>
      )}
    </div>
  );
}

/** Inline chip for a resolved `[Sn]` marker. */
export function CitationChip({ citation }: { citation: Citation }) {
  const n = markerNumber(citation.marker);
  const deleted = citation.document_deleted;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-citation-chip={citation.marker}
          aria-label={`Source ${n}: ${citation.document_title}${deleted ? " (deleted)" : ""}`}
          className={cn(
            "relative mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] -translate-y-px items-center before:absolute before:-inset-1.5 before:content-[''] justify-center rounded-[5px] px-1 align-middle font-mono text-[10.5px] leading-none font-semibold no-underline transition-colors",
            "focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none",
            deleted
              ? "border border-dashed border-muted-foreground/50 text-muted-foreground hover:bg-muted"
              : "bg-brand/12 text-brand hover:bg-brand/20 aria-expanded:bg-brand aria-expanded:text-white",
          )}
        >
          {n}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[22rem] max-w-[calc(100vw-2rem)] p-3" aria-label={`Source ${n}`}>
        <CitationDetails citation={citation} />
      </PopoverContent>
    </Popover>
  );
}

/** Compact "Sources" row under a grounded answer (one entry per cited document passage). */
export function SourceList({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null;
  return (
    <div className="mt-3">
      <p className="mb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Sources</p>
      <ul className="flex flex-wrap gap-1.5">
        {citations.map((c) => (
          <li key={c.marker}>
            <Popover>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className={cn(
                    "inline-flex max-w-[16rem] items-center gap-1.5 rounded-lg border bg-card py-1 pr-2 pl-1 text-xs transition-colors hover:bg-muted",
                    "focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none aria-expanded:bg-muted",
                  )}
                  aria-label={`Source ${markerNumber(c.marker)}: ${c.document_title}${c.document_deleted ? " (deleted)" : ""}`}
                >
                  <span
                    className={cn(
                      "inline-flex h-4.5 min-w-4.5 items-center justify-center rounded px-1 font-mono text-[10px] font-semibold",
                      c.document_deleted ? "bg-muted text-muted-foreground" : "bg-brand/12 text-brand",
                    )}
                    aria-hidden
                  >
                    {markerNumber(c.marker)}
                  </span>
                  <span className={cn("truncate", c.document_deleted && "text-muted-foreground line-through")}>
                    {c.document_title}
                  </span>
                  {describeLocation(c.location) && (
                    <span className="hidden shrink-0 text-muted-foreground sm:inline">
                      · {c.location.page != null ? `p. ${c.location.page}` : c.location.heading_path.at(-1)}
                    </span>
                  )}
                </button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-[22rem] max-w-[calc(100vw-2rem)] p-3">
                <CitationDetails citation={c} />
              </PopoverContent>
            </Popover>
          </li>
        ))}
      </ul>
    </div>
  );
}

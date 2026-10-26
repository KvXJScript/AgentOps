"use client";

import { AlertTriangleIcon, BookOpenIcon, CircleSlashIcon, Loader2Icon, OctagonXIcon, SearchXIcon, ShieldAlertIcon } from "lucide-react";
import { Markdown } from "./markdown";
import { SourceList } from "./citations";
import { RetrievalInspector } from "./retrieval-inspector";
import type { Citation, RetrievedChunk } from "@/lib/api/types";
import { BrandMark } from "@/components/layout/brand";
import { describeMessageErrorCode } from "@/lib/api/errors";
import type { UIMessage } from "@/hooks/use-chat";
import { cn } from "@/lib/utils";

export function TypingIndicator() {
  return (
    <span className="inline-flex items-center gap-1 py-2" role="status" aria-label="Assistant is typing">
      <span className="typing-dot size-1.5 rounded-full bg-muted-foreground" />
      <span className="typing-dot size-1.5 rounded-full bg-muted-foreground" />
      <span className="typing-dot size-1.5 rounded-full bg-muted-foreground" />
    </span>
  );
}

function StatusNote({ message }: { message: UIMessage }) {
  if (message.role !== "assistant" || message.status === "complete") return null;
  if (message.local === "stopped" || message.error_code === "cancelled") {
    return (
      <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <CircleSlashIcon className="size-3.5" aria-hidden />
        {message.local === "stopped" ? "Stopped by you" : "Stopped before it finished"}
      </p>
    );
  }
  if (message.status === "failed") {
    return (
      <p className="inline-flex items-center gap-1.5 rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-2 text-sm text-destructive">
        <OctagonXIcon className="size-4 shrink-0" aria-hidden />
        <span>
          <span className="font-medium">No response.</span>{" "}
          <span className="text-destructive/90">{describeMessageErrorCode(message.error_code)}</span>
        </span>
      </p>
    );
  }
  return (
    <p className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-warning/10 px-2 py-1 text-xs text-foreground/80">
      <AlertTriangleIcon className="size-3.5 text-warning" aria-hidden />
      Incomplete response
      {message.error_code ? <span className="text-muted-foreground">· {describeMessageErrorCode(message.error_code)}</span> : null}
    </p>
  );
}

export function MessageItem({ message }: { message: UIMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div
          className={cn(
            "max-w-[85%] rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-[15px] leading-7 whitespace-pre-wrap break-words text-secondary-foreground",
            message.local === "pending" && "opacity-70",
          )}
        >
          <span className="sr-only">You said: </span>
          {message.content}
        </div>
      </div>
    );
  }
  const grounding = message.grounding ?? "none";
  const citations = message.citations ?? [];
  // Persisted server ids only (local "stopped-…"/"failed-…" placeholders have no trace).
  const inspectable = grounding !== "none" && !message.local && !/^(stopped|failed|pending)-/.test(message.id);

  if (grounding === "insufficient_evidence") {
    // Contract: when retrieval is insufficient the model is NOT called (fixed answer, no model);
    // otherwise the model answered but cited nothing valid — keep the text, flag it.
    const modelAnswered = message.model !== null && message.content.trim().length > 0;
    return (
      <AssistantBubble
        content={modelAnswered ? message.content : ""}
        header={<InsufficientEvidence modelAnswered={modelAnswered} fixedText={modelAnswered ? null : message.content} />}
        contentClassName={modelAnswered ? "border-l-2 border-dashed border-warning/60 pl-3 text-foreground/85" : undefined}
        footer={
          <>
            <StatusNote message={message} />
            {inspectable && <RetrievalInspector messageId={message.id} citations={citations} />}
          </>
        }
      />
    );
  }

  return (
    <AssistantBubble
      content={message.content}
      citations={citations}
      footer={
        <>
          <StatusNote message={message} />
          {grounding === "grounded" && <SourceList citations={citations} />}
          {inspectable && <RetrievalInspector messageId={message.id} citations={citations} />}
        </>
      }
    />
  );
}

function InsufficientEvidence({ modelAnswered, fixedText }: { modelAnswered: boolean; fixedText: string | null }) {
  if (modelAnswered) {
    return (
      <div className="mb-2.5 flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs">
        <ShieldAlertIcon className="mt-px size-4 shrink-0 text-warning" aria-hidden />
        <p>
          <span className="font-medium">Not backed by your documents.</span>{" "}
          <span className="text-muted-foreground">
            This answer doesn&apos;t cite any retrieved passage, so it may come from the model&apos;s general knowledge.
            Verify before relying on it.
          </span>
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-dashed bg-muted/30 px-4 py-3">
      <p className="flex items-center gap-2 text-sm font-medium">
        <SearchXIcon className="size-4 text-muted-foreground" aria-hidden />
        Not enough evidence in your documents
      </p>
      <p className="mt-1 text-sm text-muted-foreground">
        {fixedText?.trim() ||
          "No passage was relevant enough to answer from, so no answer was generated."}
      </p>
      <p className="mt-2 text-xs text-muted-foreground">
        Try rephrasing with terms from the document, widening the document scope, or uploading the source that covers
        this.
      </p>
    </div>
  );
}

/** Shown above a streaming answer when the send used documents. */
export function RetrievalStatus({
  retrieval,
}: {
  retrieval: { chunks: RetrievedChunk[]; sufficient: boolean } | null;
}) {
  if (!retrieval) {
    return (
      <p className="mb-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
        <Loader2Icon className="size-3.5 animate-spin" aria-hidden /> Searching your documents…
      </p>
    );
  }
  const docs = new Set(retrieval.chunks.map((c) => c.document_id)).size;
  if (!retrieval.sufficient) {
    return (
      <p className="mb-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
        <SearchXIcon className="size-3.5" aria-hidden /> No passage above the relevance threshold
      </p>
    );
  }
  return (
    <p className="mb-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
      <BookOpenIcon className="size-3.5 text-brand" aria-hidden />
      Using {retrieval.chunks.length} {retrieval.chunks.length === 1 ? "passage" : "passages"} from {docs}{" "}
      {docs === 1 ? "document" : "documents"}
    </p>
  );
}

export function AssistantBubble({
  content,
  streaming = false,
  header,
  footer,
  citations,
  contentClassName,
}: {
  content: string;
  streaming?: boolean;
  header?: React.ReactNode;
  footer?: React.ReactNode;
  /** Resolved citations; only these markers render as chips. */
  citations?: readonly Citation[];
  contentClassName?: string;
}) {
  return (
    <div className="flex gap-3">
      <div className="mt-0.5 shrink-0" aria-hidden>
        <BrandMark className="size-7" />
      </div>
      <div className="min-w-0 flex-1 pt-0.5">
        <span className="sr-only">Assistant said: </span>
        {header}
        {content ? <Markdown content={content} citations={citations} className={contentClassName} /> : null}
        {streaming && <TypingIndicator />}
        {footer}
      </div>
    </div>
  );
}

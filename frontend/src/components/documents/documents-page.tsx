"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  CloudUploadIcon,
  FileTextIcon,
  InfoIcon,
  MessageSquareIcon,
  RotateCwIcon,
  Trash2Icon,
  UploadIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { RelativeTime } from "@/components/ui/relative-time";
import { PageIntro } from "@/components/layout/page-intro";
import { PageShell } from "@/components/layout/page-shell";
import { ErrorCallout, RequestId } from "@/components/chat/error-callout";
import { DocumentStatusBadge, DocumentTypeIcon } from "./document-status";
import { useDocuments, type UploadItem, type UploadOutcome } from "@/hooks/use-documents";
import { ApiError, describeDocumentFailure, describeError } from "@/lib/api/errors";
import { ACCEPT_ATTR, EXTENSION_MEDIA_TYPE, MAX_UPLOAD_BYTES, fileExtension, formatBytes } from "@/lib/api/upload";
import type { Document } from "@/lib/api/types";
import { cn } from "@/lib/utils";


function errorToast(prefix: string, err: unknown) {
  const e = err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) });
  const d = describeError(e);
  toast.error(prefix, { description: e.requestId ? `${d.title} · request_id ${e.requestId}` : d.title });
}

// --- Drop zone ---------------------------------------------------------------

function DropZone({ onFiles, compact }: { onFiles: (files: File[]) => void; compact: boolean }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [over, setOver] = useState(false);
  const depth = useRef(0);

  return (
    <div
      onDragEnter={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        depth.current += 1;
        setOver(true);
      }}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1);
        if (depth.current === 0) setOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        depth.current = 0;
        setOver(false);
        const files = Array.from(e.dataTransfer.files);
        if (files.length) onFiles(files);
      }}
      className={cn(
        "relative rounded-2xl border-2 border-dashed transition-colors",
        over ? "border-brand bg-brand/5" : "border-border bg-muted/20 hover:border-foreground/20",
      )}
    >
      <input
        ref={inputRef}
        id="document-file-input"
        type="file"
        multiple
        accept={ACCEPT_ATTR}
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) onFiles(files);
        }}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        aria-describedby="dropzone-hint"
        className={cn(
          "flex w-full flex-col items-center justify-center gap-2 rounded-2xl text-center outline-none focus-visible:ring-3 focus-visible:ring-ring/40",
          compact ? "px-4 py-6" : "px-6 py-12",
        )}
      >
        <span
          className={cn(
            "inline-flex items-center justify-center rounded-xl bg-brand/10 text-brand ring-1 ring-brand/20 ring-inset",
            compact ? "size-9" : "size-12",
          )}
          aria-hidden
        >
          <CloudUploadIcon className={compact ? "size-4.5" : "size-6"} />
        </span>
        <span className="text-sm font-medium">
          {over ? "Drop to upload" : (
            <>
              Drag files here or <span className="text-brand underline underline-offset-2">browse</span>
            </>
          )}
        </span>
        <span id="dropzone-hint" className="text-xs text-muted-foreground">
          Markdown (.md), plain text (.txt) or PDF · up to {Math.round(MAX_UPLOAD_BYTES / (1024 * 1024))} MB each
        </span>
      </button>
    </div>
  );
}

function uploadMediaType(name: string): string {
  const ext = fileExtension(name);
  return ext in EXTENSION_MEDIA_TYPE ? EXTENSION_MEDIA_TYPE[ext as keyof typeof EXTENSION_MEDIA_TYPE] : ext || "file";
}

// --- Rows --------------------------------------------------------------------

function UploadRow({
  item,
  onRetry,
  onDismiss,
}: {
  item: UploadItem;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const pct = Math.round(item.progress * 100);
  const error = item.error;
  const d = error ? describeError(error) : null;
  // Client-side validation errors carry a precise message; server ones use contract copy.
  const detail = error && error.status === null && error.requestId === null ? error.message : d?.description;
  return (
    <li
      className={cn(
        "flex items-start gap-3 rounded-xl border px-3.5 py-3",
        error ? "border-destructive/25 bg-destructive/5" : "bg-card",
      )}
    >
      <DocumentTypeIcon mediaType={uploadMediaType(item.file.name)} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium">{item.file.name}</p>
          <span className="shrink-0 text-xs text-muted-foreground">{formatBytes(item.file.size)}</span>
        </div>
        {error ? (
          <div className="mt-0.5 space-y-0.5 text-xs">
            <p className="font-medium text-destructive">{d!.title}</p>
            <p className="text-muted-foreground">{detail}</p>
            {error.requestId && <RequestId id={error.requestId} className="-ml-1" />}
          </div>
        ) : (
          <div className="mt-1.5">
            <div
              className="h-1.5 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-label={`Uploading ${item.file.name}`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={item.phase === "uploading" ? pct : undefined}
              aria-valuetext={
                item.phase === "queued" ? "Queued" : item.phase === "processing" ? "Processing" : `${pct}%`
              }
            >
              <div
                className={cn(
                  "h-full rounded-full bg-brand transition-[width] duration-200",
                  item.phase === "processing" && "w-full animate-pulse motion-reduce:animate-none",
                  item.phase === "queued" && "w-0",
                )}
                style={item.phase === "uploading" ? { width: `${pct}%` } : undefined}
              />
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {item.phase === "queued"
                ? "Waiting…"
                : item.phase === "uploading"
                  ? `Uploading · ${pct}%`
                  : "Processing · extracting text, chunking and embedding…"}
            </p>
          </div>
        )}
      </div>
      {error && (
        <div className="flex shrink-0 items-center gap-1">
          {d!.retryable && (
            <Button size="sm" variant="outline" onClick={onRetry}>
              <RotateCwIcon /> Retry
            </Button>
          )}
          <Button size="icon-sm" variant="ghost" aria-label={`Dismiss ${item.file.name}`} onClick={onDismiss}>
            <XIcon />
          </Button>
        </div>
      )}
    </li>
  );
}

function DocumentRow({
  doc,
  busy,
  highlighted,
  onRetry,
  onDelete,
}: {
  doc: Document;
  busy: boolean;
  highlighted: boolean;
  onRetry: () => void;
  onDelete: () => void;
}) {
  const failure = doc.status === "failed" ? describeDocumentFailure(doc.error_code) : null;
  const titleDiffers = doc.title !== doc.filename && !doc.filename.startsWith(`${doc.title}.`);
  return (
    <li
      className={cn(
        "group/doc flex items-start gap-3 rounded-xl border bg-card px-3.5 py-3 transition-[box-shadow,border-color] duration-500",
        highlighted && "border-brand/50 ring-3 ring-brand/15",
      )}
    >
      <DocumentTypeIcon mediaType={doc.media_type} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <p className="min-w-0 truncate text-sm font-medium" title={doc.title}>
            {doc.title}
          </p>
          <DocumentStatusBadge status={doc.status} />
        </div>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {titleDiffers && <span className="font-mono">{doc.filename} · </span>}
          {formatBytes(doc.size_bytes)}
          {doc.status === "ready" && (
            <>
              {" · "}
              {doc.chunk_count} {doc.chunk_count === 1 ? "chunk" : "chunks"}
            </>
          )}
          {" · "}
          <RelativeTime iso={doc.created_at} />
        </p>
        {failure && (
          <p className="mt-1 text-xs">
            <span className="font-medium text-destructive">{failure.label}.</span>{" "}
            <span className="text-muted-foreground">{failure.hint}</span>
            {doc.error_code && <span className="ml-1.5 font-mono text-[11px] text-muted-foreground">{doc.error_code}</span>}
          </p>
        )}
        {doc.status === "ready" && doc.embedding_model && (
          <p className="mt-0.5 hidden font-mono text-[11px] text-muted-foreground/80 sm:block">{doc.embedding_model}</p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {doc.status === "failed" && (
          <Button
            size="sm"
            variant={failure?.canRetry ? "outline" : "ghost"}
            onClick={onRetry}
            disabled={busy}
            aria-label={`Retry processing “${doc.title}”`}
          >
            <RotateCwIcon className={cn(busy && "animate-spin")} /> {busy ? "Retrying…" : "Retry"}
          </Button>
        )}
        <Button
          size="icon-sm"
          variant="ghost"
          className="hover:text-destructive"
          aria-label={`Delete “${doc.title}”`}
          title="Delete"
          disabled={busy}
          onClick={onDelete}
        >
          <Trash2Icon />
        </Button>
      </div>
    </li>
  );
}

function ListSkeleton() {
  return (
    <ul className="space-y-2" aria-busy="true" aria-label="Loading documents">
      {[0, 1, 2].map((i) => (
        <li key={i} className="flex items-center gap-3 rounded-xl border px-3.5 py-3">
          <Skeleton className="size-9 rounded-lg" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        </li>
      ))}
    </ul>
  );
}

function EmptyDocuments() {
  return (
    <div className="rounded-2xl border bg-card px-6 py-8">
      <div className="flex items-start gap-3">
        <FileTextIcon className="mt-0.5 size-5 shrink-0 text-brand" aria-hidden />
        <div className="space-y-3 text-sm">
          <div>
            <h2 className="font-medium">No documents yet</h2>
            <p className="mt-1 text-muted-foreground">
              Upload the material you want answers grounded in — product specs, meeting notes, policies, research
              papers, README files.
            </p>
          </div>
          <ul className="list-disc space-y-1 pl-4 text-muted-foreground marker:text-muted-foreground/60">
            <li>
              <span className="text-foreground">Markdown and text</span> keep their heading structure, so citations
              show the section they came from.
            </li>
            <li>
              <span className="text-foreground">PDFs</span> need selectable text; scanned images without OCR can&apos;t
              be read.
            </li>
            <li>
              In chat, turn on <span className="text-foreground">Use my documents</span> and answers cite their sources
              as <span className="rounded border bg-muted px-1 font-mono text-[11px]">S1</span> chips you can open.
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}

// --- Page --------------------------------------------------------------------

export function DocumentsPage() {
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ doc: Document } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Document | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [announce, setAnnounce] = useState("");

  const onOutcome = useCallback((o: UploadOutcome) => {
    switch (o.kind) {
      case "created":
        setAnnounce(`${o.document.title} is ready`);
        toast.success(`“${o.document.title}” is ready`, {
          description: `${o.document.chunk_count} ${o.document.chunk_count === 1 ? "chunk" : "chunks"} indexed`,
        });
        setHighlightId(o.document.id);
        break;
      case "duplicate":
        setAnnounce(`${o.document.title} was already uploaded`);
        setNotice({ doc: o.document });
        setHighlightId(o.document.id);
        break;
      case "failed": {
        const f = describeDocumentFailure(o.document.error_code);
        setAnnounce(`${o.document.title} failed: ${f.label}`);
        toast.error(`“${o.document.title}” couldn't be processed`, { description: f.hint });
        setHighlightId(o.document.id);
        break;
      }
      case "rejected":
        setAnnounce(`${o.file.name} was not uploaded: ${describeError(o.error).title}`);
        break;
    }
  }, []);

  const docs = useDocuments({ onOutcome });

  useEffect(() => {
    if (!highlightId) return;
    const t = window.setTimeout(() => setHighlightId(null), 2500);
    return () => window.clearTimeout(t);
  }, [highlightId]);

  const hasAny = docs.documents.length > 0 || docs.uploads.length > 0;
  const readyCount = docs.documents.filter((d) => d.status === "ready").length;

  return (
    <PageShell
      title="Documents"
      actions={
        readyCount > 0 ? (
          <Button asChild variant="outline" size="sm">
            <Link href="/?docs=1" aria-label="Ask your documents in chat">
              <MessageSquareIcon /> Ask<span className="hidden sm:inline">&nbsp;your documents</span>
            </Link>
          </Button>
        ) : null
      }
    >
      <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6 md:py-8">
        <PageIntro
          title="Your documents"
          description="Files are split into passages and embedded for search. Chat answers and reports cite the passages they used."
        />

        <DropZone onFiles={docs.addFiles} compact={hasAny} />

        <p className="sr-only" aria-live="polite">
          {announce}
        </p>

        {notice && (
          <div
            role="status"
            className="flex items-start gap-3 rounded-xl border border-brand/25 bg-brand/5 px-4 py-3 text-sm"
          >
            <InfoIcon className="mt-0.5 size-4 shrink-0 text-brand" aria-hidden />
            <p className="min-w-0 flex-1">
              <span className="font-medium">Already uploaded.</span>{" "}
              <span className="text-muted-foreground">
                “{notice.doc.title}” has identical content, so no new copy was made. The existing document is
                highlighted below.
              </span>
            </p>
            <Button size="icon-sm" variant="ghost" aria-label="Dismiss notice" onClick={() => setNotice(null)}>
              <XIcon />
            </Button>
          </div>
        )}

        {docs.uploads.length > 0 && (
          <section aria-label="Uploads in progress">
            <ul className="space-y-2">
              {docs.uploads.map((u) => (
                <UploadRow
                  key={u.localId}
                  item={u}
                  onRetry={() => docs.retryUpload(u.localId)}
                  onDismiss={() => docs.dismissUpload(u.localId)}
                />
              ))}
            </ul>
          </section>
        )}

        {docs.list.status === "error" && (
          <ErrorCallout error={docs.list.error} onRetry={() => void docs.refresh()} />
        )}

        <section aria-labelledby="doc-list-heading">
          <div className="mb-2 flex items-center justify-between">
            <h2 id="doc-list-heading" className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Library
              {docs.documents.length > 0 && (
                <span className="ml-1.5 tabular-nums">· {docs.documents.length}</span>
              )}
            </h2>
            {docs.list.status !== "loading" && (
              <Button variant="ghost" size="xs" onClick={() => void docs.refresh()} aria-label="Refresh documents">
                <RotateCwIcon /> Refresh
              </Button>
            )}
          </div>
          {docs.list.status === "loading" ? (
            <ListSkeleton />
          ) : docs.documents.length === 0 ? (
            docs.list.status === "ready" && docs.uploads.length === 0 ? <EmptyDocuments /> : null
          ) : (
            <ul className="space-y-2" aria-label="Documents">
              {docs.documents.map((d) => (
                <DocumentRow
                  key={d.id}
                  doc={d}
                  busy={docs.busyIds.has(d.id)}
                  highlighted={highlightId === d.id}
                  onRetry={() =>
                    void docs
                      .retryDocument(d.id)
                      .then((doc) => {
                        if (doc.status === "ready") toast.success(`“${doc.title}” is ready`);
                        else toast.error(`“${doc.title}” still couldn't be processed`, {
                          description: describeDocumentFailure(doc.error_code).hint,
                        });
                      })
                      .catch((err: unknown) => errorToast("Retry failed", err))
                  }
                  onDelete={() => setPendingDelete(d)}
                />
              ))}
            </ul>
          )}
        </section>

        {docs.list.status !== "loading" && (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <UploadIcon className="size-3.5" aria-hidden />
            Uploading the same file twice reuses the existing document.
          </p>
        )}
      </div>

      <AlertDialog open={pendingDelete !== null} onOpenChange={(open) => !open && !deleting && setPendingDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this document?</AlertDialogTitle>
            <AlertDialogDescription>
              “{pendingDelete?.title}” and its search index will be removed and it won&apos;t be used in new answers.
              Existing answers keep their quoted excerpts, marked as from a deleted source.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleting}
              onClick={async (e) => {
                e.preventDefault();
                if (!pendingDelete) return;
                setDeleting(true);
                try {
                  await docs.deleteDocument(pendingDelete.id);
                  toast.success("Document deleted");
                  setPendingDelete(null);
                } catch (err) {
                  errorToast("Couldn't delete the document", err);
                } finally {
                  setDeleting(false);
                }
              }}
            >
              {deleting ? "Deleting…" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageShell>
  );
}

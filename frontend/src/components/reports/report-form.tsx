"use client";

import { useState } from "react";
import Link from "next/link";
import { CheckIcon, Loader2Icon, SparklesIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ErrorCallout } from "@/components/chat/error-callout";
import { useReadyDocuments } from "@/components/chat/document-scope";
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { REPORT_QUESTION_MAX, REPORT_QUESTION_MIN, type ReportRun } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/** Whether a question can be submitted, and why not. */
export function questionProblem(question: string): string | null {
  const n = question.trim().length;
  if (n < REPORT_QUESTION_MIN) return `At least ${REPORT_QUESTION_MIN} characters`;
  if (n > REPORT_QUESTION_MAX) return `At most ${REPORT_QUESTION_MAX} characters`;
  return null;
}

export function ReportForm({ onCreated }: { onCreated: (run: ReportRun, requestId: string) => void }) {
  const [question, setQuestion] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const { state: docs, refresh: refreshDocs } = useReadyDocuments();

  const problem = questionProblem(question);
  const ready = docs.status === "ready" ? docs.items : [];
  const scope = selected.filter((id) => ready.some((d) => d.id === id));

  const submit = async () => {
    if (problem || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const { run, requestId } = await api.createReport({
        question: question.trim(),
        ...(scope.length ? { document_ids: scope } : {}),
      });
      setQuestion("");
      onCreated(run, requestId);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) }));
    } finally {
      setSubmitting(false);
    }
  };

  const toggle = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  return (
    <form
      className="space-y-3 rounded-xl border bg-card p-4"
      aria-label="New report"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="space-y-1.5">
        <label htmlFor="report-question" className="text-sm font-medium">
          What should the report answer?
        </label>
        <Textarea
          id="report-question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void submit();
            }
          }}
          maxLength={REPORT_QUESTION_MAX + 200}
          rows={4}
          placeholder="e.g. Summarise our remote-work policy: who is eligible, limits and approval steps."
          disabled={submitting}
          aria-describedby="report-question-hint"
        />
        <p id="report-question-hint" className="flex justify-between text-[11px] text-muted-foreground">
          <span>{question.trim() && problem ? problem : "Grounded in your documents, with citations."}</span>
          <span className={cn(question.trim().length > REPORT_QUESTION_MAX && "text-destructive")}>
            {question.trim().length}/{REPORT_QUESTION_MAX}
          </span>
        </p>
      </div>

      <fieldset className="space-y-1.5">
        <legend className="text-xs font-medium text-muted-foreground">Documents</legend>
        {docs.status === "loading" && <p className="text-xs text-muted-foreground">Loading documents…</p>}
        {docs.status === "error" && (
          <p className="text-xs text-muted-foreground">
            Couldn&apos;t load your documents — the report will search all ready documents.{" "}
            <button type="button" className="underline underline-offset-2" onClick={() => void refreshDocs()}>
              Retry
            </button>
          </p>
        )}
        {docs.status === "ready" && ready.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No ready documents yet.{" "}
            <Link href="/documents" className="underline underline-offset-2">
              Upload some
            </Link>{" "}
            so reports have evidence to cite.
          </p>
        )}
        {ready.length > 0 && (
          <>
            <p className="text-[11px] text-muted-foreground">
              {scope.length ? `${scope.length} selected` : `None selected = all ${ready.length} ready documents`}
            </p>
            <ul className="scrollbar-thin max-h-40 space-y-0.5 overflow-y-auto" aria-label="Document scope">
              {ready.map((d) => {
                const checked = scope.includes(d.id);
                return (
                  <li key={d.id}>
                    <button
                      type="button"
                      role="checkbox"
                      aria-checked={checked}
                      onClick={() => toggle(d.id)}
                      disabled={submitting}
                      className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-sm hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
                    >
                      <span
                        className={cn(
                          "inline-flex size-4 shrink-0 items-center justify-center rounded border",
                          checked ? "border-brand bg-brand text-white" : "border-input",
                        )}
                        aria-hidden
                      >
                        {checked && <CheckIcon className="size-3" />}
                      </span>
                      <span className="min-w-0 flex-1 truncate">{d.title}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </fieldset>

      {error && <ErrorCallout error={error} onRetry={() => void submit()} onDismiss={() => setError(null)} />}

      <Button type="submit" className="w-full" disabled={Boolean(problem) || submitting}>
        {submitting ? <Loader2Icon className="animate-spin" /> : <SparklesIcon />}
        {submitting ? "Starting…" : "Write report"}
      </Button>
    </form>
  );
}

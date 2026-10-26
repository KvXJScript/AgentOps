"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowLeftIcon, NotebookPenIcon } from "lucide-react";
import { toast } from "sonner";
import { PageIntro } from "@/components/layout/page-intro";
import { PageShell, scrollPageToTop } from "@/components/layout/page-shell";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api/client";
import { ApiError, describeError } from "@/lib/api/errors";
import type { ReportRun } from "@/lib/api/types";
import { useReportList, useReportRun } from "@/hooks/use-reports";
import { ProposeFollowUp } from "./propose-follow-up";
import { ReportForm } from "./report-form";
import { RunDetail } from "./run-detail";
import { RunList } from "./run-list";

const RUN_PARAM = "run";

function syncUrl(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set(RUN_PARAM, id);
  else url.searchParams.delete(RUN_PARAM);
  window.history.replaceState(window.history.state, "", url);
}

export function ReportsPage() {
  const list = useReportList();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // X-Request-ID of each POST made in this session, to show next to a failed run.
  const [requestIds, setRequestIds] = useState<Record<string, string>>({});
  const detail = useReportRun(selectedId, list.upsert);

  // Deep link: /reports?run=<id>. Read after mount (no Suspense/SSR involvement).
  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      const id = new URL(window.location.href).searchParams.get(RUN_PARAM);
      if (!cancelled && id) setSelectedId(id);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const select = useCallback((id: string | null) => {
    setSelectedId(id);
    syncUrl(id);
    scrollPageToTop();
  }, []);

  const onCreated = useCallback(
    (run: ReportRun, requestId: string) => {
      list.upsert(run);
      setRequestIds((prev) => ({ ...prev, [run.id]: requestId }));
      select(run.id);
    },
    [list, select],
  );

  const rerun = useCallback(
    async (run: ReportRun) => {
      try {
        const created = await api.createReport({
          question: run.question,
          ...(run.document_ids ? { document_ids: run.document_ids } : {}),
        });
        onCreated(created.run, created.requestId);
      } catch (err) {
        const e = err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) });
        toast.error("Couldn't start the report", {
          description: e.requestId ? `${describeError(e).title} · request_id ${e.requestId}` : describeError(e).title,
        });
      }
    },
    [onCreated],
  );

  const listItem =
    "items" in list.state ? (list.state.items.find((r) => r.id === selectedId) ?? null) : null;
  const shown = detail.run ?? listItem;

  return (
    <PageShell title="Reports">
      <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6 md:px-6">
        <PageIntro
          className={cn(selectedId && "hidden lg:flex")}
          title="Evidence-grounded reports"
          description="Ask a question and watch the workflow retrieve evidence, extract findings, draft and verify a cited report. From a finished report you can propose a follow-up issue for approval."
        />
        <div className="grid gap-6 lg:grid-cols-[minmax(0,21rem)_minmax(0,1fr)]">
          {/* On narrow screens the list and the detail are separate panes. */}
          <div className={cn("space-y-6", selectedId && "hidden lg:block")}>
            <ReportForm onCreated={onCreated} />
            <RunList
              state={list.state}
              selectedId={selectedId}
              onSelect={(run) => select(run.id)}
              onRetry={() => void list.refresh()}
            />
          </div>

          <section className={cn("min-w-0", !selectedId && "hidden lg:block")} aria-label="Report run">
            {selectedId && (
              <Button variant="ghost" size="sm" className="-ml-2 mb-3 lg:hidden" onClick={() => select(null)}>
                <ArrowLeftIcon /> All reports
              </Button>
            )}
            {selectedId ? (
              <RunDetail
                run={shown}
                pollError={detail.error}
                requestId={requestIds[selectedId] ?? null}
                onRetry={shown ? () => void rerun(shown) : undefined}
                followUp={shown ? <ProposeFollowUp key={shown.id} reportId={shown.id} /> : null}
              />
            ) : (
              <div className="flex h-full min-h-64 flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-8 text-center">
                <NotebookPenIcon className="size-6 text-muted-foreground" aria-hidden />
                <p className="text-sm font-medium">No report selected</p>
                <p className="max-w-sm text-xs leading-5 text-muted-foreground">
                  Write a report or pick a past one. Every citation is verified against your documents; a report
                  without valid evidence is not shipped.
                </p>
              </div>
            )}
          </section>
        </div>
      </div>
    </PageShell>
  );
}

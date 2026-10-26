import { FileXIcon, MapPinIcon } from "lucide-react";
import { CitationChip } from "@/components/chat/citations";
import { Markdown } from "@/components/chat/markdown";
import { describeLocation } from "@/lib/api/citations";
import type { Citation, Report } from "@/lib/api/types";
import { cn } from "@/lib/utils";

function ReferenceItem({ citation }: { citation: Citation }) {
  const location = describeLocation(citation.location);
  return (
    <li className="flex gap-3 rounded-lg border bg-card p-3" data-reference={citation.marker}>
      <div className="pt-0.5">
        <CitationChip citation={citation} />
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <p
          className={cn(
            "text-sm font-medium break-words",
            citation.document_deleted && "text-muted-foreground line-through",
          )}
        >
          {citation.document_title}
        </p>
        {location && (
          <p className="flex items-start gap-1 text-xs text-muted-foreground">
            <MapPinIcon className="mt-0.5 size-3 shrink-0" aria-hidden />
            <span className="break-words">{location}</span>
          </p>
        )}
        {citation.document_deleted && (
          <p className="flex items-center gap-1 text-xs text-muted-foreground">
            <FileXIcon className="size-3" aria-hidden /> Source deleted — excerpt kept with the report.
          </p>
        )}
        <blockquote className="border-l-2 border-brand/30 pl-2.5 text-[13px] leading-5 text-foreground/85">
          {citation.excerpt}
        </blockquote>
      </div>
    </li>
  );
}

/** The finished report: Markdown with `[S#]` chips plus the verified references. */
export function ReportView({ report }: { report: Report }) {
  const refs = report.references;
  return (
    <article className="space-y-6" aria-label="Report">
      <header className="space-y-3">
        <h2 className="text-xl font-semibold tracking-tight">{report.title}</h2>
        <Markdown content={report.summary} citations={refs} className="text-foreground/90" />
      </header>

      {report.sections.map((section, i) => (
        <section key={i} className="space-y-2" aria-label={section.heading}>
          <h3 className="text-base font-semibold tracking-tight">{section.heading}</h3>
          <Markdown content={section.body} citations={refs} />
        </section>
      ))}

      <section aria-label="References" className="space-y-2 border-t pt-5">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          References ({refs.length})
        </h3>
        <ol className="space-y-2">
          {refs.map((c) => (
            <ReferenceItem key={c.marker} citation={c} />
          ))}
        </ol>
      </section>
    </article>
  );
}

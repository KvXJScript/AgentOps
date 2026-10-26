import { CheckCircle2Icon, FileIcon, FileTextIcon, FileTypeIcon, OctagonXIcon } from "lucide-react";
import { StatusBadge, type StatusTone } from "@/components/ui/status-badge";
import type { DocumentMediaType, DocumentStatus } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const STATUS: Record<DocumentStatus, { label: string; tone: StatusTone; icon?: typeof CheckCircle2Icon; spin?: boolean }> = {
  ready: { label: "Ready", tone: "success", icon: CheckCircle2Icon },
  processing: { label: "Processing", tone: "working", spin: true },
  pending: { label: "Queued", tone: "neutral", spin: true },
  failed: { label: "Failed", tone: "danger", icon: OctagonXIcon },
};

export function DocumentStatusBadge({ status, className }: { status: DocumentStatus; className?: string }) {
  const s = STATUS[status] ?? STATUS.pending;
  return (
    <StatusBadge tone={s.tone} icon={s.icon} spin={s.spin} className={className} data-document-status={status}>
      {s.label}
    </StatusBadge>
  );
}

export function DocumentTypeIcon({ mediaType, className }: { mediaType: DocumentMediaType | string; className?: string }) {
  const Icon = mediaType === "application/pdf" ? FileTypeIcon : mediaType === "text/markdown" ? FileTextIcon : FileIcon;
  const label =
    mediaType === "application/pdf"
      ? "PDF"
      : mediaType === "text/markdown"
        ? "MD"
        : mediaType === "text/plain"
          ? "TXT"
          : mediaType.slice(0, 4).toUpperCase() || "FILE";
  return (
    <span
      className={cn(
        "relative inline-flex size-9 shrink-0 items-center justify-center rounded-lg border bg-muted/50 text-muted-foreground",
        className,
      )}
      aria-hidden
    >
      <Icon className="size-4" />
      <span className="absolute -bottom-1.5 rounded border bg-background px-1 font-mono text-[9px] leading-3.5 font-medium">
        {label}
      </span>
    </span>
  );
}

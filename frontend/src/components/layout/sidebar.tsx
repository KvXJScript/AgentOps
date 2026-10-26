"use client";

import { useState } from "react";
import {
  CheckSquareIcon,
  FileTextIcon,
  MessageSquareIcon,
  NotebookPenIcon,
  PlusIcon,
  RotateCwIcon,
  Trash2Icon,
} from "lucide-react";
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
import { Brand } from "./brand";
import { StatusIndicator } from "./status-indicator";
import { ThemeToggle } from "./theme-toggle";
import { describeError } from "@/lib/api/errors";
import type { Conversation } from "@/lib/api/types";
import type { ListState } from "@/hooks/use-chat";
import type { ReadyState } from "@/hooks/use-ready";
import { cn } from "@/lib/utils";

/** Upcoming product areas; shown disabled until their phase ships. */
const NAV = [
  { label: "Chat", icon: MessageSquareIcon, enabled: true },
  { label: "Documents", icon: FileTextIcon, enabled: false },
  { label: "Reports", icon: NotebookPenIcon, enabled: false },
  { label: "Approvals", icon: CheckSquareIcon, enabled: false },
] as const;

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.round(diff / 60_000);
  if (min < 1) return "now";
  if (min < 60) return `${min}m`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h}h`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d}d`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export interface SidebarProps {
  list: ListState;
  conversations: Conversation[];
  activeId: string | null;
  streamingId: string | null;
  ready: ReadyState;
  onRefreshReady: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => Promise<void>;
  onRetryList: () => void;
}

export function Sidebar(props: SidebarProps) {
  const { list, conversations, activeId, streamingId, ready, onRefreshReady, onNew, onSelect, onDelete, onRetryList } = props;
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState(false);

  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex h-14 shrink-0 items-center justify-between px-4">
        <Brand />
      </div>

      <nav aria-label="Primary" className="px-2">
        <ul className="space-y-0.5">
          {NAV.map(({ label, icon: Icon, enabled }) => (
            <li key={label}>
              {enabled ? (
                <span
                  aria-current="page"
                  className="flex h-8 items-center gap-2.5 rounded-md bg-sidebar-accent px-2.5 text-sm font-medium text-sidebar-accent-foreground"
                >
                  <Icon className="size-4" aria-hidden /> {label}
                </span>
              ) : (
                <span
                  aria-disabled="true"
                  title={`${label} is coming soon`}
                  className="flex h-8 cursor-not-allowed items-center gap-2.5 rounded-md px-2.5 text-sm text-muted-foreground/70"
                >
                  <Icon className="size-4" aria-hidden /> {label}
                  <span className="ml-auto rounded border px-1.5 text-[10px] tracking-wide uppercase">Soon</span>
                </span>
              )}
            </li>
          ))}
        </ul>
      </nav>

      <div className="mt-5 flex items-center justify-between px-4 pb-1.5">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Conversations</h2>
        <Button variant="ghost" size="icon-xs" onClick={onNew} aria-label="New conversation" title="New conversation">
          <PlusIcon />
        </Button>
      </div>
      <div className="px-2 pb-2">
        <Button variant="outline" className="w-full justify-start bg-background" onClick={onNew}>
          <PlusIcon /> New chat
        </Button>
      </div>

      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {list.status === "loading" ? (
          <div className="space-y-1.5 px-1 pt-1" aria-busy="true" aria-label="Loading conversations">
            {[72, 56, 84, 64, 48].map((w, i) => (
              <Skeleton key={i} className="h-7" style={{ width: `${w}%` }} />
            ))}
          </div>
        ) : (
          <>
            {list.status === "error" && (
              <div role="alert" className="mx-1 mb-2 rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-xs">
                <p className="font-medium">{describeError(list.error).title}</p>
                <Button variant="outline" size="xs" className="mt-2" onClick={onRetryList}>
                  <RotateCwIcon /> Retry
                </Button>
              </div>
            )}
            {conversations.length === 0 && list.status === "ready" ? (
              <p className="px-3 py-6 text-center text-xs leading-5 text-muted-foreground">
                No conversations yet.
                <br />
                Start one to see it here.
              </p>
            ) : (
              <ul className="space-y-0.5" aria-label="Conversations">
                {conversations.map((c) => {
                  const active = c.id === activeId;
                  return (
                    <li key={c.id} className="group/item relative">
                      <button
                        type="button"
                        onClick={() => onSelect(c.id)}
                        aria-current={active ? "true" : undefined}
                        className={cn(
                          "flex h-8 w-full items-center gap-2 rounded-md pr-8 pl-2.5 text-left text-sm transition-colors",
                          "hover:bg-sidebar-accent/70 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
                          active ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground" : "text-sidebar-foreground/85",
                        )}
                      >
                        {streamingId === c.id && (
                          <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-brand" aria-label="Responding" />
                        )}
                        <span className="truncate">{c.title}</span>
                        <span className="ml-auto shrink-0 text-[11px] text-muted-foreground group-hover/item:opacity-0 group-focus-within/item:opacity-0">
                          {relativeTime(c.updated_at)}
                        </span>
                      </button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`Delete conversation “${c.title}”`}
                        className="absolute top-1 right-1 opacity-0 group-hover/item:opacity-100 focus-visible:opacity-100 hover:text-destructive"
                        onClick={() => setPendingDelete(c)}
                      >
                        <Trash2Icon />
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
          </>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1 border-t border-sidebar-border px-2 py-2">
        <StatusIndicator state={ready} onRefresh={onRefreshReady} />
        <ThemeToggle />
      </div>

      <AlertDialog open={pendingDelete !== null} onOpenChange={(open) => !open && !deleting && setPendingDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              “{pendingDelete?.title}” and all of its messages will be permanently deleted. This can’t be undone.
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
                  await onDelete(pendingDelete.id);
                  setPendingDelete(null);
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
    </div>
  );
}

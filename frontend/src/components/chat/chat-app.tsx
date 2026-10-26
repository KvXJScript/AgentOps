"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDownIcon, MenuIcon, XIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Sidebar } from "@/components/layout/sidebar";
import { StubModelBadge } from "@/components/layout/status-indicator";
import { Composer } from "./composer";
import { EmptyState } from "./empty-state";
import { ErrorCallout } from "./error-callout";
import { AssistantBubble, MessageItem } from "./message-item";
import { useChat } from "@/hooks/use-chat";
import { isStubModel, useReady } from "@/hooks/use-ready";
import { useStickToBottom } from "@/hooks/use-stick-to-bottom";
import { ApiError, describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils";

function ThreadSkeleton() {
  return (
    <div className="mx-auto w-full max-w-3xl space-y-8 px-4 py-8" aria-busy="true" aria-label="Loading messages">
      <div className="flex justify-end">
        <Skeleton className="h-10 w-1/2 rounded-2xl" />
      </div>
      <div className="flex gap-3">
        <Skeleton className="size-7 rounded-lg" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-11/12" />
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      </div>
      <div className="flex justify-end">
        <Skeleton className="h-10 w-1/3 rounded-2xl" />
      </div>
    </div>
  );
}

export function ChatApp() {
  const chat = useChat();
  const ready = useReady();
  const [draft, setDraft] = useState("");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const thread = chat.activeThread;
  const messages = thread?.messages ?? [];
  const liveStream = chat.stream && chat.stream.conversationId === chat.activeId ? chat.stream : null;
  const busy = chat.isStreaming;

  const { ref: scrollRef, onScroll, atBottom, scrollToBottom } = useStickToBottom<HTMLDivElement>(
    `${chat.activeId}:${messages.length}:${liveStream?.content.length ?? -1}`,
  );

  const readyData = ready.kind === "ok" ? ready.data : null;
  const stub = readyData ? isStubModel(readyData.llm?.model, readyData.llm?.provider) : false;
  const modelMissing = readyData?.checks.llm === "not_configured";

  // Focus the composer whenever a stream finishes or the conversation changes.
  useEffect(() => {
    if (!busy) textareaRef.current?.focus();
  }, [busy, chat.activeId]);

  // Escape closes the mobile navigation drawer.
  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMobileNavOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen]);

  const submit = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content) return;
      setDraft("");
      scrollToBottom();
      const accepted = await chat.send(content);
      // Pre-stream failure: nothing was stored, give the text back to the user.
      if (!accepted) setDraft((d) => (d ? d : content));
    },
    [chat, scrollToBottom],
  );

  const newChat = useCallback(() => {
    chat.selectConversation(null);
    setMobileNavOpen(false);
    textareaRef.current?.focus();
  }, [chat]);

  const onDelete = useCallback(
    async (id: string) => {
      try {
        await chat.deleteConversation(id);
        toast.success("Conversation deleted");
      } catch (err) {
        const d = describeError(err instanceof ApiError ? err : { code: "unknown_error" });
        toast.error("Couldn't delete the conversation", {
          description: err instanceof ApiError && err.requestId ? `${d.title} · request_id ${err.requestId}` : d.title,
        });
      }
    },
    [chat],
  );

  const sidebar = (
    <Sidebar
      list={chat.list}
      conversations={chat.conversations}
      activeId={chat.activeId}
      streamingId={chat.stream?.conversationId ?? null}
      ready={ready}
      onRefreshReady={ready.refresh}
      onNew={newChat}
      onSelect={(id) => {
        chat.selectConversation(id);
        setMobileNavOpen(false);
      }}
      onDelete={onDelete}
      onRetryList={() => void chat.refreshList()}
    />
  );

  const title = chat.activeId
    ? (chat.activeConversation?.title ?? (thread?.status === "loading" ? "" : "Conversation"))
    : "New conversation";

  const isEmptyThread = !chat.activeId || (thread?.status === "ready" && messages.length === 0 && !liveStream);
  const noConversations = chat.list.status === "ready" && chat.conversations.length === 0;

  let body: React.ReactNode;
  if (thread?.status === "loading" && messages.length === 0) {
    body = <ThreadSkeleton />;
  } else if (thread?.status === "error" && messages.length === 0) {
    body = (
      <div className="mx-auto w-full max-w-3xl px-4 py-10">
        <ErrorCallout
          error={thread.error}
          onRetry={chat.activeId ? () => void chat.reloadThread(chat.activeId!) : undefined}
        />
        {thread.error.code === "not_found" && (
          <Button variant="outline" className="mt-4" onClick={newChat}>
            Start a new conversation
          </Button>
        )}
      </div>
    );
  } else if (isEmptyThread) {
    body = (
      <EmptyState
        title={noConversations ? "Welcome to AgentOps" : "How can I help?"}
        subtitle={
          noConversations
            ? "Start your first conversation. Answers stream in real time and every conversation is saved."
            : "Ask a question or pick a suggestion to get started."
        }
        disabled={busy}
        onPick={(p) => void submit(p)}
      />
    );
  } else {
    body = (
      <div
        role="log"
        aria-label="Messages"
        aria-busy={liveStream ? true : undefined}
        className="mx-auto w-full max-w-3xl space-y-7 px-4 pt-6 pb-10"
      >
        {messages.map((m) => (
          <MessageItem key={m.id} message={m} />
        ))}
        {liveStream && (
          <div aria-live="off">
            <AssistantBubble content={liveStream.content} streaming />
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex h-dvh w-full">
      {/* Desktop sidebar */}
      <aside className="hidden w-[272px] shrink-0 border-r border-sidebar-border md:block" aria-label="Sidebar">
        {sidebar}
      </aside>

      {/* Mobile drawer */}
      {mobileNavOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/40 backdrop-blur-[1px]"
            aria-label="Close navigation"
            onClick={() => setMobileNavOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 w-[85%] max-w-[300px] border-r shadow-xl" aria-label="Sidebar">
            {sidebar}
            <Button
              variant="ghost"
              size="icon-sm"
              className="absolute top-3 right-3"
              aria-label="Close navigation"
              onClick={() => setMobileNavOpen(false)}
            >
              <XIcon />
            </Button>
          </aside>
        </div>
      )}

      <main className="relative flex min-w-0 flex-1 flex-col bg-background">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-3 md:px-5">
          <Button
            variant="ghost"
            size="icon-sm"
            className="md:hidden"
            aria-label="Open navigation"
            onClick={() => setMobileNavOpen(true)}
          >
            <MenuIcon />
          </Button>
          <h1 className="min-w-0 truncate text-sm font-medium">{title || <Skeleton className="h-4 w-40" />}</h1>
          <div className="ml-auto flex items-center gap-2">
            {stub && <StubModelBadge />}
            {readyData?.llm && !stub && (
              <span className="hidden font-mono text-xs text-muted-foreground sm:inline">{readyData.llm.model}</span>
            )}
          </div>
        </header>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="scrollbar-thin relative flex min-h-0 flex-1 flex-col overflow-y-auto"
        >
          <div className="flex flex-1 flex-col">{body}</div>
        </div>

        {!atBottom && !isEmptyThread && (
          <div className="pointer-events-none absolute inset-x-0 bottom-36 flex justify-center">
            <Button
              variant="outline"
              size="sm"
              className="pointer-events-auto rounded-full shadow-sm"
              onClick={() => scrollToBottom("smooth")}
            >
              <ArrowDownIcon /> Jump to latest
            </Button>
          </div>
        )}

        <div className="shrink-0 px-3 pt-2 pb-3 md:px-5 md:pb-5">
          {modelMissing && !chat.sendError && (
            <p className="mx-auto mb-2 max-w-3xl text-center text-xs text-muted-foreground">
              {describeError({ code: "llm_not_configured" }).title}. Messages will fail until one is set up.
            </p>
          )}
          {chat.sendError && (
            <ErrorCallout
              className="mx-auto mb-3 max-w-3xl"
              error={chat.sendError.error}
              onRetry={() => {
                const content = chat.sendError!.content;
                if (chat.sendError!.preStream) setDraft("");
                void submit(content);
              }}
              onDismiss={chat.dismissError}
            />
          )}
          <Composer
            value={draft}
            onChange={setDraft}
            onSubmit={() => void submit(draft)}
            onStop={chat.stop}
            streaming={busy}
            autoFocus
            textareaRef={textareaRef}
          />
          <p className={cn("mx-auto mt-2 max-w-3xl text-center text-[11px] text-muted-foreground")}>
            AI responses can be inaccurate. Verify important information.
          </p>
        </div>
      </main>
    </div>
  );
}

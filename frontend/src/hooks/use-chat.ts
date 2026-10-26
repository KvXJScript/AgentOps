"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { Conversation, Message } from "@/lib/api/types";

/** A message as rendered by the UI; `local` marks client-only state. */
export type UIMessage = Message & {
  local?: "pending" | "stopped";
};

export type ThreadState =
  | { status: "loading"; messages: UIMessage[] }
  | { status: "ready"; messages: UIMessage[] }
  | { status: "error"; messages: UIMessage[]; error: ApiError };

export interface StreamState {
  conversationId: string;
  assistantId: string | null;
  content: string;
}

export interface SendError {
  conversationId: string | null;
  error: ApiError;
  /** The text the user tried to send; used by Retry. */
  content: string;
  /** True when nothing was persisted (pre-stream failure). */
  preStream: boolean;
}

export type ListState =
  | { status: "loading" }
  | { status: "ready"; items: Conversation[] }
  | { status: "error"; error: ApiError; items: Conversation[] };

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError({
    code: "unknown_error",
    message: err instanceof Error ? err.message : "Unexpected error",
  });
}

function readConversationFromUrl(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("c");
}

function writeConversationToUrl(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("c", id);
  else url.searchParams.delete("c");
  window.history.replaceState(null, "", url);
}

function sortByUpdated(items: Conversation[]): Conversation[] {
  return [...items].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

export function useChat() {
  const [list, setList] = useState<ListState>({ status: "loading" });
  const [activeId, setActiveIdState] = useState<string | null>(null);
  const [threads, setThreads] = useState<Record<string, ThreadState>>({});
  const [stream, setStream] = useState<StreamState | null>(null);
  const [sendError, setSendError] = useState<SendError | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const streamRef = useRef<StreamState | null>(null);

  // Read through a function so TS doesn't keep a stale narrowing across awaits.
  const currentStream = useCallback((): StreamState | null => streamRef.current, []);

  const updateStream = useCallback((next: StreamState | null) => {
    streamRef.current = next;
    setStream(next);
  }, []);

  const patchThread = useCallback(
    (id: string, fn: (messages: UIMessage[]) => UIMessage[]) => {
      setThreads((t) => {
        const cur = t[id] ?? { status: "ready" as const, messages: [] };
        return { ...t, [id]: { ...cur, messages: fn(cur.messages) } };
      });
    },
    [],
  );

  const refreshList = useCallback(async () => {
    try {
      const items = await api.listConversations();
      setList({ status: "ready", items });
    } catch (err) {
      setList((prev) => ({
        status: "error",
        error: toApiError(err),
        items: prev.status === "loading" ? [] : prev.items,
      }));
    }
  }, []);

  const loadThread = useCallback(async (id: string) => {
    setThreads((t) => {
      const cur = t[id];
      // Keep showing cached messages while revalidating; skeleton only on first load.
      if (cur && cur.status === "ready") return t;
      return { ...t, [id]: { status: "loading", messages: cur?.messages ?? [] } };
    });
    try {
      const conv = await api.getConversation(id);
      // Don't clobber optimistic state of a stream that is still running here.
      if (streamRef.current?.conversationId === id) return;
      setThreads((t) => ({ ...t, [id]: { status: "ready", messages: conv.messages } }));
      setList((prev) =>
        prev.status === "loading"
          ? prev
          : {
              ...prev,
              items: prev.items.map((c) =>
                c.id === id ? { id: conv.id, title: conv.title, created_at: conv.created_at, updated_at: conv.updated_at } : c,
              ),
            },
      );
    } catch (err) {
      setThreads((t) => ({
        ...t,
        [id]: { status: "error", messages: t[id]?.messages ?? [], error: toApiError(err) },
      }));
    }
  }, []);

  const selectConversation = useCallback(
    (id: string | null) => {
      setActiveIdState(id);
      setSendError(null);
      writeConversationToUrl(id);
      if (id && streamRef.current?.conversationId !== id) void loadThread(id);
    },
    [loadThread],
  );

  // Initial load: conversation list + the conversation from ?c= if present.
  useEffect(() => {
    let cancelled = false;
    const fromUrl = readConversationFromUrl();
    void (async () => {
      await Promise.resolve();
      if (cancelled) return;
      void refreshList();
      if (fromUrl) {
        setActiveIdState(fromUrl);
        void loadThread(fromUrl);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshList, loadThread]);

  // Abort any in-flight stream when the page unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  /** Returns true if the message was accepted by the server. */
  const send = useCallback(
    async (rawContent: string): Promise<boolean> => {
      const content = rawContent.trim();
      if (!content || currentStream()) return false;
      setSendError(null);

      let convId = activeId;
      if (!convId) {
        try {
          const created = await api.createConversation();
          convId = created.id;
          setList((prev) => ({
            status: "ready",
            items: [created, ...(prev.status === "loading" ? [] : prev.items.filter((c) => c.id !== created.id))],
          }));
          setThreads((t) => ({ ...t, [created.id]: { status: "ready", messages: [] } }));
          setActiveIdState(created.id);
          writeConversationToUrl(created.id);
        } catch (err) {
          setSendError({ conversationId: null, error: toApiError(err), content, preStream: true });
          return false;
        }
      }
      const cid = convId;

      const tempId = `pending-${Date.now()}`;
      const optimistic: UIMessage = {
        id: tempId,
        conversation_id: cid,
        role: "user",
        content,
        status: "complete",
        error_code: null,
        model: null,
        created_at: new Date().toISOString(),
        local: "pending",
      };
      patchThread(cid, (m) => [...m, optimistic]);

      const ac = new AbortController();
      abortRef.current = ac;
      updateStream({ conversationId: cid, assistantId: null, content: "" });
      let accepted = false;

      try {
        const result = await api.sendMessage(cid, content, {
          signal: ac.signal,
          onEvent: (ev) => {
            const s = currentStream();
            if (!s) return;
            if (ev.type === "message.created") {
              accepted = true;
              patchThread(cid, (m) => m.map((x) => (x.id === tempId ? ev.user_message : x)));
              updateStream({ ...s, assistantId: ev.assistant_message_id });
            } else if (ev.type === "token") {
              updateStream({ ...s, content: s.content + ev.delta });
            }
          },
        });

        if (result.outcome === "done") {
          accepted = true;
          patchThread(cid, (m) => [
            ...m.map((x) => (x.id === tempId && result.userMessage ? result.userMessage : x)),
            result.message,
          ]);
        } else {
          const s = currentStream();
          if (!accepted) {
            // Stopped before the server acknowledged: nothing to show.
            patchThread(cid, (m) => m.filter((x) => x.id !== tempId));
          } else {
            patchThread(cid, (m) => [
              ...m,
              {
                id: s?.assistantId ?? `stopped-${Date.now()}`,
                conversation_id: cid,
                role: "assistant",
                content: s?.content ?? "",
                // Mirrors what the backend persists on client disconnect (contract: `cancelled`).
                status: s?.content ? "incomplete" : "failed",
                error_code: "cancelled",
                model: null,
                created_at: new Date().toISOString(),
                local: "stopped",
              },
            ]);
          }
        }
      } catch (err) {
        const error = toApiError(err);
        const s = currentStream();
        if (!accepted) {
          patchThread(cid, (m) => m.filter((x) => x.id !== tempId));
          setSendError({ conversationId: cid, error, content, preStream: true });
        } else {
          const partial = s?.content ?? "";
          const messageId =
            (error.details as { message_id?: string | null } | undefined)?.message_id ?? s?.assistantId ?? `failed-${Date.now()}`;
          patchThread(cid, (m) => [
            ...m,
            {
              id: messageId,
              conversation_id: cid,
              role: "assistant",
              content: partial,
              status: partial ? "incomplete" : "failed",
              error_code: error.code,
              model: null,
              created_at: new Date().toISOString(),
            },
          ]);
          setSendError({ conversationId: cid, error, content, preStream: false });
        }
      } finally {
        abortRef.current = null;
        updateStream(null);
        void refreshList();
      }
      return accepted;
    },
    [activeId, currentStream, patchThread, refreshList, updateStream],
  );

  const deleteConversation = useCallback(
    async (id: string) => {
      if (streamRef.current?.conversationId === id) abortRef.current?.abort();
      await api.deleteConversation(id);
      setList((prev) => (prev.status === "loading" ? prev : { ...prev, items: prev.items.filter((c) => c.id !== id) }));
      setThreads((t) => {
        const next = { ...t };
        delete next[id];
        return next;
      });
      if (activeId === id) selectConversation(null);
    },
    [activeId, selectConversation],
  );

  const items = list.status === "loading" ? [] : sortByUpdated(list.items);
  const activeConversation = items.find((c) => c.id === activeId) ?? null;
  const activeThread = activeId ? (threads[activeId] ?? { status: "loading", messages: [] }) : null;

  return {
    list,
    conversations: items,
    activeId,
    activeConversation,
    activeThread,
    stream,
    isStreaming: stream !== null,
    sendError: sendError && (sendError.conversationId === null || sendError.conversationId === activeId) ? sendError : null,
    dismissError: () => setSendError(null),
    selectConversation,
    refreshList,
    reloadThread: loadThread,
    send,
    stop,
    deleteConversation,
  };
}

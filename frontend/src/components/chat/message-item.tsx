"use client";

import { AlertTriangleIcon, CircleSlashIcon, OctagonXIcon } from "lucide-react";
import { Markdown } from "./markdown";
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
  return <AssistantBubble content={message.content} footer={<StatusNote message={message} />} />;
}

export function AssistantBubble({
  content,
  streaming = false,
  footer,
}: {
  content: string;
  streaming?: boolean;
  footer?: React.ReactNode;
}) {
  return (
    <div className="flex gap-3">
      <div className="mt-0.5 shrink-0" aria-hidden>
        <BrandMark className="size-7" />
      </div>
      <div className="min-w-0 flex-1 pt-0.5">
        <span className="sr-only">Assistant said: </span>
        {content ? <Markdown content={content} /> : null}
        {streaming && <TypingIndicator />}
        {footer}
      </div>
    </div>
  );
}

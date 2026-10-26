"use client";

import { useLayoutEffect, useRef } from "react";
import { ArrowUpIcon, SquareIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { MAX_MESSAGE_LENGTH } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const COUNTER_FROM = Math.floor(MAX_MESSAGE_LENGTH * 0.75);

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  streaming,
  disabled = false,
  autoFocus = false,
  textareaRef,
  toolbar,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  streaming: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
  textareaRef?: React.RefObject<HTMLTextAreaElement | null>;
  /** Extra controls on the left of the footer row (e.g. "Use my documents"). */
  toolbar?: React.ReactNode;
  placeholder?: string;
}) {
  const localRef = useRef<HTMLTextAreaElement | null>(null);
  const ref = textareaRef ?? localRef;
  const length = value.length;
  const trimmed = value.trim();
  const canSend = !streaming && !disabled && trimmed.length > 0 && length <= MAX_MESSAGE_LENGTH;

  // Auto-grow up to a max height.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [value, ref]);

  const showCounter = length >= COUNTER_FROM;
  const over = length > MAX_MESSAGE_LENGTH;

  return (
    <form
      className="mx-auto w-full max-w-3xl"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSubmit();
      }}
    >
      <div
        className={cn(
          "relative flex flex-col rounded-2xl border bg-background shadow-xs transition-[border-color,box-shadow]",
          "focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/30",
          over && "border-destructive focus-within:border-destructive focus-within:ring-destructive/20",
        )}
      >
        <label htmlFor="composer-input" className="sr-only">
          Message
        </label>
        <textarea
          id="composer-input"
          ref={ref}
          rows={1}
          value={value}
          autoFocus={autoFocus}
          disabled={streaming || disabled}
          maxLength={MAX_MESSAGE_LENGTH}
          placeholder={streaming ? "Waiting for the response…" : (placeholder ?? "Ask anything…")}
          aria-describedby="composer-hint composer-counter"
          aria-invalid={over || undefined}
          className="max-h-60 min-h-[52px] w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-[15px] leading-6 outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-60"
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              if (canSend) onSubmit();
            }
          }}
        />
        <div className="flex items-center justify-between gap-2 px-3 pb-2.5">
          {toolbar && <div className="flex min-w-0 items-center">{toolbar}</div>}
          <p id="composer-hint" className={cn("text-xs text-muted-foreground", toolbar && "sr-only lg:not-sr-only lg:mr-auto")}>
            <kbd className="font-sans">Enter</kbd> to send · <kbd className="font-sans">Shift</kbd>+
            <kbd className="font-sans">Enter</kbd> for a new line
          </p>
          <div className="flex items-center gap-2.5">
            <span
              id="composer-counter"
              aria-live="polite"
              className={cn(
                "text-xs tabular-nums text-muted-foreground transition-opacity",
                showCounter ? "opacity-100" : "sr-only",
                over && "text-destructive",
                length >= MAX_MESSAGE_LENGTH && "text-warning",
              )}
            >
              {length.toLocaleString("en-US")} / {MAX_MESSAGE_LENGTH.toLocaleString("en-US")}
            </span>
            {streaming ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button type="button" size="icon" variant="outline" className="rounded-full" onClick={onStop} aria-label="Stop generating">
                    <SquareIcon className="size-3 fill-current" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Stop generating</TooltipContent>
              </Tooltip>
            ) : (
              <Button type="submit" size="icon" className="rounded-full" disabled={!canSend} aria-label="Send message">
                <ArrowUpIcon />
              </Button>
            )}
          </div>
        </div>
      </div>
    </form>
  );
}

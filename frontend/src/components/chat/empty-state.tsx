"use client";

import { FileSearchIcon, LightbulbIcon, ListChecksIcon, PenLineIcon } from "lucide-react";
import { BrandMark } from "@/components/layout/brand";

const SUGGESTIONS = [
  { icon: LightbulbIcon, label: "Explain a concept", prompt: "Explain retrieval-augmented generation in simple terms, with a short example." },
  { icon: ListChecksIcon, label: "Make a plan", prompt: "Draft a checklist for rolling out an internal AI assistant to a 50-person team." },
  { icon: PenLineIcon, label: "Improve writing", prompt: "Rewrite this to sound clearer and more confident: \"We kind of think the release might slip a bit.\"" },
  { icon: FileSearchIcon, label: "Compare options", prompt: "Compare SSE and WebSockets for streaming LLM responses. Use a table." },
];

export function EmptyState({
  title,
  subtitle,
  onPick,
  disabled,
}: {
  title: string;
  subtitle: string;
  onPick: (prompt: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center px-4 py-12">
      <BrandMark className="size-11" />
      <h1 className="mt-5 text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-2 max-w-md text-center text-sm text-muted-foreground">{subtitle}</p>
      <ul className="mt-8 grid w-full gap-2.5 sm:grid-cols-2" aria-label="Suggested prompts">
        {SUGGESTIONS.map(({ icon: Icon, label, prompt }) => (
          <li key={label}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onPick(prompt)}
              className="group flex h-full w-full flex-col gap-1.5 rounded-xl border bg-card p-3.5 text-left transition-colors hover:border-foreground/20 hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/40 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                <Icon className="size-4 text-brand" aria-hidden />
                {label}
              </span>
              <span className="line-clamp-2 text-[13px] leading-5 text-muted-foreground">{prompt}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

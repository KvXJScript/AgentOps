"use client";

import Link from "next/link";
import {
  ArrowRightIcon,
  BookOpenIcon,
  CheckIcon,
  FileSearchIcon,
  LightbulbIcon,
  ListChecksIcon,
  PenLineIcon,
  ShieldCheckIcon,
  UploadIcon,
} from "lucide-react";
import { BrandMark } from "@/components/layout/brand";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  { icon: LightbulbIcon, label: "Explain a concept", prompt: "Explain retrieval-augmented generation in simple terms, with a short example." },
  { icon: ListChecksIcon, label: "Make a plan", prompt: "Draft a checklist for rolling out an internal AI assistant to a 50-person team." },
  { icon: PenLineIcon, label: "Improve writing", prompt: "Rewrite this to sound clearer and more confident: \"We kind of think the release might slip a bit.\"" },
  { icon: FileSearchIcon, label: "Compare options", prompt: "Compare SSE and WebSockets for streaming LLM responses. Use a table." },
];

export interface OnboardingState {
  /** null while the documents list is loading or unavailable. */
  readyDocuments: number | null;
  documentsOn: boolean;
}

/** The core flow, shown calmly on an empty app: upload → ask with documents → report → approve. */
function GettingStarted({ readyDocuments, documentsOn }: OnboardingState) {
  const uploaded = (readyDocuments ?? 0) > 0;
  const steps = [
    {
      icon: UploadIcon,
      done: uploaded,
      title: "Upload a document",
      body: uploaded
        ? `${readyDocuments} ready to search.`
        : "Markdown, text or PDF: a runbook, a policy, meeting notes.",
      href: "/documents",
      cta: uploaded ? "Manage" : "Upload",
    },
    {
      icon: BookOpenIcon,
      done: uploaded && documentsOn,
      title: "Ask with “Use my documents”",
      body: "Turn it on below the message box. Answers cite passages you can open and inspect.",
    },
    {
      icon: ShieldCheckIcon,
      done: false,
      title: "Write a report, then approve a follow-up",
      body: "Reports show each workflow stage; a follow-up issue runs only after you approve it.",
      href: "/reports",
      cta: "Reports",
    },
  ];
  return (
    <section aria-labelledby="getting-started" className="mt-8 w-full rounded-xl border bg-card/60 p-4">
      <h2 id="getting-started" className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Getting started
      </h2>
      <ol className="mt-3 space-y-3">
        {steps.map(({ icon: Icon, done, title, body, href, cta }, i) => (
          <li key={title} className="flex items-start gap-3" data-step-done={done || undefined}>
            <span
              className={cn(
                "inline-flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-medium",
                done ? "border-brand bg-brand text-background" : "text-muted-foreground",
              )}
              aria-hidden
            >
              {done ? <CheckIcon className="size-3.5" /> : <Icon className="size-3.5" />}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">
                <span className="sr-only">
                  Step {i + 1}
                  {done ? " (done)" : ""}:{" "}
                </span>
                {title}
              </p>
              <p className="text-[13px] leading-5 text-muted-foreground">{body}</p>
            </div>
            {href && cta && (
              <Link
                href={href}
                className="inline-flex h-8 shrink-0 items-center gap-1 rounded-md px-2 text-xs font-medium text-brand hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                {cta} <ArrowRightIcon className="size-3.5" aria-hidden />
              </Link>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

export function EmptyState({
  title,
  subtitle,
  onPick,
  disabled,
  onboarding,
}: {
  title: string;
  subtitle: string;
  onPick: (prompt: string) => void;
  disabled?: boolean;
  /** Show the getting-started steps (first run, or no documents yet). */
  onboarding?: OnboardingState;
}) {
  return (
    // `justify-center-safe`: on short screens the content starts at the top instead of being clipped.
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center-safe px-4 py-10">
      <BrandMark className="size-11" />
      <h1 className="mt-5 text-center text-2xl font-semibold tracking-tight text-balance">{title}</h1>
      <p className="mt-2 max-w-md text-center text-sm text-pretty text-muted-foreground">{subtitle}</p>
      {onboarding && <GettingStarted {...onboarding} />}
      <ul className={cn("grid w-full gap-2.5 sm:grid-cols-2", onboarding ? "mt-4" : "mt-8")} aria-label="Suggested prompts">
        {SUGGESTIONS.map(({ icon: Icon, label, prompt }, i) => (
          // With the onboarding card shown, keep phones to two suggestions.
          <li key={label} className={cn(onboarding && i >= 2 && "hidden sm:block")}>
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

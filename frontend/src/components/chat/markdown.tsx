"use client";

import { memo, useMemo, useState, type ComponentProps } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { CheckIcon, CopyIcon } from "lucide-react";
import { CitationChip } from "./citations";
import { CITATION_ATTR, citationMap, remarkCitations } from "@/lib/api/citations";
import type { Citation } from "@/lib/api/types";
import { cn } from "@/lib/utils";

function CodeBlock({ children, ...props }: ComponentProps<"pre">) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="group/code relative">
      <pre {...props}>{children}</pre>
      <button
        type="button"
        aria-label={copied ? "Copied" : "Copy code"}
        className="absolute top-2 right-2 rounded-md border bg-background/80 p-1.5 text-muted-foreground opacity-0 backdrop-blur transition-opacity group-hover/code:opacity-100 hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
        onClick={(e) => {
          const text = e.currentTarget.parentElement?.querySelector("pre")?.innerText ?? "";
          void navigator.clipboard?.writeText(text).then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
      </button>
    </div>
  );
}

const components: Components = {
  a: ({ node: _node, href, children, ...props }) => (
    <a {...props} href={href} target="_blank" rel="noopener noreferrer nofollow">
      {children}
    </a>
  ),
  pre: ({ node: _node, ...props }) => <CodeBlock {...props} />,
  table: ({ node: _node, ...props }) => (
    <div className="my-4 overflow-x-auto">
      <table {...props} className="my-0" />
    </div>
  ),
};

const NO_CITATIONS: readonly Citation[] = [];

/**
 * Renders model output as Markdown. Raw HTML is never rendered (`skipHtml`,
 * no rehype-raw) and react-markdown's default URL transform drops
 * `javascript:`-style links. Images are not loaded: model output is untrusted
 * and an image URL is a classic data-exfiltration channel, so only the alt
 * text is shown.
 *
 * `[Sn]` markers become citation chips only when `citations` contains that
 * marker; any other marker is left as plain text.
 */
export const Markdown = memo(function Markdown({
  content,
  className,
  citations = NO_CITATIONS,
}: {
  content: string;
  className?: string;
  citations?: readonly Citation[];
}) {
  const { remarkPlugins, mdComponents } = useMemo(() => {
    const byMarker = citationMap(citations);
    if (byMarker.size === 0) return { remarkPlugins: [remarkGfm], mdComponents: components };
    const withChips: Components = {
      ...components,
      sup: ({ node, children, ...props }) => {
        const marker = node?.properties?.[CITATION_ATTR];
        const citation = typeof marker === "string" ? byMarker.get(marker) : undefined;
        if (citation) return <CitationChip citation={citation} />;
        return <sup {...props}>{children}</sup>;
      },
    };
    return {
      remarkPlugins: [remarkGfm, [remarkCitations, { markers: new Set(byMarker.keys()) }]] as NonNullable<
        ComponentProps<typeof ReactMarkdown>["remarkPlugins"]
      >,
      mdComponents: withChips,
    };
  }, [citations]);

  return (
    <div
      className={cn(
        "prose prose-neutral dark:prose-invert max-w-none text-[15px] leading-7",
        "prose-p:my-3 prose-headings:font-semibold prose-headings:tracking-tight prose-h1:text-xl prose-h2:text-lg prose-h3:text-base",
        "prose-a:text-brand prose-a:underline-offset-2 prose-li:my-1",
        "prose-code:rounded prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.85em] prose-code:font-normal prose-code:before:content-none prose-code:after:content-none",
        "prose-pre:rounded-lg prose-pre:border prose-pre:bg-muted/60 prose-pre:text-foreground prose-pre:text-[13px] [&_pre_code]:bg-transparent [&_pre_code]:p-0",
        "prose-th:text-left prose-table:text-sm",
        "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        skipHtml
        disallowedElements={["img"]}
        unwrapDisallowed
        components={mdComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

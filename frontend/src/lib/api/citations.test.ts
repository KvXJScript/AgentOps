import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Markdown } from "@/components/chat/markdown";
import { citationMap, describeLocation, remarkCitations, splitCitationMarkers } from "./citations";
import type { Citation } from "./types";

function citation(marker: string, over: Partial<Citation> = {}): Citation {
  return {
    marker,
    chunk_id: `chunk-${marker}`,
    document_id: "doc-1",
    document_title: "Handbook",
    excerpt: "Excerpt text",
    location: { char_start: 0, char_end: 10, page: 3, heading_path: ["Setup", "Install"] },
    document_deleted: false,
    ...over,
  };
}

function render(content: string, citations: Citation[]) {
  return renderToStaticMarkup(createElement(Markdown, { content, citations }));
}

function chips(html: string): string[] {
  return [...html.matchAll(/data-citation-chip="(S\d+)"/g)].map((m) => m[1]);
}

describe("splitCitationMarkers", () => {
  it("splits valid markers and keeps invalid ones as text", () => {
    expect(splitCitationMarkers("A [S1] b [S9] c [S2].", new Set(["S1", "S2"]))).toEqual([
      { type: "text", value: "A " },
      { type: "marker", marker: "S1" },
      { type: "text", value: " b [S9] c " },
      { type: "marker", marker: "S2" },
      { type: "text", value: "." },
    ]);
  });

  it("handles adjacent markers and text without markers", () => {
    expect(splitCitationMarkers("[S1][S2]", new Set(["S1", "S2"]))).toEqual([
      { type: "marker", marker: "S1" },
      { type: "marker", marker: "S2" },
    ]);
    expect(splitCitationMarkers("no markers", new Set(["S1"]))).toEqual([{ type: "text", value: "no markers" }]);
  });

  it("only matches the backend marker shape [S<1-3 digits>]", () => {
    const valid = new Set(["S1", "S1234"]);
    const segs = splitCitationMarkers("[s1] [S 1] (S1) [S1234] [S1]", valid);
    expect(segs.filter((s) => s.type === "marker")).toEqual([{ type: "marker", marker: "S1" }]);
  });
});

describe("remarkCitations", () => {
  it("does not touch link text or code", () => {
    const tree = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            { type: "text", value: "see [S1]" },
            { type: "link", children: [{ type: "text", value: "[S1]" }] },
            { type: "inlineCode", value: "[S1]" },
          ],
        },
      ],
    };
    remarkCitations({ markers: new Set(["S1"]) })(tree);
    const p = tree.children[0].children as Array<{ type: string; value?: string; children?: unknown[] }>;
    expect(p.map((n) => n.type)).toEqual(["text", "citationRef", "link", "inlineCode"]);
    expect(p[2].children).toEqual([{ type: "text", value: "[S1]" }]);
    expect(p[3].value).toBe("[S1]");
  });
});

describe("Markdown citation rendering", () => {
  it("renders valid markers as chips and invalid markers as plain text", () => {
    const html = render("Fact one [S1]. Invented [S9]. Fact two [S2].", [citation("S1"), citation("S2")]);
    expect(chips(html)).toEqual(["S1", "S2"]);
    expect(html).toContain("Invented [S9].");
    expect(html).not.toContain('data-citation-chip="S9"');
    expect(html).toContain('aria-label="Source 1: Handbook"');
  });

  it("renders no chips without citations (e.g. while streaming)", () => {
    const html = render("Streaming text [S1] and [S2]", []);
    expect(chips(html)).toEqual([]);
    expect(html).toContain("Streaming text [S1] and [S2]");
  });

  it("handles adjacent markers, lists, and leaves code spans/blocks alone", () => {
    const html = render("- item [S1][S2]\n\n`[S1]`\n\n```\n[S2]\n```", [citation("S1"), citation("S2")]);
    expect(chips(html)).toEqual(["S1", "S2"]);
    expect(html).toContain("<code>[S1]</code>");
    expect(html).toMatch(/<pre><code>\[S2\]\n<\/code><\/pre>/);
  });

  it("marks deleted sources in the chip label", () => {
    const html = render("Old [S1]", [citation("S1", { document_deleted: true, document_title: "Gone" })]);
    expect(html).toContain('aria-label="Source 1: Gone (deleted)"');
  });

  it("never renders raw HTML smuggled next to markers", () => {
    const html = render('<img src=x onerror="alert(1)"> [S1]', [citation("S1")]);
    expect(html).not.toContain("onerror");
    expect(chips(html)).toEqual(["S1"]);
  });
});

describe("helpers", () => {
  it("citationMap keeps the first citation per marker", () => {
    const m = citationMap([citation("S1", { excerpt: "first" }), citation("S1", { excerpt: "second" })]);
    expect(m.get("S1")?.excerpt).toBe("first");
    expect(citationMap(undefined).size).toBe(0);
  });

  it("describeLocation formats page and heading path", () => {
    expect(describeLocation({ char_start: 0, char_end: 1, page: 4, heading_path: ["A", "B"] })).toBe("p. 4 · A › B");
    expect(describeLocation({ char_start: 0, char_end: 1, page: null, heading_path: [] })).toBeNull();
  });
});

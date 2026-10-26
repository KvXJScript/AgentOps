/**
 * Citation markers (`[S1]`) in assistant markdown.
 *
 * Only markers that resolve to a citation stored on the message become chips;
 * anything else (invented labels, labels quoted from a document, markers while
 * the answer is still streaming) stays plain text. The regex mirrors the
 * backend's `_MARKER` in `application/grounding.py`.
 */
import type { Citation, ChunkLocation } from "./types";

export const MARKER_RE = /\[(S\d{1,3})\]/g;

export type MarkerSegment = { type: "text"; value: string } | { type: "marker"; marker: string };

/** Split text into plain runs and *valid* marker references. */
export function splitCitationMarkers(text: string, valid: ReadonlySet<string>): MarkerSegment[] {
  const out: MarkerSegment[] = [];
  let last = 0;
  let buf = "";
  for (const m of text.matchAll(MARKER_RE)) {
    const label = m[1];
    if (!valid.has(label)) continue;
    const start = m.index;
    buf += text.slice(last, start);
    if (buf) out.push({ type: "text", value: buf });
    buf = "";
    out.push({ type: "marker", marker: label });
    last = start + m[0].length;
  }
  buf += text.slice(last);
  if (buf) out.push({ type: "text", value: buf });
  return out;
}

export function citationMap(citations: readonly Citation[] | undefined): Map<string, Citation> {
  const map = new Map<string, Citation>();
  for (const c of citations ?? []) if (!map.has(c.marker)) map.set(c.marker, c);
  return map;
}

/** Ordinal shown in a chip: "S3" → 3. */
export function markerNumber(marker: string): string {
  return marker.replace(/^S/, "");
}

/** Human location: "p. 4 · Setup › Install" (either part may be missing). */
export function describeLocation(loc: ChunkLocation | null | undefined): string | null {
  if (!loc) return null;
  const parts: string[] = [];
  if (loc.page != null) parts.push(`p. ${loc.page}`);
  if (loc.heading_path?.length) parts.push(loc.heading_path.join(" › "));
  return parts.length ? parts.join(" · ") : null;
}

// --- remark plugin ----------------------------------------------------------

/** Minimal mdast shape; avoids depending on @types/mdast directly. */
interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: Record<string, unknown>;
}

/** Inline containers whose text must not gain interactive children. */
const SKIP_PARENTS = new Set(["link", "linkReference", "definition", "footnoteDefinition"]);

export const CITATION_ATTR = "dataCitation";

function transform(node: MdNode, valid: ReadonlySet<string>) {
  if (!node.children || SKIP_PARENTS.has(node.type)) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && typeof child.value === "string" && child.value.includes("[S")) {
      for (const seg of splitCitationMarkers(child.value, valid)) {
        if (seg.type === "text") next.push({ type: "text", value: seg.value });
        else
          next.push({
            type: "citationRef",
            data: {
              hName: "sup",
              hProperties: { [CITATION_ATTR]: seg.marker },
              hChildren: [{ type: "text", value: `[${seg.marker}]` }],
            },
          });
      }
    } else {
      transform(child, valid);
      next.push(child);
    }
  }
  node.children = next;
}

/**
 * remark plugin: replaces valid `[Sn]` markers with `<sup data-citation="Sn">`
 * hast elements, which the Markdown renderer turns into citation chips.
 */
export function remarkCitations(options: { markers: ReadonlySet<string> }) {
  const valid = options.markers;
  return (tree: MdNode) => {
    if (valid.size === 0) return;
    transform(tree, valid);
  };
}

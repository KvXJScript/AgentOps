/**
 * Client-side pre-checks for document uploads (docs/api-contract.md, Phase 2).
 * They only save a round trip: the server is authoritative and its 415/413/422
 * envelopes are still shown verbatim through `describeError`.
 */
import type { DocumentMediaType } from "./types";

/** Contract default `MAX_UPLOAD_BYTES` ("10 MB", read as MiB). */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

export const ACCEPTED_EXTENSIONS = ["md", "txt", "pdf"] as const;
export type AcceptedExtension = (typeof ACCEPTED_EXTENSIONS)[number];

export const EXTENSION_MEDIA_TYPE: Record<AcceptedExtension, DocumentMediaType> = {
  md: "text/markdown",
  txt: "text/plain",
  pdf: "application/pdf",
};

/** `accept` attribute for the file picker. */
export const ACCEPT_ATTR = ".md,.txt,.pdf,text/markdown,text/plain,application/pdf";

/**
 * Browser-reported MIME types that are compatible with each extension.
 * Browsers often report "" (or `application/octet-stream`) for .md, so an
 * empty/generic type never fails the check on its own — the extension decides.
 */
const COMPATIBLE_TYPES: Record<AcceptedExtension, readonly string[]> = {
  md: ["text/markdown", "text/x-markdown", "text/plain"],
  txt: ["text/plain"],
  pdf: ["application/pdf", "application/x-pdf"],
};
const GENERIC_TYPES = new Set(["", "application/octet-stream"]);

export type UploadValidation =
  | { ok: true; extension: AcceptedExtension }
  | { ok: false; code: "unsupported_file_type" | "file_too_large" | "empty_document"; message: string };

export function fileExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 && dot < name.length - 1 ? name.slice(dot + 1).toLowerCase() : "";
}

/** Title the server will use by default (filename stem). */
export function filenameStem(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function validateUploadFile(
  file: { name: string; size: number; type: string },
  maxBytes: number = MAX_UPLOAD_BYTES,
): UploadValidation {
  const ext = fileExtension(file.name);
  if (!(ACCEPTED_EXTENSIONS as readonly string[]).includes(ext)) {
    return {
      ok: false,
      code: "unsupported_file_type",
      message: ext
        ? `.${ext} files aren't supported. Upload Markdown (.md), plain text (.txt) or PDF.`
        : "Files need a .md, .txt or .pdf extension.",
    };
  }
  const extension = ext as AcceptedExtension;
  const type = file.type.toLowerCase().split(";")[0].trim();
  if (!GENERIC_TYPES.has(type) && !COMPATIBLE_TYPES[extension].includes(type)) {
    return {
      ok: false,
      code: "unsupported_file_type",
      message: `This file says it is ${type}, which doesn't match its .${extension} extension.`,
    };
  }
  if (file.size === 0) {
    return { ok: false, code: "empty_document", message: "This file is empty." };
  }
  if (file.size > maxBytes) {
    return {
      ok: false,
      code: "file_too_large",
      message: `This file is ${formatBytes(file.size)}; the limit is ${formatBytes(maxBytes)}.`,
    };
  }
  return { ok: true, extension };
}

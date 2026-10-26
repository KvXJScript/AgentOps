import { describe, expect, it } from "vitest";
import { MAX_UPLOAD_BYTES, fileExtension, filenameStem, formatBytes, validateUploadFile } from "./upload";

const f = (name: string, size = 100, type = "") => ({ name, size, type });

describe("validateUploadFile", () => {
  it.each([
    ["notes.md", "text/markdown"],
    ["notes.md", ""],
    ["notes.md", "text/x-markdown"],
    ["README.MD", "text/plain"],
    ["a.txt", "text/plain"],
    ["a.txt", "text/plain;charset=utf-8"],
    ["paper.pdf", "application/pdf"],
    ["paper.pdf", "application/octet-stream"],
  ])("accepts %s (%s)", (name, type) => {
    expect(validateUploadFile(f(name, 10, type)).ok).toBe(true);
  });

  it.each([["image.png", "image/png"], ["doc.docx", ""], ["noext", ""], [".md", ""], ["archive.md.zip", ""]])(
    "rejects %s as unsupported_file_type",
    (name, type) => {
      const r = validateUploadFile(f(name, 10, type));
      expect(r).toMatchObject({ ok: false, code: "unsupported_file_type" });
    },
  );

  it("rejects a MIME type that contradicts the extension", () => {
    expect(validateUploadFile(f("fake.pdf", 10, "image/png"))).toMatchObject({ ok: false, code: "unsupported_file_type" });
  });

  it("enforces the 10 MB limit inclusively", () => {
    expect(validateUploadFile(f("big.pdf", MAX_UPLOAD_BYTES, "application/pdf")).ok).toBe(true);
    const r = validateUploadFile(f("big.pdf", MAX_UPLOAD_BYTES + 1, "application/pdf"));
    expect(r).toMatchObject({ ok: false, code: "file_too_large" });
    if (!r.ok) expect(r.message).toContain("10.0 MB");
  });

  it("rejects empty files", () => {
    expect(validateUploadFile(f("empty.txt", 0, "text/plain"))).toMatchObject({ ok: false, code: "empty_document" });
  });
});

describe("upload helpers", () => {
  it("parses extensions and stems", () => {
    expect(fileExtension("a.b.PDF")).toBe("pdf");
    expect(fileExtension("x.")).toBe("");
    expect(filenameStem("Q3 report.final.pdf")).toBe("Q3 report.final");
  });
  it("formats bytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});

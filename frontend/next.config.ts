import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

/**
 * CSP without nonces (pages stay statically prerendered). Next.js and next-themes
 * emit small inline bootstrap scripts, hence 'unsafe-inline' for scripts; there is
 * no other script source. The browser only talks to this origin (the API is proxied
 * under /api/v1), so connect-src is 'self'. Dev adds 'unsafe-eval' (React dev tools /
 * Turbopack HMR) and ws: for the HMR socket. Markdown images are never loaded, so
 * img-src stays local.
 */
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self'",
  `connect-src 'self'${isDev ? " ws: wss:" : ""}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

const nextConfig: NextConfig = {
  // Keep the dev-only indicator away from the sidebar status widget.
  devIndicators: { position: "bottom-right" },
  poweredByHeader: false,
  cacheComponents: true,
  partialPrefetching: true,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

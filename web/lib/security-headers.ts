/**
 * Secure HTTP response headers for the Postmortem console (charter T8).
 *
 * Applied to every route via `next.config.ts`'s `headers()`. Kept in a separate,
 * unit-tested module so header presence and CSP shape are asserted in CI
 * (`security-headers.test.ts`) rather than trusted by inspection.
 *
 * Deploy-time note: `connect-src` is `'self'` here; the backend responder/SSE
 * origin is appended at deploy time from the environment (it is not known at
 * build time and must never be hard-coded). `script-src`/`style-src` keep
 * `'unsafe-inline'` for Next.js's inline bootstrap + hydration; tightening these
 * to per-request nonces is the documented deploy-time hardening step.
 */

export interface SecurityHeader {
  key: string;
  value: string;
}

/** Content-Security-Policy directives, deny-by-default. */
export const CSP_DIRECTIVES: Record<string, string[]> = {
  "default-src": ["'self'"],
  "base-uri": ["'self'"],
  "font-src": ["'self'", "data:"],
  "form-action": ["'self'"],
  // Clickjacking defense (belt-and-suspenders with X-Frame-Options).
  "frame-ancestors": ["'none'"],
  "frame-src": ["'none'"],
  "img-src": ["'self'", "data:", "blob:"],
  "object-src": ["'none'"],
  "script-src": ["'self'", "'unsafe-inline'"],
  "style-src": ["'self'", "'unsafe-inline'"],
  // Same-origin only; the API origin is appended from env at deploy time.
  "connect-src": ["'self'"],
  "worker-src": ["'self'", "blob:"],
  "manifest-src": ["'self'"],
  "upgrade-insecure-requests": [],
};

/** Serialize CSP directives into a header value. Valueless directives (e.g.
 * `upgrade-insecure-requests`) are emitted as bare tokens. */
export function buildContentSecurityPolicy(
  directives: Record<string, string[]> = CSP_DIRECTIVES,
): string {
  return Object.entries(directives)
    .map(([name, values]) => (values.length ? `${name} ${values.join(" ")}` : name))
    .join("; ");
}

/** The full set of security headers applied to every response. */
export const SECURITY_HEADERS: SecurityHeader[] = [
  { key: "Content-Security-Policy", value: buildContentSecurityPolicy() },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), browsing-topics=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "X-DNS-Prefetch-Control", value: "off" },
];

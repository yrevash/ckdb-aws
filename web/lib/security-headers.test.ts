import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  buildContentSecurityPolicy,
  CSP_DIRECTIVES,
  SECURITY_HEADERS,
} from "./security-headers";

describe("security response headers (T8)", () => {
  const byKey = new Map(SECURITY_HEADERS.map((h) => [h.key, h.value]));

  it("emits every required hardening header", () => {
    for (const key of [
      "Content-Security-Policy",
      "Strict-Transport-Security",
      "X-Frame-Options",
      "X-Content-Type-Options",
      "Referrer-Policy",
      "Permissions-Policy",
    ]) {
      expect(byKey.has(key)).toBe(true);
      expect(byKey.get(key)).toBeTruthy();
    }
  });

  it("pins the clickjacking, sniffing, and referrer defenses", () => {
    expect(byKey.get("X-Frame-Options")).toBe("DENY");
    expect(byKey.get("X-Content-Type-Options")).toBe("nosniff");
    expect(byKey.get("Referrer-Policy")).toBe("strict-origin-when-cross-origin");
    expect(byKey.get("Strict-Transport-Security")).toContain("max-age=");
    expect(byKey.get("Strict-Transport-Security")).toContain("includeSubDomains");
  });

  it("builds a deny-by-default CSP with framing locked down", () => {
    const csp = buildContentSecurityPolicy();
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'self'");
    // valueless directive serialized as a bare token
    expect(csp).toContain("upgrade-insecure-requests");
    expect(csp).not.toContain("upgrade-insecure-requests ");
  });

  it("never widens a fetch/script directive to a wildcard", () => {
    for (const [name, values] of Object.entries(CSP_DIRECTIVES)) {
      expect(values, `${name} must not use *`).not.toContain("*");
      if (name === "script-src" || name === "default-src") {
        expect(values).not.toContain("'unsafe-eval'");
      }
    }
  });
});

/**
 * No server-only secret may reach the client bundle: only non-secret,
 * URL-shaped values are allowed to travel over NEXT_PUBLIC_*. This scan fails if
 * anyone introduces a NEXT_PUBLIC_ env var whose name looks like a credential.
 */
describe("no secrets in the client bundle (T8)", () => {
  const webRoot = join(__dirname, "..");
  const scanDirs = ["app", "components", "hooks", "lib"];
  const secretPattern = /NEXT_PUBLIC_[A-Z0-9_]*(SECRET|TOKEN|KEY|PASSWORD|CREDENTIAL)/;
  const nextPublicPattern = /process\.env\.(NEXT_PUBLIC_[A-Z0-9_]+)/g;

  function walk(dir: string): string[] {
    const out: string[] = [];
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
        out.push(...walk(full));
      } else if (/\.(ts|tsx|js|jsx)$/.test(entry.name)) {
        out.push(full);
      }
    }
    return out;
  }

  const files = scanDirs.flatMap((d) => {
    try {
      return walk(join(webRoot, d));
    } catch {
      return [];
    }
  });

  it("references no secret-named NEXT_PUBLIC_ variable", () => {
    const offenders: string[] = [];
    const referenced = new Set<string>();
    for (const file of files) {
      const src = readFileSync(file, "utf8");
      if (secretPattern.test(src)) offenders.push(file);
      for (const match of src.matchAll(nextPublicPattern)) referenced.add(match[1]);
    }
    expect(offenders, `secret-named NEXT_PUBLIC var in: ${offenders.join(", ")}`).toEqual(
      [],
    );
    // Sanity: the client only reaches for URL-shaped public config.
    for (const name of referenced) {
      expect(name).toMatch(/_URL$/);
    }
  });
});

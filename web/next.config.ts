import type { NextConfig } from "next";

import { SECURITY_HEADERS } from "./lib/security-headers";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Secure HTTP response headers on every route (charter T8). Defined in a
  // unit-tested module so the header set is verified in CI, not by inspection.
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: SECURITY_HEADERS.map(({ key, value }) => ({ key, value })),
      },
    ];
  },
};

export default nextConfig;

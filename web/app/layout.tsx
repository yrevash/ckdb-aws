import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Postmortem · Incident Console",
  description:
    "An on-call SRE agent with persistent, transactionally consistent memory — a calm, insight-first evidence board of what's proven and what's pending.",
};

/**
 * Commit the theme before first paint so there is no light/dark flash. Reads the
 * saved choice, else the OS preference, and stamps <html data-theme>. Runs under
 * CSP `script-src 'self' 'unsafe-inline'` (kept intact in security-headers.ts).
 */
const themeInitScript = `(function(){try{var t=localStorage.getItem("postmortem-theme");if(t!=="light"&&t!=="dark"){t=window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}document.documentElement.setAttribute("data-theme",t);}catch(e){}})();`;

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}

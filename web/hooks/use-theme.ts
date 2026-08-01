"use client";

import { useCallback, useSyncExternalStore } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "postmortem-theme";
const THEME_EVENT = "postmortem-themechange";

/** The theme currently committed to <html data-theme> (set pre-paint by the
 *  inline script in layout.tsx), falling back to the OS preference. */
function readTheme(): Theme {
  if (typeof document === "undefined") return "light";
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "dark" || attr === "light") return attr;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(THEME_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(THEME_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/**
 * Theme toggle backed by <html data-theme> + localStorage, read through
 * `useSyncExternalStore` — the DOM attribute is the single source of truth, so
 * there is no setState-in-effect and no first-paint flash (the attribute is
 * committed before hydration).
 */
export function useTheme(): {
  theme: Theme;
  toggle: () => void;
  setTheme: (t: Theme) => void;
} {
  const theme = useSyncExternalStore<Theme>(subscribe, readTheme, () => "light");

  const setTheme = useCallback((next: Theme) => {
    document.documentElement.setAttribute("data-theme", next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private mode / storage disabled — the attribute still drives the theme.
    }
    window.dispatchEvent(new Event(THEME_EVENT));
  }, []);

  const toggle = useCallback(() => {
    setTheme(readTheme() === "dark" ? "light" : "dark");
  }, [setTheme]);

  return { theme, toggle, setTheme };
}

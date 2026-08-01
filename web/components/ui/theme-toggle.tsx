"use client";

import { useTheme } from "@/hooks/use-theme";
import { IconMoon, IconSun } from "@/components/ui/icons";

/** Light/dark toggle. Initial theme is committed pre-paint (layout.tsx), so this
 *  only flips the <html data-theme> attribute and persists the choice. */
export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
    >
      {theme === "dark" ? <IconSun /> : <IconMoon />}
      <span>{theme === "dark" ? "Light" : "Dark"}</span>
    </button>
  );
}

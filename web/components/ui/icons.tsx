import type { SVGProps } from "react";

/**
 * The console's icon set — a small, consistent line family (1.6 stroke, round
 * caps) so glyphs read as one system. `currentColor` throughout, so an icon
 * inherits whatever ink/accent its context sets.
 */
type IconProps = SVGProps<SVGSVGElement>;

function base(props: IconProps) {
  return {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    ...props,
  };
}

export function IconOverview(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 13h6v7H4zM4 4h6v5H4zM14 4h6v7h-6zM14 15h6v5h-6z" />
    </svg>
  );
}

export function IconIncident(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12 3 3 20h18L12 3z" />
      <path d="M12 10v4M12 17.5v.01" />
    </svg>
  );
}

export function IconResilience(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6l-7-3z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}

export function IconMemory(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12 4a5 5 0 0 0-5 5v1a4 4 0 0 0 0 8h1M12 4a5 5 0 0 1 5 5v1a4 4 0 0 1 0 8h-1M12 4v14" />
    </svg>
  );
}

export function IconCheck(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="m5 12 4.5 4.5L19 7" />
    </svg>
  );
}

export function IconCross(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

export function IconClock(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  );
}

export function IconSun(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

export function IconMoon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />
    </svg>
  );
}

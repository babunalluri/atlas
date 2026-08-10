"use client";

import { useEffect, useState } from "react";

export type SurfaceTheme = "light" | "dark";
export type ThemeSurface = "admin" | "workspace";

const STORAGE_KEYS: Record<ThemeSurface, string> = {
  admin: "atlas-admin-theme",
  workspace: "atlas-workspace-theme",
};

const DEFAULTS: Record<ThemeSurface, SurfaceTheme> = {
  admin: "light",
  workspace: "light",
};

export function readSurfaceTheme(surface: ThemeSurface): SurfaceTheme {
  if (typeof window === "undefined") return DEFAULTS[surface];
  const raw = window.localStorage.getItem(STORAGE_KEYS[surface]);
  if (raw === "light" || raw === "dark") return raw;
  return DEFAULTS[surface];
}

export function writeSurfaceTheme(surface: ThemeSurface, theme: SurfaceTheme) {
  try {
    window.localStorage.setItem(STORAGE_KEYS[surface], theme);
  } catch {
    // private mode / blocked storage
  }
}

/** Persistable light/dark preference for admin or workspace. */
export function useSurfaceTheme(surface: ThemeSurface) {
  const [theme, setTheme] = useState<SurfaceTheme>(DEFAULTS[surface]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setTheme(readSurfaceTheme(surface));
    setReady(true);
  }, [surface]);

  function changeTheme(next: SurfaceTheme) {
    setTheme(next);
    writeSurfaceTheme(surface, next);
  }

  return {
    theme,
    ready,
    dark: theme === "dark",
    changeTheme,
    toggleTheme: () => changeTheme(theme === "dark" ? "light" : "dark"),
  };
}

export function ThemeToggle({
  theme,
  onChange,
  className,
}: {
  theme: SurfaceTheme;
  onChange: (theme: SurfaceTheme) => void;
  className?: string;
}) {
  const dark = theme === "dark";
  // Label the destination theme so the control reads as an action
  // ("click for Light" while on dark, and vice versa).
  const nextTheme = dark ? "light" : "dark";
  const nextLabel = dark ? "Light" : "Dark";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      aria-label={`Switch to ${nextLabel.toLowerCase()} theme`}
      title={`Switch to ${nextLabel.toLowerCase()} theme`}
      onClick={() => onChange(nextTheme)}
      className={
        className ??
        "inline-flex h-7 items-center gap-1.5 rounded-full border border-line bg-raised/70 px-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-muted transition hover:border-line-strong hover:text-ink"
      }
    >
      <span
        className={
          nextTheme === "dark"
            ? "size-1.5 rounded-full bg-teal-bright"
            : "size-1.5 rounded-full bg-amber"
        }
      />
      {nextLabel}
    </button>
  );
}

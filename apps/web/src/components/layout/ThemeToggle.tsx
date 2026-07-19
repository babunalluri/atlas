"use client";

export type AdminTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "atlas-admin-theme";

export function readStoredTheme(): AdminTheme {
  if (typeof window === "undefined") return "light";
  return window.localStorage.getItem(THEME_STORAGE_KEY) === "dark"
    ? "dark"
    : "light";
}

export function ThemeToggle({
  theme,
  onChange,
}: {
  theme: AdminTheme;
  onChange: (theme: AdminTheme) => void;
}) {
  const dark = theme === "dark";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      aria-label="Toggle dark command-center theme"
      title={dark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => onChange(dark ? "light" : "dark")}
      className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line bg-raised/70 px-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-muted transition hover:border-line-strong hover:text-ink"
    >
      <span
        className={
          dark
            ? "size-1.5 rounded-full bg-teal-bright"
            : "size-1.5 rounded-full bg-amber"
        }
      />
      {dark ? "Ops" : "Day"}
    </button>
  );
}

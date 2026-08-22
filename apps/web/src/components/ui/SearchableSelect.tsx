"use client";

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { cn } from "@/lib/utils";

export type SearchableSelectOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

const fieldClass =
  "w-full rounded-md border border-line bg-raised text-ink outline-none transition placeholder:text-slate-muted focus:border-teal focus:ring-2 focus:ring-teal/20";
const fieldSizeClass = {
  md: "px-3 py-2 text-sm",
  sm: "px-2 py-1 text-xs",
} as const;

/** ~12 option rows before the panel scrolls. */
const LIST_MAX_HEIGHT = "max-h-72";

export function SearchableSelect({
  id,
  value,
  onChange,
  options,
  placeholder = "Select…",
  disabled = false,
  className,
  emptyMessage = "No matches",
  allowCustom = false,
  size = "md",
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: SearchableSelectOption[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  emptyMessage?: string;
  /** When true, Enter/blur/Tab commits typed text even if it is not in options. */
  allowCustom?: boolean;
  size?: "sm" | "md";
}) {
  const reactId = useId();
  const listId = `${id ?? reactId}-listbox`;
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const openRef = useRef(false);
  const userMovedHighlightRef = useRef(false);
  const commitPendingQueryRef = useRef<() => void>(() => {});
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);

  const selected = useMemo(
    () => options.find((option) => option.value === value) ?? null,
    [options, value],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (option) =>
        option.label.toLowerCase().includes(q) ||
        option.value.toLowerCase().includes(q),
    );
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    setHighlight(0);
    userMovedHighlightRef.current = false;
  }, [open, query]);

  function resetPanel() {
    openRef.current = false;
    setOpen(false);
    setQuery("");
    userMovedHighlightRef.current = false;
  }

  function findExactOption(raw: string) {
    const trimmed = raw.trim();
    if (!trimmed) return null;
    const needle = trimmed.toLowerCase();
    return (
      options.find(
        (option) =>
          !option.disabled &&
          (option.value.toLowerCase() === needle ||
            option.label.toLowerCase() === needle),
      ) ?? null
    );
  }

  function commitCustom(raw: string) {
    const trimmed = raw.trim();
    if (!trimmed || trimmed === value) {
      resetPanel();
      return;
    }
    onChange(trimmed);
    resetPanel();
  }

  function commit(next: string) {
    const option = options.find((item) => item.value === next);
    if (option) {
      if (!option.disabled) {
        if (option.value !== value) onChange(option.value);
        resetPanel();
      }
      return;
    }
    if (allowCustom) commitCustom(next);
  }

  function commitPendingQuery(options?: { preferHighlight?: boolean }) {
    if (!openRef.current) return;
    const trimmed = query.trim();
    if (!trimmed) {
      resetPanel();
      return;
    }
    const exact = findExactOption(trimmed);
    if (exact) {
      commit(exact.value);
      return;
    }
    if (allowCustom) {
      if (userMovedHighlightRef.current) {
        const highlighted = filtered[highlight];
        if (highlighted && !highlighted.disabled) {
          commit(highlighted.value);
          return;
        }
      }
      commitCustom(trimmed);
      return;
    }
    if (options?.preferHighlight || userMovedHighlightRef.current) {
      const highlighted = filtered[highlight];
      if (highlighted && !highlighted.disabled) {
        commit(highlighted.value);
        return;
      }
    }
    resetPanel();
  }

  useEffect(() => {
    commitPendingQueryRef.current = commitPendingQuery;
  });

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        commitPendingQueryRef.current();
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  function openPanel() {
    if (disabled) return;
    openRef.current = true;
    setOpen(true);
    setQuery("");
    userMovedHighlightRef.current = false;
    requestAnimationFrame(() => inputRef.current?.select());
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (disabled) return;
    if (!open && (event.key === "ArrowDown" || event.key === "Enter")) {
      event.preventDefault();
      openPanel();
      return;
    }
    if (event.key === "Tab") {
      if (open) commitPendingQuery();
      return;
    }
    if (!open) return;

    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      resetPanel();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      userMovedHighlightRef.current = true;
      setHighlight((index) =>
        filtered.length === 0 ? 0 : Math.min(index + 1, filtered.length - 1),
      );
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      userMovedHighlightRef.current = true;
      setHighlight((index) => Math.max(index - 1, 0));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      commitPendingQuery({ preferHighlight: true });
    }
  }

  function onBlur() {
    window.setTimeout(() => {
      if (rootRef.current?.contains(document.activeElement)) return;
      commitPendingQueryRef.current();
    }, 0);
  }

  const displayValue = open
    ? query
    : (selected?.label ?? (allowCustom ? value : ""));

  const customHint =
    allowCustom && query.trim() && !findExactOption(query)
      ? query.trim()
      : null;

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      <input
        ref={inputRef}
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-disabled={disabled || undefined}
        disabled={disabled}
        autoComplete="off"
        placeholder={selected ? undefined : placeholder}
        value={displayValue}
        onChange={(event) => {
          setQuery(event.target.value);
          if (!open) {
            openRef.current = true;
            setOpen(true);
          }
        }}
        onFocus={openPanel}
        onClick={openPanel}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        className={cn(
          fieldClass,
          fieldSizeClass[size],
          "pr-9",
          disabled && "cursor-not-allowed opacity-60",
        )}
      />
      <span
        aria-hidden
        className="pointer-events-none absolute inset-y-0 right-0 flex w-9 items-center justify-center text-slate-muted"
      >
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
          <path
            d="M2.5 4.5L6 8L9.5 4.5"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>

      {open ? (
        <ul
          id={listId}
          role="listbox"
          className={cn(
            "absolute z-40 mt-1 w-full overflow-y-auto rounded-md border border-line bg-raised py-1 shadow-lg",
            LIST_MAX_HEIGHT,
          )}
          onMouseDown={(event) => event.preventDefault()}
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-sm text-slate-muted">
              {customHint
                ? `Tab or click away to use “${customHint}”`
                : emptyMessage}
            </li>
          ) : (
            <>
              {filtered.map((option, index) => {
                const active = index === highlight;
                const isSelected = option.value === value;
                return (
                  <li
                    key={option.value}
                    role="option"
                    aria-selected={isSelected}
                    aria-disabled={option.disabled || undefined}
                    className={cn(
                      "cursor-pointer px-3 py-2 text-sm",
                      option.disabled && "cursor-not-allowed opacity-50",
                      active && !option.disabled && "bg-teal/10 text-ink",
                      isSelected && !active && "font-medium text-teal",
                    )}
                    onMouseEnter={() => setHighlight(index)}
                    onMouseDown={(event) => {
                      event.preventDefault();
                      if (option.disabled) return;
                      commit(option.value);
                    }}
                  >
                    {option.label}
                  </li>
                );
              })}
              {customHint ? (
                <li className="border-t border-line px-3 py-2 text-sm text-slate-muted">
                  Tab or click away to use “{customHint}”
                </li>
              ) : null}
            </>
          )}
        </ul>
      ) : null}
    </div>
  );
}

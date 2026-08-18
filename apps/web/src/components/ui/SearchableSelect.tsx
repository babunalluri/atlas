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
  "w-full rounded-md border border-line bg-raised px-3 py-2 text-sm text-ink outline-none transition placeholder:text-slate-muted focus:border-teal focus:ring-2 focus:ring-teal/20";

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
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: SearchableSelectOption[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  emptyMessage?: string;
  /** When true, Enter commits typed text even if it is not in options. */
  allowCustom?: boolean;
}) {
  const reactId = useId();
  const listId = `${id ?? reactId}-listbox`;
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
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
  }, [open, query]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  function commitCustom(raw: string) {
    const trimmed = raw.trim();
    if (!trimmed) return;
    onChange(trimmed);
    setOpen(false);
    setQuery("");
  }

  function commit(next: string) {
    const option = options.find((item) => item.value === next);
    if (option && !option.disabled) {
      onChange(next);
      setOpen(false);
      setQuery("");
      return;
    }
    if (allowCustom) commitCustom(next);
  }

  function openPanel() {
    if (disabled) return;
    setOpen(true);
    setQuery("");
    requestAnimationFrame(() => inputRef.current?.select());
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (disabled) return;
    if (!open && (event.key === "ArrowDown" || event.key === "Enter")) {
      event.preventDefault();
      openPanel();
      return;
    }
    if (!open) return;

    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      setQuery("");
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlight((index) =>
        filtered.length === 0 ? 0 : Math.min(index + 1, filtered.length - 1),
      );
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlight((index) => Math.max(index - 1, 0));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const option = filtered[highlight];
      if (option) {
        commit(option.value);
      } else if (allowCustom && query.trim()) {
        commitCustom(query);
      }
    }
  }

  const displayValue = open
    ? query
    : (selected?.label ?? (allowCustom ? value : ""));

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
          if (!open) setOpen(true);
        }}
        onFocus={openPanel}
        onClick={openPanel}
        onKeyDown={onKeyDown}
        className={cn(fieldClass, "pr-9", disabled && "cursor-not-allowed opacity-60")}
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
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-sm text-slate-muted">
              {allowCustom && query.trim()
                ? `Press Enter to use “${query.trim()}”`
                : emptyMessage}
            </li>
          ) : (
            filtered.map((option, index) => {
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
                    commit(option.value);
                  }}
                >
                  {option.label}
                </li>
              );
            })
          )}
        </ul>
      ) : null}
    </div>
  );
}

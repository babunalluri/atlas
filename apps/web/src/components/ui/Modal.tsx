"use client";

import { useEffect, useRef } from "react";

import { CloseIcon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

/**
 * Centered modal dialog.
 *
 * Escape closes (unless disabled). Backdrop click closes only when
 * ``dismissOnBackdrop`` is true, and only on a press that starts on the
 * backdrop (never on a drag that merely ends there). The page behind cannot
 * scroll while the dialog is up.
 *
 * Tool windows (Options Lab / Signal / Chart) pass ``dismissOnBackdrop={false}``
 * so traders must use Close / Escape to return to the desk — an accidental
 * click on the dimmed rail must not wipe a live chain.
 */
export function Modal({
  title,
  subtitle,
  onClose,
  children,
  actions,
  className,
  dismissOnBackdrop = true,
  dismissOnEscape = true,
}: {
  title: string;
  subtitle?: string | null;
  onClose: () => void;
  children: React.ReactNode;
  /** Controls for the header row, beside the title (e.g. a chart button). */
  actions?: React.ReactNode;
  /** Override the default full-height tool size (e.g. a short settings body). */
  className?: string;
  /** Click the dimmed overlay to close. Off for trading tool windows. */
  dismissOnBackdrop?: boolean;
  /** Escape key closes. Keep on unless a nested editor owns Escape. */
  dismissOnEscape?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (dismissOnEscape) onClose();
        return;
      }
      // Keep Tab inside the dialog. `aria-modal` tells a screen reader the rest
      // of the page is inert, but it does not stop the browser tabbing into it,
      // so a keyboard user would otherwise walk out into the desk behind.
      if (event.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (focusable.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && (active === first || active === panel)) {
        event.preventDefault();
        last.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    // The page behind must not scroll while the dialog is up.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Return focus where it was, so closing a tool does not dump the caret at
    // the top of the workspace.
    const opener = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      opener?.focus?.();
    };
  }, [dismissOnEscape, onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4 backdrop-blur-sm"
      onMouseDown={(event) => {
        // Close only on the backdrop itself, never on a drag that ends there.
        if (
          dismissOnBackdrop &&
          event.target === event.currentTarget
        ) {
          onClose();
        }
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cn(
          "flex w-full flex-col overflow-hidden rounded-xl border border-line bg-raised shadow-2xl outline-none",
          className ?? "h-[92vh] max-w-[92rem]",
        )}
      >
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          {/* Actions sit immediately after the name, on the left: a control
              that acts on the instrument reads as part of naming it, not as
              window chrome next to Close. */}
          <div className="flex min-w-0 items-center gap-2.5">
            <h2 className="min-w-0 truncate font-display text-lg font-semibold tracking-tight">
              {title}
              {subtitle ? (
                <span className="ml-2 text-sm font-normal text-slate-muted">
                  {subtitle}
                </span>
              ) : null}
            </h2>
            {actions ? (
              <div className="flex shrink-0 items-center gap-2">{actions}</div>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            title="Close"
            className="inline-flex size-8 shrink-0 items-center justify-center self-start rounded-md border border-line bg-raised/70 text-slate-muted transition hover:border-line-strong hover:text-ink"
          >
            <CloseIcon className="size-4" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto p-3">{children}</div>
      </div>
    </div>
  );
}

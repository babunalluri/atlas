"use client";

import { useEffect, type ReactNode } from "react";

import { CloseIcon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

export function AdminFormDialog({
  title,
  subtitle,
  titleId,
  onClose,
  children,
  className,
  showCloseButton = false,
}: {
  title: string;
  subtitle?: string;
  titleId: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
  showCloseButton?: boolean;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !event.defaultPrevented) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-40 overflow-y-auto bg-ink/40"
      onClick={onClose}
    >
      <div className="flex min-h-full items-center justify-center p-4">
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          className={cn(
            "w-full max-w-md rounded-xl border border-line bg-canvas p-5 shadow-lg",
            className,
          )}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 id={titleId} className="font-display text-lg font-semibold">
                {title}
              </h2>
              {subtitle ? (
                <p className="mt-1 text-sm text-slate-muted">{subtitle}</p>
              ) : null}
            </div>
            {showCloseButton ? (
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="-mr-1 -mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-slate-muted hover:bg-fog/70 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fog focus-visible:ring-offset-1 focus-visible:ring-offset-canvas"
              >
                <CloseIcon className="h-4 w-4" />
              </button>
            ) : null}
          </div>
          <div className="mt-4">{children}</div>
        </div>
      </div>
    </div>
  );
}

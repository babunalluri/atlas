import type { PropsWithChildren } from "react";

import { cn } from "@/lib/utils";

/** Compact header action cluster for editor pages (Delete / Save / Publish). */
export function EditorActions({
  children,
  className,
}: PropsWithChildren<{ className?: string }>) {
  return (
    <div className={cn("flex items-center gap-1.5", className)}>{children}</div>
  );
}

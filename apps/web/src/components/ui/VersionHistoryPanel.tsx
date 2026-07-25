"use client";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { formatRelative } from "@/lib/utils";

export interface VersionHistoryItem {
  id: string;
  version: number;
  status: string;
  isLive: boolean;
  createdAt: string;
  /** Short secondary labels shown after status (mode, member count, etc.). */
  details?: string[];
}

export function VersionHistoryPanel({
  versions,
  busy = false,
  viewing,
  onRefresh,
  onView,
  onRestoreLive,
  onRestoreDraft,
  onCloseView,
}: {
  versions: VersionHistoryItem[];
  busy?: boolean;
  viewing?: React.ReactNode;
  onRefresh: () => void;
  onView: (version: VersionHistoryItem) => void;
  onRestoreLive: (version: VersionHistoryItem) => void;
  onRestoreDraft: (version: VersionHistoryItem) => void;
  onCloseView?: () => void;
}) {
  return (
    <section className="rounded-xl border border-line bg-raised/40 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Version history</h2>
          <p className="mt-0.5 text-xs text-slate-muted">
            Restore a previous snapshot as live, or clone it into a new draft.
          </p>
        </div>
        <Button
          size="sm"
          variant="secondary"
          onClick={onRefresh}
          disabled={busy}
        >
          Refresh
        </Button>
      </div>

      {versions.length === 0 ? (
        <p className="rounded-md border border-dashed border-line px-3 py-4 text-center text-sm text-slate-muted">
          No versions yet.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {versions.map((version) => (
            <li
              key={version.id}
              className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-canvas/40 px-2.5 py-2"
            >
              <span className="text-sm font-semibold">v{version.version}</span>
              <Badge
                tone={
                  version.status === "published"
                    ? "success"
                    : version.status === "validated"
                      ? "info"
                      : "warning"
                }
              >
                {version.status}
              </Badge>
              {version.isLive ? (
                <Badge tone="info" live>
                  live
                </Badge>
              ) : null}
              {(version.details ?? []).map((detail) => (
                <span key={detail} className="text-xs text-slate-muted">
                  {detail}
                </span>
              ))}
              <span className="text-xs text-slate-muted">
                {formatRelative(version.createdAt)}
              </span>
              <div className="ml-auto flex flex-wrap items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => onView(version)}
                >
                  View
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => onRestoreDraft(version)}
                >
                  Edit as draft
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy || version.isLive}
                  onClick={() => onRestoreLive(version)}
                >
                  Restore live
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {viewing ? (
        <div className="mt-3 rounded-md border border-line bg-canvas/60 p-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">Version details</h3>
            {onCloseView ? (
              <Button size="sm" variant="ghost" onClick={onCloseView}>
                Close
              </Button>
            ) : null}
          </div>
          {viewing}
        </div>
      ) : null}
    </section>
  );
}

"use client";

import { Link, useRouter } from "@/i18n/navigation";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button, buttonClassName } from "@/components/ui/Button";
import {
  CatalogControls,
  DEFAULT_CATALOG_QUERY,
  type CatalogQuery,
} from "@/components/ui/CatalogControls";
import {
  CloneIcon,
  HistoryIcon,
  PencilIcon,
  TrashIcon,
} from "@/components/ui/icons";
import {
  VersionHistoryPanel,
  type VersionHistoryItem,
} from "@/components/ui/VersionHistoryPanel";
import {
  cloneTeam,
  deleteTeam,
  getTeamVersion,
  listTeamCatalog,
  listTeamVersions,
  restoreTeamVersion,
} from "@/lib/api/admin";
import type {
  CatalogPage,
  TeamConfig,
  TeamSummary,
  TeamVersionDetail,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

function toSummary(team: TeamConfig): TeamSummary {
  return {
    id: team.id,
    name: team.name,
    slug: team.slug,
    status: team.status,
    mode: team.mode,
    memberCount: team.members.length,
    domain: team.domain,
    publishedVersion: team.publishedVersion,
    updatedAt: team.updatedAt,
  };
}

export function TeamList({
  initial,
}: {
  initial: CatalogPage<TeamSummary>;
}) {
  const router = useRouter();
  const { getAccessToken } = useAgentOsToken();
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState<CatalogQuery>(DEFAULT_CATALOG_QUERY);
  const [pageData, setPageData] = useState(initial);
  const [loading, setLoading] = useState(false);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(() => new Set());
  const [cloningIds, setCloningIds] = useState<Set<string>>(() => new Set());
  const skipInitialFetch = useRef(true);

  const [versionsTeam, setVersionsTeam] = useState<TeamSummary | null>(null);
  const [versions, setVersions] = useState<VersionHistoryItem[]>([]);
  const [versionBusy, setVersionBusy] = useState(false);
  const [versionError, setVersionError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<TeamVersionDetail | null>(null);

  useEffect(() => {
    if (skipInitialFetch.current) {
      skipInitialFetch.current = false;
      return;
    }
    let cancelled = false;
    const handle = window.setTimeout(() => {
      setLoading(true);
      void (async () => {
        try {
          const next = await listTeamCatalog(await getAccessToken(), {
            q: query.q,
            status: query.status,
            page: query.page,
            pageSize: query.pageSize,
          });
          if (cancelled) return;
          setPageData(next);
          setError(null);
        } catch (reason) {
          if (!cancelled) {
            setError(
              reason instanceof Error ? reason.message : "Failed to load teams",
            );
          }
        } finally {
          if (!cancelled) setLoading(false);
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [getAccessToken, query]);

  function closeVersions() {
    setVersionsTeam(null);
    setVersions([]);
    setVersionError(null);
    setViewing(null);
    setVersionBusy(false);
  }

  async function refreshVersions(team: TeamSummary) {
    setVersionBusy(true);
    setVersionError(null);
    try {
      const rows = await listTeamVersions(await getAccessToken(), team.id);
      setVersions(
        rows.map((row) => ({
          id: row.id,
          version: row.version,
          status: row.status,
          isLive: row.isLive,
          createdAt: row.createdAt,
          details: [row.mode, `${row.memberCount} members`],
        })),
      );
    } catch (reason) {
      setVersionError(
        reason instanceof Error ? reason.message : "Failed to load versions",
      );
    } finally {
      setVersionBusy(false);
    }
  }

  async function openVersions(team: TeamSummary) {
    setVersionsTeam(team);
    setVersions([]);
    setViewing(null);
    setVersionError(null);
    await refreshVersions(team);
  }

  async function viewVersion(version: VersionHistoryItem) {
    if (!versionsTeam) return;
    setVersionBusy(true);
    setVersionError(null);
    try {
      const detail = await getTeamVersion(
        await getAccessToken(),
        versionsTeam.id,
        version.id,
      );
      setViewing(detail);
    } catch (reason) {
      setVersionError(
        reason instanceof Error ? reason.message : "Failed to load version",
      );
    } finally {
      setVersionBusy(false);
    }
  }

  async function restoreLive(version: VersionHistoryItem) {
    if (!versionsTeam || version.isLive) return;
    if (
      !window.confirm(
        `Make v${version.version} the live published version? Current live stays available in history.`,
      )
    ) {
      return;
    }
    setVersionBusy(true);
    setVersionError(null);
    try {
      const restored = await restoreTeamVersion(
        await getAccessToken(),
        versionsTeam.id,
        version.id,
      );
      const summary = toSummary(restored);
      setPageData((prev) => ({
        ...prev,
        items: prev.items.map((item) =>
          item.id === summary.id ? summary : item,
        ),
      }));
      setVersionsTeam(summary);
      setViewing(null);
      await refreshVersions(summary);
    } catch (reason) {
      setVersionError(
        reason instanceof Error ? reason.message : "Restore failed",
      );
      setVersionBusy(false);
    }
  }

  async function restoreDraft(version: VersionHistoryItem) {
    if (!versionsTeam) return;
    if (
      !window.confirm(
        `Clone v${version.version} into a draft for editing? Live published version will not change until you publish.`,
      )
    ) {
      return;
    }
    setVersionBusy(true);
    setVersionError(null);
    try {
      const restored = await restoreTeamVersion(
        await getAccessToken(),
        versionsTeam.id,
        version.id,
        { asDraft: true },
      );
      const summary = toSummary(restored);
      setPageData((prev) => ({
        ...prev,
        items: prev.items.map((item) =>
          item.id === summary.id ? summary : item,
        ),
      }));
      closeVersions();
      router.push(`/admin/teams/${restored.id}`);
    } catch (reason) {
      setVersionError(
        reason instanceof Error ? reason.message : "Restore failed",
      );
      setVersionBusy(false);
    }
  }

  async function onClone(team: TeamSummary) {
    setCloningIds((current) => new Set(current).add(team.id));
    setError(null);
    try {
      const cloned = await cloneTeam(await getAccessToken(), team.id);
      router.push(`/admin/teams/${cloned.id}`);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to clone team",
      );
      setCloningIds((current) => {
        const next = new Set(current);
        next.delete(team.id);
        return next;
      });
    }
  }

  async function onDelete(team: TeamSummary) {
    if (
      !window.confirm(`Delete “${team.name}”? This cannot be undone.`)
    ) {
      return;
    }
    setDeletingIds((current) => new Set(current).add(team.id));
    setError(null);
    try {
      await deleteTeam(await getAccessToken(), team.id);
      setPageData((prev) => ({
        ...prev,
        items: prev.items.filter((item) => item.id !== team.id),
        total: Math.max(0, prev.total - 1),
      }));
      if (versionsTeam?.id === team.id) {
        closeVersions();
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to delete team",
      );
    } finally {
      setDeletingIds((current) => {
        const next = new Set(current);
        next.delete(team.id);
        return next;
      });
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">
            Teams
          </h1>
          <p className="mt-0.5 text-sm text-slate-muted">
            Group published agents so they can work together.
          </p>
        </div>
        <Link
          href="/admin/teams/new"
          className={buttonClassName({ variant: "accent" })}
        >
          Create
        </Link>
      </header>
      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <section className="table-shell rounded-xl">
        <CatalogControls
          query={query}
          total={pageData.total}
          noun="teams"
          loading={loading}
          onChange={setQuery}
        />
        <div className="hidden items-center gap-3 border-b border-line px-4 py-2 md:flex">
          <div className="grid min-w-0 flex-1 grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr] gap-3">
            <span className="th-label">Name</span>
            <span className="th-label">Mode</span>
            <span className="th-label">Status</span>
            <span className="th-label text-right">Updated</span>
          </div>
          <span className="th-label w-auto shrink-0 text-right">Actions</span>
        </div>
        <ul>
          {pageData.items.map((team) => (
            <li key={team.id} className="border-b border-line/60 last:border-0">
              <div className="flex items-center gap-3 px-4 py-2.5 transition hover:bg-mist/70">
                <Link
                  href={`/admin/teams/${team.id}`}
                  className="grid min-w-0 flex-1 items-center gap-3 md:grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr]"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{team.name}</p>
                    <p className="mono-cell truncate text-slate-muted">
                      {team.memberCount} agent{team.memberCount === 1 ? "" : "s"}
                    </p>
                  </div>
                  <p className="text-sm capitalize text-ink-soft">{team.mode}</p>
                  <div className="flex items-center gap-2">
                    <Badge
                      dot
                      tone={team.status === "published" ? "success" : "warning"}
                    >
                      {team.status}
                    </Badge>
                    {team.publishedVersion ? (
                      <span className="mono-cell text-slate-muted">
                        v{team.publishedVersion}
                      </span>
                    ) : null}
                  </div>
                  <p className="mono-cell text-right text-slate-muted">
                    {formatRelative(team.updatedAt)}
                  </p>
                </Link>
                <div className="flex shrink-0 items-center justify-end gap-0.5">
                  <Link
                    href={`/admin/teams/${team.id}`}
                    className={buttonClassName({
                      variant: "ghost",
                      size: "icon",
                    })}
                    aria-label={`Edit ${team.name}`}
                    title="Edit"
                  >
                    <PencilIcon />
                  </Link>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Versions for ${team.name}`}
                    title="Versions"
                    onClick={() => void openVersions(team)}
                  >
                    <HistoryIcon />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Clone ${team.name}`}
                    title="Clone"
                    disabled={cloningIds.has(team.id)}
                    onClick={() => void onClone(team)}
                  >
                    {cloningIds.has(team.id) ? "…" : <CloneIcon />}
                  </Button>
                  <Button
                    size="icon"
                    variant="danger"
                    aria-label={`Delete ${team.name}`}
                    title="Delete"
                    disabled={deletingIds.has(team.id)}
                    onClick={() => void onDelete(team)}
                  >
                    {deletingIds.has(team.id) ? "…" : <TrashIcon />}
                  </Button>
                </div>
              </div>
            </li>
          ))}
          {pageData.items.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-slate-muted">
              {query.q || query.status !== "all"
                ? "No teams match this search."
                : "No teams yet — create one above, then add published agents."}
            </li>
          ) : null}
        </ul>
      </section>

      {versionsTeam ? (
        <div
          className="fixed inset-0 z-40 flex items-end justify-center p-4 sm:items-center"
          role="dialog"
          aria-modal="true"
          aria-labelledby="team-versions-title"
        >
          <button
            type="button"
            className="absolute inset-0 bg-ink/40"
            aria-label="Close version history"
            onClick={closeVersions}
          />
          <div className="relative z-10 flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-line bg-canvas shadow-lg">
            <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
              <div className="min-w-0">
                <h2
                  id="team-versions-title"
                  className="truncate text-sm font-semibold"
                >
                  Versions · {versionsTeam.name}
                </h2>
                <p className="mt-0.5 truncate text-xs text-slate-muted">
                  /{versionsTeam.slug}
                </p>
              </div>
              <Button size="sm" variant="ghost" onClick={closeVersions}>
                Close
              </Button>
            </div>
            <div className="space-y-3 overflow-y-auto p-4">
              {versionError ? (
                <p className="text-sm text-rose">{versionError}</p>
              ) : null}
              <VersionHistoryPanel
                versions={versions}
                busy={versionBusy}
                onRefresh={() => {
                  if (versionsTeam) void refreshVersions(versionsTeam);
                }}
                onView={(version) => void viewVersion(version)}
                onRestoreLive={(version) => void restoreLive(version)}
                onRestoreDraft={(version) => void restoreDraft(version)}
                onCloseView={() => setViewing(null)}
                viewing={
                  viewing ? (
                    <dl className="grid gap-2 text-sm sm:grid-cols-2">
                      <div>
                        <dt className="text-xs text-slate-muted">Mode</dt>
                        <dd>{viewing.mode}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-slate-muted">Model</dt>
                        <dd>{viewing.model}</dd>
                      </div>
                      <div className="sm:col-span-2">
                        <dt className="text-xs text-slate-muted">Instructions</dt>
                        <dd className="mt-0.5 whitespace-pre-wrap font-mono text-[13px]">
                          {viewing.instructions}
                        </dd>
                      </div>
                      <div className="sm:col-span-2">
                        <dt className="mb-1 text-xs text-slate-muted">Members</dt>
                        <dd>
                          <ul className="space-y-1">
                            {viewing.members.map((member) => (
                              <li key={member.agentConfigId} className="text-sm">
                                {member.position + 1}. {member.name}{" "}
                                <span className="text-xs text-slate-muted">
                                  (agent v{member.version})
                                </span>
                              </li>
                            ))}
                          </ul>
                        </dd>
                      </div>
                    </dl>
                  ) : null
                }
              />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

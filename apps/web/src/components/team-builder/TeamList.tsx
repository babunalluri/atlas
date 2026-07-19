"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import { createTeam, getTeam } from "@/lib/api/admin";
import type { TeamConfig, TeamMember, TeamSummary } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn, formatRelative } from "@/lib/utils";
import { slugifyName } from "@/lib/validation/agent-form";

function MemberRow({ member }: { member: TeamMember }) {
  return (
    <li className="rounded-lg border border-line/70 bg-raised/80 px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-ink">{member.name}</p>
          <p className="mono-cell truncate text-slate-muted">
            /{member.slug} · pinned v{member.version}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Badge
            tone={member.status === "published" ? "success" : "warning"}
            dot
          >
            {member.status}
          </Badge>
          <span className="mono-cell text-slate-muted">#{member.position + 1}</span>
        </div>
      </div>
      <Link
        href={`/admin/agents/${member.agentConfigId}`}
        className="mt-2 inline-flex text-xs font-medium text-teal hover:text-teal-bright"
      >
        View agent →
      </Link>
    </li>
  );
}

export function TeamList({ teams }: { teams: TeamSummary[] }) {
  const router = useRouter();
  const { getAccessToken } = useAgentOsToken();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(
    teams[0]?.id ?? null,
  );
  const [detail, setDetail] = useState<TeamConfig | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    void (async () => {
      try {
        const next = await getTeam(await getAccessToken(), selectedId);
        if (!cancelled) setDetail(next);
      } catch (reason) {
        if (!cancelled) {
          setDetail(null);
          setDetailError(
            reason instanceof Error ? reason.message : "Failed to load team",
          );
        }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, selectedId]);

  async function onCreate() {
    setBusy(true);
    setError(null);
    try {
      const token = await getAccessToken();
      const created = await createTeam(token, {
        name: name.trim() || "Untitled team",
        slug: slugifyName(name || `untitled-team-${Date.now()}`),
      });
      router.push(`/admin/teams/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create team");
    } finally {
      setBusy(false);
    }
  }

  const selectedSummary = teams.find((team) => team.id === selectedId);
  const members = detail?.members ?? [];

  return (
    <div className="space-y-8">
      <section className="surface-panel relative overflow-hidden rounded-2xl p-6 md:p-8">
        <div className="pointer-events-none absolute inset-0 grid-noise opacity-60" />
        <div className="relative grid gap-6 md:grid-cols-[1.4fr_1fr] md:items-end">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
              Multi-agent teams
            </p>
            <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight text-ink md:text-5xl">
              Bring tenant specialists together.
            </h1>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-slate-muted">
              Route requests to the right agent or coordinate several specialists,
              then publish a version with pinned agent releases.
            </p>
          </div>
          <div className="rounded-xl border border-line bg-raised/90 p-4">
            <Label htmlFor="new-team">New team</Label>
            <div className="flex gap-2">
              <Input
                id="new-team"
                placeholder="e.g. Customer success"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              <Button onClick={onCreate} disabled={busy} variant="accent">
                {busy ? "Creating…" : "Create"}
              </Button>
            </div>
            {error ? <p className="mt-2 text-xs text-rose">{error}</p> : null}
          </div>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(280px,0.85fr)]">
        <div className="table-shell rounded-xl">
          <div className="grid grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr] gap-3 border-b border-line px-4 py-2.5">
            <span className="th-label">Team</span>
            <span className="th-label">Mode</span>
            <span className="th-label">Status</span>
            <span className="th-label text-right">Updated</span>
          </div>
          <ul>
            {teams.map((team) => {
              const active = team.id === selectedId;
              return (
                <li key={team.id} className="border-b border-line/60 last:border-0">
                  <button
                    type="button"
                    onClick={() => setSelectedId(team.id)}
                    className={cn(
                      "grid w-full grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr] items-center gap-3 px-4 py-2.5 text-left transition",
                      active ? "bg-ink text-canvas" : "hover:bg-mist/70",
                    )}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{team.name}</p>
                      <p
                        className={cn(
                          "mono-cell truncate",
                          active ? "text-canvas/70" : "text-slate-muted",
                        )}
                      >
                        /{team.slug} · {team.memberCount} agents
                      </p>
                    </div>
                    <p
                      className={cn(
                        "text-sm capitalize",
                        active ? "text-canvas/80" : "text-ink-soft",
                      )}
                    >
                      {team.mode}
                    </p>
                    <div className="flex items-center gap-2">
                      <Badge
                        dot
                        tone={team.status === "published" ? "success" : "warning"}
                        className={active ? "bg-canvas/15 text-canvas" : undefined}
                      >
                        {team.status}
                      </Badge>
                      {team.publishedVersion ? (
                        <span
                          className={cn(
                            "mono-cell",
                            active ? "text-canvas/70" : "text-slate-muted",
                          )}
                        >
                          v{team.publishedVersion}
                        </span>
                      ) : null}
                    </div>
                    <p
                      className={cn(
                        "mono-cell text-right",
                        active ? "text-canvas/70" : "text-slate-muted",
                      )}
                    >
                      {formatRelative(team.updatedAt)}
                    </p>
                  </button>
                </li>
              );
            })}
            {teams.length === 0 ? (
              <li className="px-4 py-10 text-center text-sm text-slate-muted">
                No teams yet — create one above and add published agents.
              </li>
            ) : null}
          </ul>
        </div>

        <aside className="table-shell flex min-h-[320px] flex-col rounded-xl">
          {selectedSummary ? (
            <>
              <div className="border-b border-line px-4 py-3">
                <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-muted">
                  Selected team
                </p>
                <div className="mt-1 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate font-display text-xl font-semibold">
                      {selectedSummary.name}
                    </h2>
                    <p className="mono-cell truncate text-slate-muted">
                      /{selectedSummary.slug} · {selectedSummary.mode}
                    </p>
                  </div>
                  <Link
                    href={`/admin/teams/${selectedSummary.id}`}
                    className="shrink-0 rounded-md border border-line bg-raised px-2.5 py-1 text-xs font-medium text-ink hover:bg-mist"
                  >
                    Open editor
                  </Link>
                </div>
              </div>
              <div className="flex-1 space-y-3 px-4 py-4">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-ink">Assigned agents</h3>
                  {!detailLoading && detail ? (
                    <span className="mono-cell text-slate-muted">
                      {members.length} member{members.length === 1 ? "" : "s"}
                    </span>
                  ) : null}
                </div>
                {detailLoading ? (
                  <p className="text-sm text-slate-muted">Loading agents…</p>
                ) : detailError ? (
                  <p className="text-sm text-rose">{detailError}</p>
                ) : members.length > 0 ? (
                  <ul className="space-y-2">
                    {members
                      .slice()
                      .sort((a, b) => a.position - b.position)
                      .map((member) => (
                        <MemberRow
                          key={`${member.agentConfigId}-${member.agentVersionId}`}
                          member={member}
                        />
                      ))}
                  </ul>
                ) : (
                  <p className="rounded-lg border border-dashed border-line px-3 py-6 text-center text-sm text-slate-muted">
                    No agents assigned yet. Open the editor to add published agents.
                  </p>
                )}
              </div>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center px-4 py-10 text-center text-sm text-slate-muted">
              Select a team to see its assigned agents.
            </div>
          )}
        </aside>
      </section>
    </div>
  );
}

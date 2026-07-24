"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PublicApiShareEmbed } from "@/components/security/PublicApiShareEmbed";
import { PublicApiTestBed } from "@/components/security/PublicApiTestBed";
import { listPublicApiRunCatalog } from "@/lib/api/admin";
import {
  publicApiCatalogEmptyHint,
  type PublicApiCatalogRow,
} from "@/lib/api/public-api-catalog";
import { useAgentOsToken } from "@/lib/auth/token";

export function PublicApiPanel() {
  const { getAccessToken } = useAgentOsToken();
  const [error, setError] = useState<string | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalog, setCatalog] = useState<PublicApiCatalogRow[]>([]);
  const [emptyHint, setEmptyHint] = useState<string | null>(null);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");
  const [selectedTeamId, setSelectedTeamId] = useState("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setCatalogLoading(true);
      try {
        const load = await listPublicApiRunCatalog(await getAccessToken());
        if (cancelled) return;
        setCatalog(load.rows);
        setEmptyHint(publicApiCatalogEmptyHint(load) || null);
        if (load.rows[0]) {
          setSelectedWorkflowId(load.rows[0].workflow.id);
          setSelectedTeamId(load.rows[0].teams[0]?.id ?? "");
        } else {
          setSelectedWorkflowId("");
          setSelectedTeamId("");
        }
      } catch (reason) {
        if (!cancelled) {
          setCatalog([]);
          setEmptyHint(null);
          setSelectedWorkflowId("");
          setSelectedTeamId("");
          setError(
            reason instanceof Error
              ? reason.message
              : "Failed to load workflows",
          );
        }
      } finally {
        if (!cancelled) setCatalogLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken]);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">
            Public API
          </h1>
          <p className="mt-0.5 text-sm text-slate-muted">
            Share hosted customer chat embeds, or paste a service account token
            to test runs against a published workflow and team.{" "}
            <Link
              href="/admin/service-accounts"
              className="text-teal hover:text-teal-bright"
            >
              Get a token from Service accounts
            </Link>
            .
          </p>
        </div>
      </header>
      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <PublicApiShareEmbed />

      <PublicApiTestBed
        catalog={catalog}
        catalogLoading={catalogLoading}
        emptyHint={emptyHint}
        workflowId={selectedWorkflowId}
        teamId={selectedTeamId}
        onWorkflowChange={setSelectedWorkflowId}
        onTeamChange={setSelectedTeamId}
      />
    </div>
  );
}

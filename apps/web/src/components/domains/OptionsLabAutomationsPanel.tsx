"use client";

import { useCallback, useEffect, useState } from "react";

import {
  STRATEGY_TEMPLATES,
  type StrategyTemplateId,
} from "@/components/domains/options-lab-strategy";
import { Button } from "@/components/ui/Button";
import { useAgentOsToken } from "@/lib/auth/token";
import {
  createOptionsLabBot,
  deleteOptionsLabBot,
  evaluateOptionsLabBots,
  listOptionsLabBots,
  runOptionsLabBot,
  updateOptionsLabBot,
  type OptionsLabBot,
} from "@/lib/api/admin";
import { cn } from "@/lib/utils";

const TEMPLATE_CHOICES = STRATEGY_TEMPLATES.filter((t) => !t.gated);

/**
 * Automations desk tab — server-persisted bots.
 * Armed paper runs via OPTIONS_LAB_BOTS_ENABLED worker (~60s) or Evaluate nudge.
 * Live never auto-fires; Run once always confirms.
 */
export function OptionsLabAutomationsPanel() {
  const { getAccessToken } = useAgentOsToken();
  const [bots, setBots] = useState<OptionsLabBot[]>([]);
  const [name, setName] = useState("My iron condor bot");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [workerEnabled, setWorkerEnabled] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const token = await getAccessToken();
      if (!token) {
        setNote("Sign in to load bots.");
        return;
      }
      const res = await listOptionsLabBots(token);
      if (!res.ok) {
        setNote(res.error ?? "Failed to load bots.");
        return;
      }
      setBots(res.bots ?? []);
      setWorkerEnabled(Boolean(res.worker_enabled));
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Failed to load bots.");
    } finally {
      setLoading(false);
    }
  }, [getAccessToken]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function addBot() {
    const token = await getAccessToken();
    if (!token) return;
    const res = await createOptionsLabBot(token, {
      name: name.trim() || "Untitled bot",
      enabled: false,
      mode: "paper",
      template: "iron_condor",
      profit_pct: 50,
      stop_pct: 40,
      avoid_events: true,
      max_dte_hold: 1,
    });
    if (!res.ok) {
      setNote(res.error ?? "Create failed.");
      return;
    }
    setNote(`Created “${res.bot?.name}”.`);
    await refresh();
  }

  async function patchBot(botId: string, payload: Parameters<typeof updateOptionsLabBot>[2]) {
    const token = await getAccessToken();
    if (!token) return;
    const res = await updateOptionsLabBot(token, botId, payload);
    if (!res.ok) {
      setNote(res.error ?? "Update failed.");
      return;
    }
    await refresh();
  }

  async function runOnce(bot: OptionsLabBot) {
    const live = bot.mode === "live";
    const ok = window.confirm(
      live
        ? `Run “${bot.name}” LIVE once?\n\nTemplate ${bot.template || bot.backtest_id || "legs"} · SL ${bot.stop_pct}% · TP ${bot.profit_pct}%.\nHITL: places broker orders if live tools are bound.`
        : `Run “${bot.name}” once (paper)?\n\nUses current Options Lab underlying/chain + entry gates.`,
    );
    if (!ok) return;
    setBusyId(bot.id);
    try {
      const token = await getAccessToken();
      if (!token) throw new Error("Not signed in.");
      const res = await runOptionsLabBot(token, bot.id, { confirm: true });
      setNote(res.message || res.error || (res.ok ? "Run OK." : "Run failed."));
      await refresh();
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Run failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function nudgeEvaluate() {
    const token = await getAccessToken();
    if (!token) return;
    setNote("Evaluating armed paper bots…");
    const res = await evaluateOptionsLabBots(token);
    if (!res.ok) {
      setNote(res.error ?? "Evaluate failed.");
      return;
    }
    const fired = (res.results ?? []).filter((r) => r.ok && !r.skipped).length;
    const skipped = (res.results ?? []).filter((r) => r.skipped).length;
    setNote(`Evaluate done — ${fired} ran, ${skipped} skipped.`);
    await refresh();
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-auto p-3">
      <p className="text-sm text-slate-muted">
        Bots persist server-side (session). Armed{" "}
        <span className="font-medium text-ink">paper</span> evaluates on the bots
        worker when <code className="text-[11px]">OPTIONS_LAB_BOTS_ENABLED</code>{" "}
        {workerEnabled ? (
          <span className="text-teal">is on</span>
        ) : (
          <span className="text-rose">is off</span>
        )}
        — or use Evaluate now (exits first, then entries). Live never auto-fires.
        Default SL 40%. New bots default to event-avoid + DTE flat at 1d.
      </p>
      {note ? (
        <p className="rounded-md border border-line bg-canvas/50 px-2 py-1.5 text-xs text-slate-muted">
          {note}
        </p>
      ) : null}
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-slate-muted">
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="ml-1 rounded border border-line bg-canvas px-2 py-1 text-sm text-ink"
          />
        </label>
        <Button type="button" size="sm" onClick={() => void addBot()}>
          Add bot
        </Button>
        <Button type="button" size="sm" variant="secondary" onClick={() => void nudgeEvaluate()}>
          Evaluate now
        </Button>
      </div>
      {loading ? (
        <p className="py-8 text-center text-sm text-slate-muted">Loading bots…</p>
      ) : bots.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-muted">
          No bots yet — add one or Create bot from a saved Backtest.
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-lg border border-line">
          {bots.map((bot) => (
            <li
              key={bot.id}
              className="flex flex-col gap-2 px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <p className="text-sm font-semibold text-ink">{bot.name}</p>
                <p className="text-xs text-slate-muted">
                  {bot.template || bot.backtest_id || "legs"} · TP {bot.profit_pct}% · SL{" "}
                  {bot.stop_pct}% · {bot.mode}
                  {bot.avoid_events ? " · event-avoid" : ""}
                  {bot.max_dte_hold != null ? ` · flat≤${bot.max_dte_hold}d` : ""}
                  {bot.open_position ? " · OPEN" : ""}
                  {bot.kill
                    ? " · KILL"
                    : bot.enabled
                      ? bot.mode === "paper"
                        ? " · armed (server)"
                        : " · armed (manual only)"
                      : ""}
                  {bot.runs_today != null
                    ? ` · today ${bot.runs_today}/${bot.max_runs_per_day ?? 3}`
                    : ""}
                </p>
                {bot.last_run_message ? (
                  <p className="mt-1 text-xs text-slate-muted">{bot.last_run_message}</p>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={bot.mode}
                  onChange={(e) =>
                    void patchBot(bot.id, {
                      mode: e.target.value as "paper" | "live",
                    })
                  }
                  className="rounded border border-line bg-canvas px-2 py-1 text-xs"
                >
                  <option value="paper">paper</option>
                  <option value="live">live</option>
                </select>
                <select
                  value={bot.template || ""}
                  disabled={Boolean(bot.backtest_id)}
                  onChange={(e) =>
                    void patchBot(bot.id, {
                      template: e.target.value as StrategyTemplateId,
                    })
                  }
                  className="max-w-[9rem] rounded border border-line bg-canvas px-2 py-1 text-xs"
                >
                  {TEMPLATE_CHOICES.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className={cn(
                    "rounded px-2 py-1 text-xs font-medium",
                    bot.avoid_events ? "bg-teal/20 text-teal" : "bg-fog text-slate-muted",
                  )}
                  onClick={() =>
                    void patchBot(bot.id, { avoid_events: !bot.avoid_events })
                  }
                >
                  {bot.avoid_events ? "Avoid evt" : "Any day"}
                </button>
                <label className="flex items-center gap-1 text-xs text-slate-muted">
                  Flat≤
                  <input
                    type="number"
                    min={0}
                    max={30}
                    defaultValue={bot.max_dte_hold ?? ""}
                    key={`${bot.id}-dte-${bot.max_dte_hold ?? "x"}`}
                    placeholder="—"
                    onBlur={(e) => {
                      const raw = e.target.value.trim();
                      const next = raw === "" ? null : Number(raw);
                      if (next === bot.max_dte_hold) return;
                      if (next != null && !Number.isFinite(next)) return;
                      void patchBot(bot.id, { max_dte_hold: next });
                    }}
                    className="w-12 rounded border border-line bg-canvas px-1 py-0.5 text-ink"
                  />
                  d
                </label>
                {bot.open_position ? (
                  <button
                    type="button"
                    className="rounded px-2 py-1 text-xs font-medium bg-rose/15 text-rose"
                    onClick={() => {
                      if (
                        !window.confirm(
                          `Clear tracked OPEN book for “${bot.name}”?\n\nOnly clears Lab tracking — does not place broker exits.`,
                        )
                      ) {
                        return;
                      }
                      void patchBot(bot.id, { clear_open_position: true });
                    }}
                  >
                    Clear OPEN
                  </button>
                ) : null}
                <button
                  type="button"
                  className={cn(
                    "rounded px-2 py-1 text-xs font-medium",
                    bot.enabled && !bot.kill
                      ? "bg-teal/20 text-teal"
                      : "bg-fog text-slate-muted",
                  )}
                  onClick={() =>
                    void patchBot(bot.id, {
                      enabled: !bot.enabled,
                      kill: false,
                    })
                  }
                >
                  {bot.enabled && !bot.kill ? "Armed" : "Disarmed"}
                </button>
                <button
                  type="button"
                  className={cn(
                    "rounded px-2 py-1 text-xs font-medium",
                    bot.kill ? "bg-rose/20 text-rose" : "bg-fog text-slate-muted",
                  )}
                  onClick={() => void patchBot(bot.id, { kill: !bot.kill })}
                >
                  {bot.kill ? "Killed" : "Kill"}
                </button>
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  disabled={busyId === bot.id || Boolean(bot.kill)}
                  onClick={() => void runOnce(bot)}
                >
                  {busyId === bot.id ? "Running…" : "Run once"}
                </Button>
                <button
                  type="button"
                  className="text-xs text-rose hover:underline"
                  onClick={async () => {
                    const token = await getAccessToken();
                    if (!token) return;
                    await deleteOptionsLabBot(token, bot.id);
                    await refresh();
                  }}
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";

import {
  STRATEGY_TEMPLATES,
  type StrategyTemplateId,
} from "@/components/domains/options-lab-strategy";
import { Button } from "@/components/ui/Button";
import {
  BellIcon,
  CloseIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RefreshIcon,
  StopIcon,
  TrashIcon,
} from "@/components/ui/icons";
import { useAgentOsToken } from "@/lib/auth/token";
import {
  createOptionsLabBot,
  deleteOptionsLabBot,
  evaluateOptionsLabBots,
  getOptionsLabBrokerReconcile,
  getOptionsLabConfig,
  listOptionsLabBots,
  runOptionsLabBot,
  updateOptionsLabBot,
  type OptionsLabBot,
  type OptionsLabBrokerReconcileResponse,
} from "@/lib/api/admin";
import { cn } from "@/lib/utils";

const TEMPLATE_CHOICES = STRATEGY_TEMPLATES.filter((t) => !t.gated);

/** Match Button sm height so selects/inputs/time align on one baseline. */
const fieldClass =
  "box-border h-8 rounded-md border border-line bg-canvas px-2 text-xs font-medium leading-none tracking-tight text-ink";
const fieldNarrowClass = cn(fieldClass, "w-14 px-1.5 text-center");
const fieldTimeClass = cn(fieldClass, "w-[7.25rem] px-1.5");
const btnAlignClass = "h-8";
const rowClass = "flex min-h-8 flex-wrap items-center gap-2";
const labelClass = "inline-flex h-8 items-center gap-1.5 text-xs leading-none text-slate-muted";
const tagClass = "inline-flex h-8 shrink-0 items-center text-xs font-medium leading-none text-ink";

function formatCash(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  return `₹${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

/**
 * Bot overlay — server-persisted paper/live bots.
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
  const [labMock, setLabMock] = useState(false);
  const [reconcile, setReconcile] = useState<OptionsLabBrokerReconcileResponse | null>(
    null,
  );
  const [reconcileBusy, setReconcileBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const token = await getAccessToken();
      if (!token) {
        setNote("Sign in to load bots.");
        return;
      }
      const [res, cfg] = await Promise.all([
        listOptionsLabBots(token),
        getOptionsLabConfig(token),
      ]);
      if (!res.ok) {
        setNote(res.error ?? "Failed to load bots.");
        return;
      }
      setBots(res.bots ?? []);
      setWorkerEnabled(Boolean(res.worker_enabled));
      if (cfg.ok) {
        setLabMock(Boolean(cfg.config?.mock));
      }
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

  async function refreshReconcile() {
    setReconcileBusy(true);
    try {
      const token = await getAccessToken();
      if (!token) {
        setNote("Sign in to reconcile broker book.");
        return;
      }
      const res = await getOptionsLabBrokerReconcile(token);
      setReconcile(res);
      if (!res.ok) {
        setNote(res.error ?? "Reconcile failed.");
      }
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Reconcile failed.");
    } finally {
      setReconcileBusy(false);
    }
  }

  const diffSummary = reconcile?.diff?.summary;

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
      <div className="rounded-lg border border-line bg-canvas/40 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm font-medium text-ink">Broker book (read-only)</p>
            <p className="text-xs text-slate-muted">
              Positions (lots) + available / used margin vs Lab books and bot OPEN legs.
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            icon={<RefreshIcon />}
            disabled={reconcileBusy || labMock}
            title={labMock ? "Disable Lab mock for broker reconcile" : undefined}
            onClick={() => void refreshReconcile()}
          >
            {reconcileBusy ? "Checking…" : "Reconcile"}
          </Button>
        </div>
        {reconcile ? (
          <div className="mt-2 space-y-1.5 text-xs text-slate-muted">
            <p>
              Avail{" "}
              <span className="font-medium text-ink">
                {formatCash(reconcile.margin?.available_cash)}
              </span>
              {" · used "}
              <span className="font-medium text-ink">
                {formatCash(reconcile.margin?.used_margin)}
              </span>
              {reconcile.margin?.utilization_pct != null
                ? ` · ${reconcile.margin.utilization_pct}% util`
                : ""}
              {reconcile.margin?.source ? ` · ${reconcile.margin.source}` : ""}
              {reconcile.broker_ready === false ? " · broker not bound" : ""}
              {diffSummary ? (
                <>
                  {" "}
                  ·{" "}
                  {diffSummary.in_sync ? (
                    <span className="text-teal">in sync</span>
                  ) : (
                    <span className="text-rose">
                      {diffSummary.broker_only +
                        diffSummary.lab_only +
                        diffSummary.qty_mismatch}{" "}
                      mismatch
                      {diffSummary.matched
                        ? ` · ${diffSummary.matched} matched`
                        : ""}
                    </span>
                  )}
                  {" · qty in lots"}
                </>
              ) : null}
            </p>
            {(reconcile.diff?.broker_only?.length ||
              reconcile.diff?.lab_only?.length ||
              reconcile.diff?.qty_mismatch?.length) ? (
              <ul className="max-h-28 space-y-0.5 overflow-auto font-mono text-[11px]">
                {(reconcile.diff?.broker_only ?? []).map((row) => (
                  <li key={`b-${row.symbol}`}>
                    broker-only {row.symbol} {row.broker_lots ?? row.broker_qty} lot
                    {row.broker_shares != null ? ` (${row.broker_shares} sh)` : ""}
                  </li>
                ))}
                {(reconcile.diff?.lab_only ?? []).map((row) => (
                  <li key={`l-${row.symbol}`}>
                    lab-only {row.symbol} {row.lab_lots ?? row.lab_qty} lot
                    {row.lab_shares != null ? ` (${row.lab_shares} sh)` : ""}
                  </li>
                ))}
                {(reconcile.diff?.qty_mismatch ?? []).map((row) => (
                  <li key={`q-${row.symbol}`}>
                    qty {row.symbol} broker {row.broker_lots ?? row.broker_qty} vs lab{" "}
                    {row.lab_lots ?? row.lab_qty} lots
                    {row.lot_size != null ? ` · lot ${row.lot_size}` : ""}
                    {row.lab_unit_guess || row.lab_unit
                      ? ` · lab ${row.lab_unit_guess ?? row.lab_unit}`
                      : ""}
                  </li>
                ))}
              </ul>
            ) : null}
            {reconcile.warnings?.length ? (
              <p className="text-[11px] text-slate-muted/80">
                {reconcile.warnings.slice(0, 2).join(" · ")}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className={rowClass}>
        <label className={labelClass}>
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={cn(fieldClass, "min-w-[12rem]")}
          />
        </label>
        <Button
          type="button"
          size="sm"
          className={btnAlignClass}
          icon={<PlusIcon />}
          onClick={() => void addBot()}
        >
          Add bot
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className={btnAlignClass}
          icon={<RefreshIcon />}
          onClick={() => void nudgeEvaluate()}
        >
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
            <li key={bot.id} className="flex flex-col gap-2.5 px-3 py-2.5">
              <div className="min-w-0">
                <p className="text-sm font-medium text-ink">{bot.name}</p>
                <p className="text-xs text-slate-muted">
                  {bot.template || bot.backtest_id || "legs"} · TP {bot.profit_pct}% · SL{" "}
                  {bot.stop_pct}% · {bot.mode}
                  {bot.avoid_events ? " · event-avoid" : ""}
                  {bot.max_dte_hold != null ? ` · flat≤${bot.max_dte_hold}d` : ""}
                  {bot.entry?.min_ivp != null ? ` · IVP≥${bot.entry.min_ivp}` : ""}
                  {bot.entry?.max_dte != null ? ` · enter≤${bot.entry.max_dte}d` : ""}
                  {bot.schedule?.window_start
                    ? ` · ${bot.schedule.window_start}–${bot.schedule.window_end ?? "15:30"}`
                    : ""}
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

              <div className="flex flex-col gap-2">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <div className={cn(rowClass, "min-w-0")}>
                    <span className={tagClass}>Window</span>
                    <label className={labelClass}>
                      <input
                        type="time"
                        defaultValue={bot.schedule?.window_start ?? "09:15"}
                        key={`${bot.id}-ws-${bot.schedule?.window_start ?? "x"}`}
                        onBlur={(e) => {
                          const next = e.target.value || "09:15";
                          if (next === (bot.schedule?.window_start ?? "09:15")) return;
                          void patchBot(bot.id, {
                            schedule: {
                              ...(bot.schedule ?? {}),
                              window_start: next,
                              window_end: bot.schedule?.window_end ?? "15:30",
                              days: bot.schedule?.days ?? [0, 1, 2, 3, 4],
                            },
                          });
                        }}
                        className={fieldTimeClass}
                      />
                      <span>–</span>
                      <input
                        type="time"
                        defaultValue={bot.schedule?.window_end ?? "15:30"}
                        key={`${bot.id}-we-${bot.schedule?.window_end ?? "x"}`}
                        onBlur={(e) => {
                          const next = e.target.value || "15:30";
                          if (next === (bot.schedule?.window_end ?? "15:30")) return;
                          void patchBot(bot.id, {
                            schedule: {
                              ...(bot.schedule ?? {}),
                              window_end: next,
                              window_start: bot.schedule?.window_start ?? "09:15",
                              days: bot.schedule?.days ?? [0, 1, 2, 3, 4],
                            },
                          });
                        }}
                        className={fieldTimeClass}
                      />
                      <span>IST</span>
                    </label>
                  </div>
                  <div className={cn(rowClass, "sm:justify-end")}>
                    {bot.open_position ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="danger"
                        icon={<CloseIcon />}
                        className={btnAlignClass}
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
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      icon={bot.enabled && !bot.kill ? <PlayIcon /> : <PauseIcon />}
                      className={cn(
                        btnAlignClass,
                        bot.enabled && !bot.kill
                          ? "border-teal/40 bg-teal/10 text-teal hover:border-teal/55 hover:bg-teal/20"
                          : undefined,
                      )}
                      onClick={() =>
                        void patchBot(bot.id, {
                          enabled: !bot.enabled,
                          kill: false,
                        })
                      }
                    >
                      {bot.enabled && !bot.kill ? "Armed" : "Disarmed"}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      icon={<StopIcon />}
                      className={cn(
                        btnAlignClass,
                        bot.kill
                          ? "border-rose/40 bg-rose/10 text-rose hover:border-rose/55 hover:bg-rose/15"
                          : undefined,
                      )}
                      onClick={() => void patchBot(bot.id, { kill: !bot.kill })}
                    >
                      {bot.kill ? "Killed" : "Kill"}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      icon={<PlayIcon />}
                      className={btnAlignClass}
                      disabled={busyId === bot.id || Boolean(bot.kill)}
                      onClick={() => void runOnce(bot)}
                    >
                      {busyId === bot.id ? "Running…" : "Run once"}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="danger"
                      icon={<TrashIcon />}
                      className={btnAlignClass}
                      onClick={async () => {
                        const token = await getAccessToken();
                        if (!token) return;
                        await deleteOptionsLabBot(token, bot.id);
                        await refresh();
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                </div>

                <div className={cn(rowClass, "border-t border-line/60 pt-2")}>
                  <select
                    value={bot.mode}
                    onChange={(e) =>
                      void patchBot(bot.id, {
                        mode: e.target.value as "paper" | "live",
                      })
                    }
                    className={fieldClass}
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
                    className={cn(fieldClass, "max-w-[9rem] disabled:opacity-50")}
                  >
                    {TEMPLATE_CHOICES.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    icon={<BellIcon />}
                    className={cn(
                      btnAlignClass,
                      bot.avoid_events
                        ? "border-teal/40 bg-teal/10 text-teal hover:border-teal/55 hover:bg-teal/20"
                        : undefined,
                    )}
                    onClick={() =>
                      void patchBot(bot.id, { avoid_events: !bot.avoid_events })
                    }
                  >
                    {bot.avoid_events ? "Avoid evt" : "Any day"}
                  </Button>
                  <label className={labelClass}>
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
                      className={fieldNarrowClass}
                    />
                    d
                  </label>
                  <span className="mx-1 hidden h-4 w-px shrink-0 bg-line sm:block" aria-hidden />
                  <span className={tagClass}>Entry</span>
                  <label className={labelClass}>
                    IVP≥
                    <input
                      type="number"
                      min={0}
                      max={100}
                      defaultValue={bot.entry?.min_ivp ?? ""}
                      key={`${bot.id}-minivp-${bot.entry?.min_ivp ?? "x"}`}
                      placeholder="—"
                      onBlur={(e) => {
                        const raw = e.target.value.trim();
                        const next = raw === "" ? null : Number(raw);
                        if (next === (bot.entry?.min_ivp ?? null)) return;
                        void patchBot(bot.id, {
                          entry: { ...(bot.entry ?? {}), min_ivp: next },
                        });
                      }}
                      className={fieldNarrowClass}
                    />
                  </label>
                  <label className={labelClass}>
                    enter≤
                    <input
                      type="number"
                      min={0}
                      max={45}
                      defaultValue={bot.entry?.max_dte ?? ""}
                      key={`${bot.id}-maxdte-${bot.entry?.max_dte ?? "x"}`}
                      placeholder="—"
                      onBlur={(e) => {
                        const raw = e.target.value.trim();
                        const next = raw === "" ? null : Number(raw);
                        if (next === (bot.entry?.max_dte ?? null)) return;
                        void patchBot(bot.id, {
                          entry: { ...(bot.entry ?? {}), max_dte: next },
                        });
                      }}
                      className={fieldNarrowClass}
                    />
                    d
                  </label>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

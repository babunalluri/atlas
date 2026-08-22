"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  STRATEGY_TEMPLATES,
  buildStrategyFromTemplate,
  estimateLotSize,
  type StrategyTemplateId,
} from "@/components/domains/options-lab-strategy";
import { Button } from "@/components/ui/Button";
import { useAgentOsToken } from "@/lib/auth/token";
import {
  getOptionsChain,
  getOptionsLabConfig,
  postOptionsLabOrders,
} from "@/lib/api/admin";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "atlas-options-lab-bots-v1";
/** Session runner tick while Automations tab is open. */
const SESSION_TICK_MS = 60_000;
/** Min gap between auto paper runs for the same bot. */
const PAPER_COOLDOWN_MS = 5 * 60_000;
/** Cap auto fires per Automations tab session (audit: avoid ~78 positions/day). */
const MAX_SESSION_AUTO_RUNS = 3;

type BotDraft = {
  id: string;
  name: string;
  enabled: boolean;
  mode: "paper" | "live";
  template: StrategyTemplateId;
  profitPct: number;
  stopPct: number;
  lastRunAt?: number;
  lastRunMessage?: string;
  sessionAutoRuns?: number;
};

const TEMPLATE_CHOICES = STRATEGY_TEMPLATES.filter((t) => !t.gated);

function loadBots(): BotDraft[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map((row) => {
      const templateId = TEMPLATE_CHOICES.some((t) => t.id === row.template)
        ? row.template
        : "iron_condor";
      // sessionAutoRuns is tab-session only — never restore from localStorage.
      return {
        id: String(row.id ?? `bot-${Date.now()}`),
        name: String(row.name ?? "Untitled bot"),
        enabled: Boolean(row.enabled),
        mode: row.mode === "live" ? "live" : "paper",
        template: templateId as StrategyTemplateId,
        profitPct: Number(row.profitPct) || 50,
        stopPct: Number(row.stopPct) || 40,
        lastRunAt: typeof row.lastRunAt === "number" ? row.lastRunAt : undefined,
        lastRunMessage:
          typeof row.lastRunMessage === "string" ? row.lastRunMessage : undefined,
        sessionAutoRuns: 0,
      };
    });
  } catch {
    return [];
  }
}

function saveBots(bots: BotDraft[]) {
  try {
    const durable = bots.map((bot) => ({
      id: bot.id,
      name: bot.name,
      enabled: bot.enabled,
      mode: bot.mode,
      template: bot.template,
      profitPct: bot.profitPct,
      stopPct: bot.stopPct,
      lastRunAt: bot.lastRunAt,
      lastRunMessage: bot.lastRunMessage,
    }));
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(durable));
  } catch {
    // ignore
  }
}

/**
 * Automations desk tab — local drafts + Run once + session runner for armed paper.
 * Live never auto-fires; always requires confirm on Run once.
 */
export function OptionsLabAutomationsPanel() {
  const { getAccessToken } = useAgentOsToken();
  const [bots, setBots] = useState<BotDraft[]>([]);
  const [name, setName] = useState("My iron condor bot");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [runnerNote, setRunnerNote] = useState<string | null>(null);
  const botsRef = useRef<BotDraft[]>([]);
  const busyRef = useRef<string | null>(null);
  const runningAuto = useRef(false);

  useEffect(() => {
    const loaded = loadBots();
    setBots(loaded);
    botsRef.current = loaded;
  }, []);

  function persist(next: BotDraft[]) {
    botsRef.current = next;
    setBots(next);
    saveBots(next);
  }

  function addBot() {
    const bot: BotDraft = {
      id: `bot-${Date.now()}`,
      name: name.trim() || "Untitled bot",
      enabled: false,
      mode: "paper",
      template: "iron_condor",
      profitPct: 50,
      stopPct: 40,
    };
    persist([bot, ...botsRef.current]);
  }

  const executeBot = useCallback(
    async (bot: BotDraft, opts: { auto: boolean }) => {
      const auto = opts.auto;
      if (busyRef.current) return;
      if (auto && bot.mode === "live") {
        // Never auto-fire live.
        return;
      }
      if (!auto) {
        const live = bot.mode === "live";
        const ok = window.confirm(
          live
            ? `Run “${bot.name}” LIVE once?\n\nTemplate ${bot.template} · SL ${bot.stopPct}% · TP ${bot.profitPct}%.\nHITL: this places broker orders if live tools are bound.`
            : `Run “${bot.name}” once (paper preferred)?\n\nTemplate ${bot.template} · SL ${bot.stopPct}% · TP ${bot.profitPct}%.\nUses current Options Lab underlying/chain.`,
        );
        if (!ok) return;
      }

      busyRef.current = bot.id;
      setBusyId(bot.id);
      try {
        const token = await getAccessToken();
        if (!token) {
          throw new Error("Not signed in.");
        }
        const cfg = await getOptionsLabConfig(token);
        if (!cfg.ok || !cfg.config?.underlying_symbol) {
          throw new Error(
            cfg.error || "Set an Options Lab underlying first (Options Lab tab).",
          );
        }
        const config = cfg.config;
        const chain = await getOptionsChain(token, 12);
        if (!chain.ok || chain.atm == null) {
          throw new Error(chain.error || "Failed to load options chain.");
        }
        const lotSize = estimateLotSize(config.underlying_symbol || config.underlying_label);
        const legs = buildStrategyFromTemplate(bot.template, {
          atm: chain.atm,
          strikeStep: config.strike_step ?? chain.strike_step ?? 50,
          rows: chain.rows ?? [],
          widthSteps: 1,
        });
        if (!legs.length) {
          throw new Error("Template built no legs — check chain wings/ATM.");
        }
        const payload = legs.map((leg) => {
          const row = (chain.rows ?? []).find((r) => r.strike === leg.strike);
          const side = leg.type === "CE" ? row?.ce : row?.pe;
          return {
            id: leg.id,
            side: leg.side,
            type: leg.type,
            strike: leg.strike,
            qty: leg.qty,
            entry_premium: leg.premium ?? 0,
            premium: leg.premium ?? 0,
            symbol: side?.symbol || undefined,
          };
        });
        if (payload.some((l) => !l.symbol)) {
          throw new Error("Some legs lack symbols — widen wings or pick another template.");
        }

        const live = bot.mode === "live";
        const wantExit =
          (bot.stopPct > 0 || bot.profitPct > 0) && bot.mode === "live";
        // Audit F1: auto GTT only on MARKET — use MARKET when SL/TP requested on live.
        const orderType = wantExit ? "MARKET" : "LIMIT";
        let res;
        if (live) {
          res = await postOptionsLabOrders(token, {
            legs: payload,
            confirm: true,
            live: true,
            lot_size: lotSize,
            product: "NRML",
            order_type: orderType,
            name: bot.name,
            underlying_symbol: config.underlying_symbol,
            save_draft: true,
            mock: false,
            stop_loss_pct: bot.stopPct > 0 ? Math.min(90, bot.stopPct) : undefined,
            target_pct: bot.profitPct > 0 ? Math.min(200, bot.profitPct) : undefined,
          });
        } else {
          res = await postOptionsLabOrders(token, {
            legs: payload,
            confirm: true,
            live: false,
            lot_size: lotSize,
            product: "NRML",
            order_type: "LIMIT",
            name: bot.name,
            underlying_symbol: config.underlying_symbol,
            save_draft: true,
            mock: Boolean(chain.mock || config.mock),
            stop_loss_pct: bot.stopPct > 0 ? Math.min(90, bot.stopPct) : undefined,
            target_pct: bot.profitPct > 0 ? Math.min(200, bot.profitPct) : undefined,
          });
          if (
            !auto &&
            !res.ok &&
            !res.partial &&
            typeof res.error === "string" &&
            res.error.includes("live=true")
          ) {
            const liveOk = window.confirm(
              "No paper place tool — send LIVE place_order for this bot run?",
            );
            if (!liveOk) {
              throw new Error("Cancelled — live place not confirmed.");
            }
            res = await postOptionsLabOrders(token, {
              legs: payload,
              confirm: true,
              live: true,
              lot_size: lotSize,
              product: "NRML",
              order_type: orderType,
              name: bot.name,
              underlying_symbol: config.underlying_symbol,
              save_draft: true,
              mock: false,
              stop_loss_pct: bot.stopPct > 0 ? Math.min(90, bot.stopPct) : undefined,
              target_pct: bot.profitPct > 0 ? Math.min(200, bot.profitPct) : undefined,
            });
          }
        }

        const prefix = auto ? "Session: " : "";
        const msg = res.ok
          ? res.mock
            ? `${prefix}Mock run OK + draft saved.`
            : `${prefix}Submitted via ${res.tool || "broker"} (${res.submitted_count ?? "?"} legs).`
          : res.error || res.errors?.join("; ") || "Run failed.";

        persist(
          botsRef.current.map((b) =>
            b.id === bot.id
              ? {
                  ...b,
                  lastRunAt: Date.now(),
                  lastRunMessage: msg,
                  enabled: res.ok ? b.enabled : false,
                  sessionAutoRuns: auto
                    ? (b.sessionAutoRuns ?? 0) + (res.ok || res.partial ? 1 : 0)
                    : b.sessionAutoRuns,
                }
              : b,
          ),
        );
        if (auto) {
          setRunnerNote(`${bot.name}: ${msg}`);
        }
        if (!res.ok && !res.partial) {
          throw new Error(msg);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "Run failed.";
        persist(
          botsRef.current.map((b) =>
            b.id === bot.id
              ? {
                  ...b,
                  lastRunAt: Date.now(),
                  lastRunMessage: auto ? `Session: ${message}` : message,
                  enabled: false,
                }
              : b,
          ),
        );
        if (auto) {
          setRunnerNote(`${bot.name}: ${message}`);
        }
      } finally {
        busyRef.current = null;
        setBusyId(null);
      }
    },
    [getAccessToken],
  );

  useEffect(() => {
    const tick = async () => {
      if (runningAuto.current || busyRef.current) return;
      const armedPaper = botsRef.current.filter(
        (b) => b.enabled && b.mode === "paper",
      );
      if (!armedPaper.length) {
        setRunnerNote((prev) =>
          prev?.startsWith("Idle") ? prev : "Idle — no armed paper bots.",
        );
        return;
      }
      const now = Date.now();
      const due = armedPaper.find(
        (b) =>
          (b.sessionAutoRuns ?? 0) < MAX_SESSION_AUTO_RUNS &&
          (!b.lastRunAt || now - b.lastRunAt >= PAPER_COOLDOWN_MS),
      );
      if (!due) {
        const capped = armedPaper.every(
          (b) => (b.sessionAutoRuns ?? 0) >= MAX_SESSION_AUTO_RUNS,
        );
        setRunnerNote(
          capped
            ? `Armed paper hit session cap (${MAX_SESSION_AUTO_RUNS} auto-runs). Live never auto-runs.`
            : `Armed paper waiting cooldown (${Math.round(PAPER_COOLDOWN_MS / 60000)}m). Live never auto-runs.`,
        );
        return;
      }
      runningAuto.current = true;
      try {
        setRunnerNote(`Running armed paper “${due.name}”…`);
        await executeBot(due, { auto: true });
      } finally {
        runningAuto.current = false;
      }
    };

    setRunnerNote("Session runner active (armed paper only, 5m cooldown). First auto tick in 60s.");
    // Do not fire on mount — give the operator time to disarm before paper auto-runs.
    const timer = window.setInterval(() => void tick(), SESSION_TICK_MS);
    return () => {
      window.clearInterval(timer);
      setRunnerNote(null);
    };
  }, [executeBot]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-auto p-3">
      <p className="text-sm text-slate-muted">
        Automations drafts store locally. While this tab is open, armed{" "}
        <span className="font-medium text-ink">paper</span> bots auto-run on a session
        interval (5m cooldown, max {MAX_SESSION_AUTO_RUNS}/session). Live never auto-fires —
        Run once always confirms. Default SL is 40% (capped at 90%).
      </p>
      {runnerNote ? (
        <p className="rounded-md border border-line bg-canvas/50 px-2 py-1.5 text-xs text-slate-muted">
          {runnerNote}
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
        <Button type="button" size="sm" onClick={addBot}>
          Add bot
        </Button>
      </div>
      {bots.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-muted">No bots yet.</p>
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
                  {bot.template} · TP {bot.profitPct}% · SL {bot.stopPct}% · {bot.mode}
                  {bot.enabled
                    ? bot.mode === "paper"
                      ? " · armed (session)"
                      : " · armed (manual only)"
                    : ""}
                </p>
                {bot.lastRunMessage ? (
                  <p className="mt-1 text-xs text-slate-muted">{bot.lastRunMessage}</p>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={bot.mode}
                  onChange={(e) =>
                    persist(
                      botsRef.current.map((b) =>
                        b.id === bot.id
                          ? { ...b, mode: e.target.value as "paper" | "live" }
                          : b,
                      ),
                    )
                  }
                  className="rounded border border-line bg-canvas px-2 py-1 text-xs"
                >
                  <option value="paper">paper</option>
                  <option value="live">live</option>
                </select>
                <select
                  value={bot.template}
                  onChange={(e) =>
                    persist(
                      botsRef.current.map((b) =>
                        b.id === bot.id
                          ? { ...b, template: e.target.value as StrategyTemplateId }
                          : b,
                      ),
                    )
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
                    bot.enabled ? "bg-teal/20 text-teal" : "bg-fog text-slate-muted",
                  )}
                  onClick={() =>
                    persist(
                      botsRef.current.map((b) =>
                        b.id === bot.id ? { ...b, enabled: !b.enabled } : b,
                      ),
                    )
                  }
                >
                  {bot.enabled ? "Armed" : "Disarmed"}
                </button>
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  disabled={busyId === bot.id}
                  onClick={() => void executeBot(bot, { auto: false })}
                >
                  {busyId === bot.id ? "Running…" : "Run once"}
                </Button>
                <button
                  type="button"
                  className="text-xs text-rose hover:underline"
                  onClick={() => persist(botsRef.current.filter((b) => b.id !== bot.id))}
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

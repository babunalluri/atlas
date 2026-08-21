"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  formatMtm,
  mtmTone,
} from "@/components/domains/options-lab-portfolio";
import { Button } from "@/components/ui/Button";
import { Label } from "@/components/ui/Field";
import { RefreshIcon, TrashIcon, UploadIcon } from "@/components/ui/icons";
import {
  createOptionsPortfolio,
  deleteOptionsPortfolio,
  importOptionsPortfolioFromKite,
  listOptionsPortfolios,
  markOptionsPortfolio,
  type OptionsPortfolio,
  type OptionsPortfolioMarkResponse,
} from "@/lib/api/admin";
import { cn } from "@/lib/utils";

const POLL_MS = 5_000;

function formatNum(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(digits);
}

function formatTime(ts: number | null | undefined) {
  if (ts == null) return "—";
  return new Date(ts * 1000).toLocaleTimeString();
}

export function OptionsLabPortfoliosPanel({
  active,
  getAccessToken,
  configReady,
  underlyingSymbol,
  underlyingLabel,
  futSymbol,
  strikeStep,
  mock,
  pendingSave,
  onSaved,
}: {
  active: boolean;
  getAccessToken: () => Promise<string | null>;
  configReady: boolean;
  underlyingSymbol: string;
  underlyingLabel: string;
  futSymbol: string;
  strikeStep: number;
  mock: boolean;
  pendingSave?: {
    name: string;
    legs: Array<{
      id: string;
      side: "buy" | "sell";
      type: "CE" | "PE";
      strike: number;
      qty: number;
      entry_premium: number;
    }>;
  } | null;
  onSaved?: () => void;
}) {
  const [portfolios, setPortfolios] = useState<OptionsPortfolio[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mark, setMark] = useState<OptionsPortfolioMarkResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [newName, setNewName] = useState("");
  const mounted = useRef(true);
  const markSeq = useRef(0);

  const selected = portfolios.find((row) => row.id === selectedId) ?? null;

  const refreshList = useCallback(async () => {
    try {
      const token = await getAccessToken();
      if (!token) return;
      const res = await listOptionsPortfolios(token);
      if (!mounted.current) return;
      if (!res.ok) {
        setError(res.error ?? "Failed to load portfolios");
        return;
      }
      setPortfolios(res.portfolios ?? []);
      setError(null);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : "Failed to load portfolios");
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [getAccessToken]);

  const refreshMark = useCallback(
    async (portfolioId: string) => {
      const seq = ++markSeq.current;
      try {
        const token = await getAccessToken();
        if (!token) return;
        const res = await markOptionsPortfolio(token, portfolioId);
        if (!mounted.current || seq !== markSeq.current) return;
        if (!res.ok) {
          setError(res.error ?? "Mark-to-market failed");
          setMark(null);
          return;
        }
        setMark(res);
        setError(null);
      } catch (err) {
        if (!mounted.current || seq !== markSeq.current) return;
        setError(err instanceof Error ? err.message : "Mark-to-market failed");
      }
    },
    [getAccessToken],
  );

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!active || !configReady) return;
    setLoading(true);
    void refreshList();
  }, [active, configReady, refreshList]);

  useEffect(() => {
    if (!active || !configReady || !selectedId) return;
    void refreshMark(selectedId);
    const timer = window.setInterval(() => void refreshMark(selectedId), POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, configReady, refreshMark, selectedId, mock]);

  useEffect(() => {
    if (!portfolios.length) {
      setSelectedId(null);
      setMark(null);
      return;
    }
    if (!selectedId || !portfolios.some((row) => row.id === selectedId)) {
      setSelectedId(portfolios[0]?.id ?? null);
    }
  }, [portfolios, selectedId]);

  useEffect(() => {
    if (!pendingSave?.legs.length) return;
    setNewName(pendingSave.name);
  }, [pendingSave]);

  async function onCreateFromBuilder() {
    if (!pendingSave?.legs.length) return;
    setSaving(true);
    setError(null);
    try {
      const token = await getAccessToken();
      if (!token) return;
      const res = await createOptionsPortfolio(token, {
        name: newName.trim() || pendingSave.name,
        underlying_symbol: underlyingSymbol,
        underlying_label: underlyingLabel,
        fut_symbol: futSymbol,
        strike_step: strikeStep,
        source: "builder",
        legs: pendingSave.legs,
      });
      if (!res.ok || !res.portfolio) {
        setError(res.error ?? "Save failed");
        return;
      }
      await refreshList();
      setSelectedId(res.portfolio.id);
      onSaved?.();
      setNewName("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function onImportKite() {
    setImporting(true);
    setError(null);
    try {
      const token = await getAccessToken();
      if (!token) return;
      const res = await importOptionsPortfolioFromKite(token);
      if (!res.ok || !res.portfolio) {
        setError(res.error ?? "Import failed");
        return;
      }
      await refreshList();
      setSelectedId(res.portfolio.id);
      if (res.mark?.ok) setMark(res.mark);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setImporting(false);
    }
  }

  async function onDelete(portfolioId: string) {
    const token = await getAccessToken();
    if (!token) return;
    const res = await deleteOptionsPortfolio(token, portfolioId);
    if (!res.ok) {
      setError(res.error ?? "Delete failed");
      return;
    }
    await refreshList();
  }

  const totalMtm = mark?.summary?.total_mtm ?? null;
  const mtmClass =
    mtmTone(totalMtm) === "profit"
      ? "text-teal"
      : mtmTone(totalMtm) === "loss"
        ? "text-rose"
        : "text-ink";

  return (
    <div className="mt-3 flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-slate-muted">
            Draft portfolios with live mark-to-market. Save from Builder or import open F&O
            options from Kite.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            icon={<UploadIcon />}
            disabled={importing || mock}
            onClick={() => void onImportKite()}
            title={mock ? "Disable mock for Kite import" : undefined}
          >
            {importing ? "Importing…" : "Import from Kite"}
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={!selectedId}
            onClick={() => selectedId && void refreshMark(selectedId)}
          >
            <RefreshIcon className="mr-1.5 size-3.5" />
            Refresh MTM
          </Button>
        </div>
      </div>

      {pendingSave?.legs.length ? (
        <div className="rounded-lg border border-teal/30 bg-teal/5 p-3">
          <p className="text-sm font-medium text-ink">Save builder strategy</p>
          <div className="mt-2 flex flex-wrap items-end gap-3">
            <div className="min-w-[12rem] flex-1">
              <Label htmlFor="portfolio-name">Portfolio name</Label>
              <input
                id="portfolio-name"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                className="mt-1 w-full rounded-md border border-line bg-raised px-3 py-2 text-sm text-ink"
                placeholder={pendingSave.name}
              />
            </div>
            <Button
              type="button"
              size="sm"
              disabled={saving}
              onClick={() => void onCreateFromBuilder()}
            >
              {saving ? "Saving…" : "Save to portfolio"}
            </Button>
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="rounded-lg border border-rose/30 bg-rose/10 px-3 py-2 text-sm text-rose">
          {error}
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
        <div className="overflow-auto rounded-lg border border-line">
          {loading ? (
            <p className="p-4 text-sm text-slate-muted">Loading portfolios…</p>
          ) : portfolios.length === 0 ? (
            <p className="p-4 text-sm text-slate-muted">
              No draft portfolios yet. Save a strategy from Builder or import from Kite.
            </p>
          ) : (
            <ul className="divide-y divide-line/70">
              {portfolios.map((row) => (
                <li key={row.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(row.id)}
                    className={cn(
                      "flex w-full flex-col gap-0.5 px-3 py-2 text-left transition hover:bg-raised/40",
                      selectedId === row.id && "bg-teal/10",
                    )}
                  >
                    <span className="text-sm font-medium text-ink">{row.name}</span>
                    <span className="text-[11px] text-slate-muted">
                      {row.legs.length} legs · {row.source.replace("_", " ")}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="min-h-0 overflow-auto rounded-lg border border-line p-3">
          {!selected ? (
            <p className="py-8 text-center text-sm text-slate-muted">Select a portfolio</p>
          ) : (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line pb-3">
                <div>
                  <h3 className="font-display text-base font-semibold text-ink">{selected.name}</h3>
                  <p className="mt-0.5 text-xs text-slate-muted">
                    {selected.underlying_label || selected.underlying_symbol || "—"} ·{" "}
                    {selected.fut_symbol || "No FUT"} · updated{" "}
                    {formatTime(selected.updated_at)}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="text-rose"
                  icon={<TrashIcon />}
                  onClick={() => void onDelete(selected.id)}
                >
                  Delete
                </Button>
              </div>

              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                {[
                  ["Total MTM", formatMtm(totalMtm), mtmClass],
                  ["Net entry", formatNum(mark?.summary?.net_entry_cash), "text-ink"],
                  [
                    "Marked at",
                    formatTime(mark?.marked_at),
                    "text-slate-muted text-sm font-normal",
                  ],
                ].map(([label, value, tone]) => (
                  <div
                    key={label}
                    className="rounded-md border border-line/70 bg-canvas/50 px-3 py-2"
                  >
                    <div className="text-[10px] uppercase tracking-wide text-slate-muted">
                      {label}
                    </div>
                    <div className={cn("font-display text-lg font-semibold tabular-nums", tone)}>
                      {value}
                    </div>
                  </div>
                ))}
              </div>

              {(mark?.summary?.missing_quotes ?? 0) > 0 ? (
                <p className="mt-2 text-xs text-amber-700">
                  {mark?.summary?.missing_quotes} leg(s) missing live quotes — check FUT symbol and
                  Kite binding.
                </p>
              ) : null}

              <div className="mt-3 overflow-auto rounded-lg border border-line/70">
                <table className="min-w-full border-collapse text-left text-xs">
                  <thead className="bg-raised/90 text-[10px] uppercase tracking-wide text-slate-muted">
                    <tr>
                      <th className="px-3 py-2">Leg</th>
                      <th className="px-3 py-2">Strike</th>
                      <th className="px-3 py-2">Entry</th>
                      <th className="px-3 py-2">LTP</th>
                      <th className="px-3 py-2">MTM</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(mark?.legs ?? selected.legs).map((leg) => {
                      const tone = mtmTone(leg.mtm);
                      return (
                        <tr key={leg.id} className="border-t border-line/70">
                          <td className="px-3 py-2 capitalize">
                            {leg.side} {leg.type}
                            {leg.qty !== 1 ? ` × ${leg.qty}` : ""}
                          </td>
                          <td className="px-3 py-2 tabular-nums">{leg.strike}</td>
                          <td className="px-3 py-2 tabular-nums">
                            {formatNum(leg.entry_premium)}
                          </td>
                          <td className="px-3 py-2 tabular-nums">
                            {formatNum(leg.current_premium)}
                          </td>
                          <td
                            className={cn(
                              "px-3 py-2 tabular-nums font-semibold",
                              tone === "profit" && "text-teal",
                              tone === "loss" && "text-rose",
                            )}
                          >
                            {formatMtm(leg.mtm)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

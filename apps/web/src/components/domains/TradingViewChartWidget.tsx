"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { ChartLineIcon, PlayIcon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

const PRESETS = [
  { label: "NIFTY", symbol: "NSE:NIFTY" },
  { label: "BANKNIFTY", symbol: "NSE:BANKNIFTY" },
  { label: "RELIANCE", symbol: "NSE:RELIANCE" },
  { label: "BTC", symbol: "BINANCE:BTCUSDT" },
];

/** Map desk underlying / preset symbols to TradingView tickers. */
export function toTradingViewSymbol(raw: string | null | undefined): string {
  const s = (raw || "").trim().toUpperCase();
  if (!s) return "NSE:NIFTY";
  const aliases: Record<string, string> = {
    "NSE:NIFTY 50": "NSE:NIFTY",
    "NIFTY 50": "NSE:NIFTY",
    NIFTY: "NSE:NIFTY",
    BANKNIFTY: "NSE:BANKNIFTY",
    "NSE:BANKNIFTY": "NSE:BANKNIFTY",
    FINNIFTY: "NSE:FINNIFTY",
    "NSE:FINNIFTY": "NSE:FINNIFTY",
    MIDCPNIFTY: "NSE:MIDCPNIFTY",
    "NSE:MIDCPNIFTY": "NSE:MIDCPNIFTY",
    NIFTYNXT50: "NSE:NIFTYNXT50",
    "NSE:NIFTYNXT50": "NSE:NIFTYNXT50",
    SENSEX: "BSE:SENSEX",
    "BSE:SENSEX": "BSE:SENSEX",
  };
  if (aliases[s]) return aliases[s];
  if (s.includes(":")) return s.replace(/\s+/g, "");
  return `NSE:${s.replace(/\s+/g, "")}`;
}

export function tradingViewChartUrl(raw: string | null | undefined): string {
  const symbol = toTradingViewSymbol(raw);
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}`;
}

/** Desk-sized TradingView popup (~70% screen, centered — not fullscreen). */
export function openTradingViewChartWindow(raw: string | null | undefined) {
  const url = tradingViewChartUrl(raw);
  const availW = window.screen.availWidth || 1440;
  const availH = window.screen.availHeight || 900;
  const width = Math.min(1100, Math.max(880, Math.round(availW * 0.7)));
  const height = Math.min(720, Math.max(560, Math.round(availH * 0.72)));
  const left = Math.max(0, Math.round((availW - width) / 2));
  const top = Math.max(0, Math.round((availH - height) / 2));
  const features = [
    "popup=yes",
    `width=${width}`,
    `height=${height}`,
    `left=${left}`,
    `top=${top}`,
  ].join(",");

  // Open blank first so we can enforce size before navigating cross-origin.
  // Drop opener before navigate — features cannot include noopener on about:blank.
  const win = window.open("about:blank", "atlas-tradingview", features);
  if (!win) {
    window.open(url, "_blank", "noopener,noreferrer");
    return;
  }
  try {
    win.resizeTo(width, height);
    win.moveTo(left, top);
  } catch {
    // Some browsers ignore resize; features still applied on first create.
  }
  win.opener = null;
  win.location.href = url;
  win.focus();
}

export function TradingViewChartWidget() {
  const [draft, setDraft] = useState("NSE:NIFTY");
  const [symbol, setSymbol] = useState("NSE:NIFTY");

  function loadSymbol(next: string) {
    const trimmed = toTradingViewSymbol(next);
    if (!trimmed) return;
    setDraft(trimmed);
    setSymbol(trimmed);
  }

  return (
    <section className="surface-panel overflow-hidden rounded-xl">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
        <div>
          <p className="th-label">Market chart</p>
          <p className="mt-0.5 text-[11px] text-slate-muted">
            TradingView · {symbol}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {PRESETS.map((preset) => (
            <button
              key={preset.symbol}
              type="button"
              onClick={() => loadSymbol(preset.symbol)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-[11px] font-medium",
                symbol === preset.symbol
                  ? "border-teal/40 bg-teal/10 text-teal"
                  : "border-line text-slate-muted hover:text-ink",
              )}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") loadSymbol(draft);
          }}
          spellCheck={false}
          className="min-w-[10rem] flex-1 rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs outline-none focus-visible:ring-2 focus-visible:ring-teal/25"
          placeholder="NSE:NIFTY"
          aria-label="Chart symbol"
        />
        <Button
          size="sm"
          variant="secondary"
          icon={<PlayIcon />}
          onClick={() => loadSymbol(draft)}
        >
          Load
        </Button>
        <Button
          type="button"
          size="sm"
          variant="primary"
          icon={<ChartLineIcon />}
          onClick={() => openTradingViewChartWindow(symbol)}
        >
          Open chart
        </Button>
      </div>
    </section>
  );
}

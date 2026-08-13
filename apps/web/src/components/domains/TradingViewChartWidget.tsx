"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { useSurfaceTheme } from "@/components/layout/ThemeToggle";
import { cn } from "@/lib/utils";

const PRESETS = [
  { label: "NIFTY", symbol: "NSE:NIFTY" },
  { label: "BANKNIFTY", symbol: "NSE:BANKNIFTY" },
  { label: "RELIANCE", symbol: "NSE:RELIANCE" },
  { label: "BTC", symbol: "BINANCE:BTCUSDT" },
];

const INTERVALS = [
  { label: "5m", value: "5" },
  { label: "15m", value: "15" },
  { label: "1h", value: "60" },
  { label: "D", value: "D" },
  { label: "W", value: "W" },
];

function chartSrc(symbol: string, interval: string, dark: boolean) {
  const params = new URLSearchParams({
    symbol,
    interval,
    timezone: "Asia/Kolkata",
    theme: dark ? "dark" : "light",
    style: "1",
    locale: "en",
    toolbarbg: dark ? "0f172a" : "f8fafc",
    hideideas: "1",
    hide_legend: "0",
    hide_side_toolbar: "0",
    allow_symbol_change: "1",
    withdateranges: "1",
    saveimage: "0",
    studies: "[]",
  });
  return `https://s.tradingview.com/widgetembed/?${params.toString()}`;
}

export function TradingViewChartWidget() {
  const { theme } = useSurfaceTheme("admin");
  const dark = theme === "dark";
  const [draft, setDraft] = useState("NSE:NIFTY");
  const [symbol, setSymbol] = useState("NSE:NIFTY");
  const [interval, setInterval] = useState("15");

  function loadSymbol(next: string) {
    const trimmed = next.trim().toUpperCase();
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
            TradingView candles · {symbol} · {interval === "D" || interval === "W" ? interval : `${interval}m`}
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
          className="min-w-[10rem] flex-1 rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs outline-none focus:ring-2 focus:ring-teal/25"
          placeholder="NSE:NIFTY"
          aria-label="Chart symbol"
        />
        <Button size="sm" variant="secondary" onClick={() => loadSymbol(draft)}>
          Load
        </Button>
        <div className="flex gap-1">
          {INTERVALS.map((item) => (
            <button
              key={item.value}
              type="button"
              onClick={() => setInterval(item.value)}
              className={cn(
                "rounded-md px-2 py-1 text-[11px] font-medium",
                interval === item.value
                  ? "bg-ink text-canvas"
                  : "text-slate-muted hover:bg-fog/70 hover:text-ink",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <iframe
        key={`${symbol}:${interval}:${dark ? "dark" : "light"}`}
        title={`${symbol} TradingView chart`}
        src={chartSrc(symbol, interval, dark)}
        className="h-[26rem] w-full border-0"
      />
    </section>
  );
}

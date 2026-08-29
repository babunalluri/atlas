"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { RefreshIcon } from "@/components/ui/icons";
import type { DomainDashboard, DomainDeskBook } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

/**
 * Broker-style account nav for the trading desk's main panel.
 *
 * Dashboard · Orders · Holdings · Positions · Bids · Funds, with Bids opening a
 * sub-nav of Corporate actions / SSE IPO — the shape a customer already knows
 * from their broker.
 *
 * Every number comes from the user's own broker through the desk snapshot
 * (`GET /api/desk?desk_snapshot=true`), which calls whatever read capabilities
 * the toolkit bound on the Live trading team exposes. Nothing here is seeded or
 * synthesized: a book the broker cannot answer renders its own empty hint, so
 * "no holdings" and "holdings not readable" never look alike.
 */

/** Nav order follows the broker's, not the API's. */
const NAV_ORDER = ["orders", "holdings", "positions", "funds"] as const;

type NavKey = "dashboard" | "bids" | (typeof NAV_ORDER)[number] | string;

function formatFetchedAt(value: string | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString();
}

/** `record_date` / `equity.available.cash` → "Record date" / "Equity available cash". */
function humanize(text: string) {
  const spaced = text.replaceAll("_", " ").replaceAll(".", " ").trim();
  if (!spaced) return "—";
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function cellValue(value: Cell) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

type Cell = string | number | boolean | null | undefined;

function isNumericLike(value: Cell) {
  if (typeof value === "number") return true;
  if (typeof value !== "string") return false;
  return /^-?[\d,.]+%?$/.test(value.trim());
}

function asNumber(value: Cell): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const parsed = Number(value.replace(/,/g, "").trim());
  return Number.isFinite(parsed) ? parsed : null;
}

/** Money in the grouping an Indian desk reads (₹1,00,000.00). */
function formatMoney(value: Cell) {
  const num = asNumber(value);
  if (num === null) return cellValue(value);
  return `₹${num.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** Tone for a P&L-ish number. Zero is flat, not a gain. */
function pnlTone(value: Cell) {
  const num = asNumber(value);
  if (num === null || num === 0) return "";
  return num > 0 ? "text-teal" : "text-rose";
}

const PNL_COLUMNS = new Set(["pnl", "change", "day_pnl", "unrealised", "total_pnl"]);

export function DeskBooksNav({
  data,
  loading = false,
  refreshing = false,
  error = null,
  onRefresh,
  className,
}: {
  /** Desk payload, fetched by the host so the chat rail shares the one call. */
  data: DomainDashboard | null;
  loading?: boolean;
  refreshing?: boolean;
  error?: string | null;
  onRefresh?: () => void;
  className?: string;
}) {
  const [active, setActive] = useState<NavKey>("dashboard");
  const [bidsTab, setBidsTab] = useState("corporate_actions");

  const books = useMemo(
    () => data?.desk_snapshot?.books ?? [],
    [data?.desk_snapshot?.books],
  );
  const byTab = useMemo(
    () => new Map(books.map((book) => [book.tab, book])),
    [books],
  );
  const bidsBooks = useMemo(
    () => books.filter((book) => book.group === "bids"),
    [books],
  );

  const navItems: Array<{ key: NavKey; label: string }> = [
    { key: "dashboard", label: "Dashboard" },
    ...NAV_ORDER.filter((tab) => byTab.has(tab)).map((tab) => ({
      key: tab as NavKey,
      label: byTab.get(tab)?.label ?? tab,
    })),
  ];
  // Keep the broker's order: Bids sits between Positions and Funds.
  if (bidsBooks.length) {
    const fundsAt = navItems.findIndex((item) => item.key === "funds");
    const bids = { key: "bids" as NavKey, label: "Bids" };
    if (fundsAt === -1) navItems.push(bids);
    else navItems.splice(fundsAt, 0, bids);
  }

  const currentBids =
    bidsBooks.find((book) => book.tab === bidsTab) ?? bidsBooks[0] ?? null;
  const snapshotError = data?.desk_snapshot?.error;

  return (
    <section className={cn("flex min-h-0 flex-col", className)}>
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-4 border-b border-line px-6">
        <nav
          role="tablist"
          aria-label="Broker account"
          className="flex flex-wrap items-center gap-2"
        >
          {navItems.map((item) => {
            const selected = active === item.key;
            return (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => setActive(item.key)}
                className={cn(
                  "-mb-px border-b-2 px-4 py-3.5 text-sm font-medium transition",
                  selected
                    ? "border-teal text-teal"
                    : "border-transparent text-ink-soft hover:text-ink",
                )}
              >
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-[11px] text-slate-muted">
            {formatFetchedAt(data?.fetched_at)}
          </span>
          {onRefresh ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={onRefresh}
              disabled={refreshing}
              aria-label="Refresh books"
              icon={<RefreshIcon className={cn(refreshing && "animate-spin")} />}
            >
              {refreshing ? "…" : "Refresh"}
            </Button>
          ) : null}
        </div>
      </div>

      {active === "bids" && bidsBooks.length ? (
        <div className="flex shrink-0 flex-wrap items-center gap-7 border-b border-line px-6 py-3">
          {bidsBooks.map((book) => {
            const selected = currentBids?.tab === book.tab;
            return (
              <button
                key={book.tab}
                type="button"
                onClick={() => setBidsTab(book.tab)}
                className={cn(
                  "text-sm transition",
                  selected
                    ? "font-medium text-ink"
                    : "text-slate-muted hover:text-ink",
                )}
              >
                {book.label}
              </button>
            );
          })}
        </div>
      ) : null}

      {error ? (
        <p className="mx-6 mt-4 shrink-0 rounded-md border border-rose/30 bg-rose/10 px-3 py-2 text-xs text-rose">
          {error}
        </p>
      ) : null}
      {snapshotError ? (
        <p className="mx-6 mt-4 shrink-0 rounded-md border border-amber/30 bg-amber/10 px-3 py-2 text-xs text-amber">
          {snapshotError}
        </p>
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto">
        {loading ? (
          <p className="px-6 py-12 text-sm text-slate-muted">
            Loading your broker books…
          </p>
        ) : active === "dashboard" ? (
          <DeskDashboard books={books} />
        ) : active === "funds" && byTab.has("funds") ? (
          <FundsCards book={byTab.get("funds")!} />
        ) : active === "bids" ? (
          currentBids ? (
            <BookTable book={currentBids} />
          ) : null
        ) : byTab.has(active) ? (
          <BookTable book={byTab.get(active)!} />
        ) : null}
      </div>
    </section>
  );
}

/**
 * Funds as cards, the way a broker shows a margin summary.
 *
 * A margin read is three or four numbers, and a two-column label/value table
 * made them read like debug output rather than an account balance.
 */
function FundsCards({ book }: { book: DomainDeskBook }) {
  if (book.error) {
    return <BookNotice tone="rose" text={book.error} via={book.via} />;
  }
  if (!book.rows.length) {
    return <BookNotice tone="muted" text={book.empty_hint} via={book.via} />;
  }

  const [lead, ...rest] = book.rows;

  return (
    <div className="px-6 py-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {[lead, ...rest].map((row, index) => (
          <div
            key={`${cellValue(row.label)}-${index}`}
            className={cn(
              "surface-panel rounded-xl px-4 py-3.5",
              index === 0 && "sm:col-span-2 xl:col-span-1",
            )}
          >
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-muted">
              {humanize(cellValue(row.label))}
            </p>
            <p
              className={cn(
                "mt-1.5 font-display tnum tabular-nums tracking-tight",
                index === 0 ? "text-3xl font-semibold" : "text-2xl font-semibold",
              )}
            >
              {formatMoney(row.value)}
            </p>
          </div>
        ))}
      </div>
      <SourceLine via={book.via} source={book.source} />
    </div>
  );
}

function BookNotice({
  tone,
  text,
  via,
}: {
  tone: "rose" | "muted";
  text: string;
  via?: string | null;
}) {
  return (
    <div className="px-6 py-12">
      <p
        className={cn(
          "text-sm",
          tone === "rose" ? "text-rose" : "text-slate-muted",
        )}
      >
        {text}
      </p>
      <SourceLine via={via} source={null} />
    </div>
  );
}

/** Provenance belongs under the data, not above it. */
function SourceLine({
  via,
  source,
}: {
  via?: string | null;
  source?: string | null;
}) {
  if (!via) return null;
  return (
    <p className="mt-4 text-[11px] text-slate-muted">
      Via {via}
      {source ? ` · ${source}` : ""}
    </p>
  );
}

/** Dashboard is a summary of the same books — no second source of truth. */
function DeskDashboard({ books }: { books: DomainDeskBook[] }) {
  const funds = books.find((book) => book.tab === "funds");
  const counted = books.filter((book) =>
    ["orders", "holdings", "positions"].includes(book.tab),
  );
  const hasAnything =
    Boolean(funds?.rows.length) || counted.some((book) => book.rows.length);

  return (
    <div className="px-6 py-5">
      {funds?.rows.length ? (
        <>
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-muted">
            Funds
          </h3>
          <div className="mt-2 grid gap-3 sm:grid-cols-3">
            {funds.rows.slice(0, 3).map((row, index) => (
              <div
                key={`${cellValue(row.label)}-${index}`}
                className="surface-panel rounded-xl px-4 py-3.5"
              >
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-muted">
                  {humanize(cellValue(row.label))}
                </p>
                <p className="mt-1.5 font-display text-2xl font-semibold tnum tabular-nums tracking-tight">
                  {formatMoney(row.value)}
                </p>
              </div>
            ))}
          </div>
        </>
      ) : null}

      <h3
        className={cn(
          "text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-muted",
          funds?.rows.length && "mt-6",
        )}
      >
        Books
      </h3>
      <div className="mt-2 grid gap-3 sm:grid-cols-3">
        {counted.map((book) => (
          <div key={book.tab} className="surface-panel rounded-xl px-4 py-3.5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-muted">
              {book.label}
            </p>
            <p className="mt-1.5 font-display text-2xl font-semibold tnum tabular-nums tracking-tight">
              {book.rows.length}
            </p>
            {/* A book the broker could not read is not an empty book. */}
            {book.error ? (
              <p className="mt-1 text-[11px] text-rose">{book.error}</p>
            ) : null}
          </div>
        ))}
      </div>

      {!hasAnything ? (
        <p className="mt-6 text-sm text-slate-muted">
          Nothing to show yet. Bind a broker toolkit on the Live trading team,
          then Refresh to pull your orders, holdings, positions and funds.
        </p>
      ) : null}
    </div>
  );
}

function BookTable({ book }: { book: DomainDeskBook }) {
  const columns = book.columns.length ? book.columns : ["symbol"];
  const template = `repeat(${columns.length}, minmax(5rem, 1fr))`;

  if (book.error) {
    return <BookNotice tone="rose" text={book.error} via={book.via} />;
  }
  if (!book.rows.length) {
    return <BookNotice tone="muted" text={book.empty_hint} via={book.via} />;
  }

  return (
    <div className="px-6 py-5">
      <div className="overflow-x-auto rounded-xl border border-line">
        <div
          className="grid gap-3 border-b border-line bg-canvas/60 px-3 py-2"
          style={{ gridTemplateColumns: template }}
        >
          {columns.map((column, index) => (
            <span
              key={column}
              className={cn(
                "text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-muted",
                // Numbers read down their last digit, so their headers align right.
                index > 0 && "text-right",
              )}
            >
              {humanize(column)}
            </span>
          ))}
        </div>
        {book.rows.map((row, index) => (
          <div
            key={String(row.order_id ?? row.trade_id ?? row.symbol ?? index) + index}
            className="grid gap-3 border-b border-line/60 px-3 py-2.5 transition last:border-0 hover:bg-raised/50"
            style={{ gridTemplateColumns: template }}
          >
            {columns.map((column, colIndex) => {
              const value = row[column];
              const numeric = isNumericLike(value);
              return (
                <p
                  key={column}
                  title={cellValue(value)}
                  className={cn(
                    "truncate text-sm",
                    colIndex === 0 ? "font-medium" : "text-right",
                    numeric && "tnum tabular-nums",
                    PNL_COLUMNS.has(column) && pnlTone(value),
                  )}
                >
                  {cellValue(value)}
                </p>
              );
            })}
          </div>
        ))}
      </div>
      <SourceLine via={book.via} source={book.source} />
    </div>
  );
}

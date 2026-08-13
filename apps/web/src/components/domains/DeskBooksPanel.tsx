"use client";

import { useMemo, useState } from "react";

import { Link } from "@/i18n/navigation";

import { cn } from "@/lib/utils";
import type {
  DomainBrokerTool,
  DomainDashboard,
  DomainDeskBook,
} from "@/lib/api/admin";

const CORE_TABS = ["orders", "positions", "holdings", "watchlist"] as const;

const PLACEHOLDER_BOOKS: DomainDeskBook[] = [
  {
    id: "orders",
    label: "Orders",
    tab: "orders",
    via: null,
    team_slug: null,
    source: null,
    columns: ["symbol", "side", "qty", "status", "price"],
    rows: [],
    error: null,
    empty_hint:
      "Tap Refresh to load orders, positions, and watchlist from Live trading.",
  },
  {
    id: "positions",
    label: "Positions",
    tab: "positions",
    via: null,
    team_slug: null,
    source: null,
    columns: ["symbol", "qty", "avg", "ltp", "pnl"],
    rows: [],
    error: null,
    empty_hint:
      "Tap Refresh to load orders, positions, and watchlist from Live trading.",
  },
  {
    id: "holdings",
    label: "Holdings",
    tab: "holdings",
    via: null,
    team_slug: null,
    source: null,
    columns: ["symbol", "qty", "avg", "ltp", "pnl"],
    rows: [],
    error: null,
    empty_hint:
      "Tap Refresh to load orders, positions, and watchlist from Live trading.",
  },
  {
    id: "watchlist",
    label: "Watchlist",
    tab: "watchlist",
    via: null,
    team_slug: null,
    source: null,
    columns: ["symbol", "ltp", "change"],
    rows: [
      { symbol: "NSE:NIFTY", ltp: null, change: null },
      { symbol: "NSE:BANKNIFTY", ltp: null, change: null },
      { symbol: "NSE:RELIANCE", ltp: null, change: null },
    ],
    error: null,
    empty_hint:
      "Tap Refresh to load orders, positions, and watchlist from Live trading.",
  },
];

function columnLabel(column: string) {
  return column.replaceAll("_", " ");
}

function cellValue(value: string | number | boolean | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function isNumericLike(value: string | number | boolean | null | undefined) {
  if (typeof value === "number") return true;
  if (typeof value !== "string") return false;
  return /^-?[\d,.]+%?$/.test(value.trim());
}

export function DeskBooksPanel({
  snapshot,
  customer,
  brokerTools = [],
}: {
  snapshot: DomainDashboard["desk_snapshot"];
  customer: boolean;
  brokerTools?: DomainBrokerTool[];
}) {
  const loaded = snapshot != null;
  const books = snapshot?.books?.length ? snapshot.books : PLACEHOLDER_BOOKS;
  const byTab = useMemo(
    () => Object.fromEntries(books.map((book) => [book.tab, book])),
    [books],
  );
  const funds = byTab.funds;
  const extraTabs = books.filter(
    (book) =>
      !(CORE_TABS as readonly string[]).includes(book.tab) && book.tab !== "funds",
  );
  const tabs = [
    ...CORE_TABS.map((tab) => byTab[tab]).filter(Boolean),
    ...extraTabs,
  ];
  const [active, setActive] = useState(tabs[0]?.tab ?? "orders");
  const current =
    tabs.find((book) => book.tab === active) ?? tabs[0] ?? PLACEHOLDER_BOOKS[0];
  const snapshotError = snapshot?.error;

  return (
    <section className="mt-5">
      <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-muted">
        Desk books
      </p>

      {funds ? <FundsStrip book={funds} loaded={loaded} /> : null}

      {snapshotError ? (
        <p className="mb-2 text-xs text-rose">{snapshotError}</p>
      ) : null}

      <div className="table-shell rounded-xl">
        <div className="flex flex-wrap gap-1 border-b border-line px-2 py-1.5">
          {tabs.map((book) => {
            const selected = book.tab === current.tab;
            return (
              <button
                key={book.id}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => setActive(book.tab)}
                className={cn(
                  "rounded-md px-2.5 py-1.5 text-xs font-medium transition",
                  selected
                    ? "bg-teal/15 text-teal"
                    : "text-ink-soft hover:bg-fog/70 hover:text-ink",
                )}
              >
                {book.label}
                {book.rows.length ? (
                  <span className="ml-1.5 tnum text-[10px] text-slate-muted">
                    {book.rows.length}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>

        {!loaded ? (
          <p className="px-4 py-8 text-center text-sm text-slate-muted">
            Tap Refresh to load orders, positions, and watchlist from Live
            trading.
          </p>
        ) : (
          <BookTable book={current} />
        )}
      </div>

      {brokerTools.length ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {brokerTools.map((tool) =>
            customer ? (
              <span
                key={tool.id}
                className="rounded-full border border-line px-3 py-1.5 text-xs font-medium text-ink-soft"
              >
                {tool.name}
                {tool.via_team_name ? ` · ${tool.via_team_name}` : ""}
              </span>
            ) : (
              <Link
                key={tool.id}
                href="/admin/teams"
                className="rounded-full border border-line px-3 py-1.5 text-xs font-medium text-ink-soft transition hover:border-teal/40 hover:text-teal"
              >
                {tool.name}
                {tool.via_team_name ? ` · ${tool.via_team_name}` : ""}
                {!tool.published ? " · draft" : ""}
              </Link>
            ),
          )}
        </div>
      ) : (
        <p className="mt-2 text-xs text-slate-muted">
          No broker toolkit on Live trading yet. Assign Groww/Kite on that team,
          then Refresh.
        </p>
      )}
    </section>
  );
}

function FundsStrip({
  book,
  loaded,
}: {
  book: DomainDeskBook;
  loaded: boolean;
}) {
  if (!loaded || !book.rows.length) return null;
  const rows = book.rows.slice(0, 4);
  return (
    <div className="mb-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
      {rows.map((row, index) => (
        <div key={`${cellValue(row.label)}-${index}`} className="surface-panel rounded-xl px-3 py-2.5">
          <p className="th-label">{cellValue(row.label)}</p>
          <p className="mt-1 text-sm font-semibold tnum">
            {cellValue(row.value)}
          </p>
        </div>
      ))}
    </div>
  );
}

function BookTable({ book }: { book: DomainDeskBook }) {
  const columns = book.columns.length ? book.columns : ["symbol"];
  const hasRows = book.rows.length > 0;
  const blankWatchlist =
    book.tab === "watchlist" &&
    hasRows &&
    book.rows.every((row) => row.ltp == null || row.ltp === "");

  return (
    <div className="overflow-x-auto">
      {book.via ? (
        <p className="border-b border-line/60 px-4 py-2 text-[11px] text-slate-muted">
          Via {book.via}
          {book.source ? ` · ${book.source}` : ""}
        </p>
      ) : null}
      {book.error ? (
        <p className="border-b border-rose/30 bg-rose/10 px-4 py-2 text-xs text-rose">
          {book.error}
        </p>
      ) : null}
      {hasRows ? (
        <>
          <div
            className="grid gap-3 border-b border-line px-4 py-2.5"
            style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(4.5rem, 1fr))` }}
          >
            {columns.map((column) => (
              <span key={column} className="th-label">
                {columnLabel(column)}
              </span>
            ))}
          </div>
          {book.rows.map((row, index) => (
            <div
              key={String(row.order_id ?? row.trade_id ?? row.symbol ?? index) + index}
              className="grid gap-3 border-b border-line/60 px-4 py-2.5 last:border-0"
              style={{
                gridTemplateColumns: `repeat(${columns.length}, minmax(4.5rem, 1fr))`,
              }}
            >
              {columns.map((column) => {
                const value = row[column];
                return (
                  <p
                    key={column}
                    className={cn(
                      "truncate text-sm",
                      isNumericLike(value) ? "mono-cell tnum" : "",
                    )}
                  >
                    {cellValue(value)}
                  </p>
                );
              })}
            </div>
          ))}
          {blankWatchlist ? (
            <p className="px-4 py-3 text-xs text-slate-muted">{book.empty_hint}</p>
          ) : null}
        </>
      ) : (
        <p className="px-4 py-10 text-center text-sm text-slate-muted">
          {book.empty_hint}
        </p>
      )}
    </div>
  );
}

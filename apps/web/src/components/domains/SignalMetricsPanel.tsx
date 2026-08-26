"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { SignalSetupBar } from "@/components/domains/SignalSetupBar";
import { DeskBooksPanel } from "@/components/domains/DeskBooksPanel";
import { OptionsLabScreenerPanel } from "@/components/domains/OptionsLabScreenerPanel";
import {
  deskInstrumentKey,
  readDeskInstrument,
  subscribeDeskInstrument,
  type DeskInstrumentSelection,
} from "@/components/domains/desk-instrument";
import { useSignalConfigAutosave } from "@/components/domains/useSignalConfigAutosave";
import { AdminFormDialog } from "@/components/ui/AdminFormDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { FieldHint, Input, Label, Textarea } from "@/components/ui/Field";
import {
  PauseIcon,
  PlayIcon,
  RefreshIcon,
  BellIcon,
  CheckIcon,
  CloseIcon,
  ChevronDownIcon,
  ArrowUpIcon,
  BookIcon,
  SearchIcon,
} from "@/components/ui/icons";
import {
  getOptionsScreener,
  getSignalState,
  publishSignalEntry,
  resetOptionsLabScreenerBaseline,
  type DomainBrokerTool,
  type DomainDashboard,
  type OptionsScreenerRow,
  type OptionsScreenerSnapshot,
  type SignalEngineAdminConfig,
  type SignalEngineState,
  type SignalEntry,
  type SignalEntryStatus,
  type SignalMetricRow,
} from "@/lib/api/admin";
import { streamSignalState } from "@/lib/api/signals-stream";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";

const SCREENER_POLL_MS = 60_000;

/** Trade Desk Checklist category order (matches backend). */
const CHECKLIST_CATEGORY_ORDER = [
  "Data & Charts Watch",
  "Timing & No-Trade Rules",
  "Levels & Technicals",
  "Global Markets Watch",
  "Stock Big-Move Watch",
  "Trade Discipline Check",
] as const;

function groupMetricsByCategory(
  metrics: SignalMetricRow[],
): { category: string; rows: SignalMetricRow[]; passing: string }[] {
  const buckets = new Map<string, SignalMetricRow[]>();
  for (const row of metrics) {
    const cat = row.category?.trim() || "Other";
    const list = buckets.get(cat) ?? [];
    list.push(row);
    buckets.set(cat, list);
  }

  const ordered: string[] = [
    ...CHECKLIST_CATEGORY_ORDER.filter((cat) => buckets.has(cat)),
    ...[...buckets.keys()].filter(
      (cat) => !(CHECKLIST_CATEGORY_ORDER as readonly string[]).includes(cat),
    ),
  ];

  return ordered.map((category) => {
    const rows = [...(buckets.get(category) ?? [])].sort(
      (a, b) => (a.check_no ?? 999) - (b.check_no ?? 999),
    );
    const gated = rows.filter((r) => r.gates_entry);
    const passing =
      gated.length === 0
        ? `${rows.length} checks`
        : `${gated.filter((r) => r.passed === true).length}/${gated.length} gated pass`;
    return { category, rows, passing };
  });
}

/** Split rows evenly across three columns (same density as the legacy board). */
function splitMetricColumns(
  rows: SignalMetricRow[],
  columnCount = 3,
): SignalMetricRow[][] {
  const columns: SignalMetricRow[][] = Array.from({ length: columnCount }, () => []);
  for (const row of rows) {
    let shortest = 0;
    for (let i = 1; i < columnCount; i += 1) {
      if (columns[i].length < columns[shortest].length) shortest = i;
    }
    columns[shortest].push(row);
  }
  return columns.filter((col) => col.length > 0);
}

const CONFIG_OVERRIDE_METRICS: Record<
  string,
  keyof SignalEngineAdminConfig
> = {
  pcr: "pcr",
  max_pain: "max_pain",
  max_pain_check: "max_pain",
  ivp: "ivp",
  india_vix: "india_vix",
  india_vix_level: "india_vix",
  dow_jones: "dow_change_pct",
  oi_pct_chg: "oi_pct_chg",
  iv_chg: "iv_chg",
  fii_net: "fii_net",
};

function formatValue(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(2);
}

type ValueTick = "up" | "down" | null;

const TICK_STICKY_MS = 3_000;

function ValueTickMark({ tick }: { tick: "up" | "down" }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 10 10"
      className={cn(
        "size-2.5 shrink-0",
        tick === "up" ? "text-teal" : "text-rose",
      )}
    >
      {tick === "up" ? (
        <path d="M5 1.5 L8.5 7 H1.5 Z" fill="currentColor" />
      ) : (
        <path d="M5 8.5 L1.5 3 H8.5 Z" fill="currentColor" />
      )}
    </svg>
  );
}

function LiveMetricValue({
  value,
  tick,
  passed,
  gatesEntry,
}: {
  value: number | null | undefined;
  tick: ValueTick;
  passed: boolean | null | undefined;
  gatesEntry?: boolean;
}) {
  const formatted = formatValue(value);
  if (formatted === "—") {
    return <span className="tabular-nums text-slate-muted">—</span>;
  }

  const movementTick = tick === "up" || tick === "down" ? tick : null;
  const showFailMark = movementTick === null && gatesEntry && passed === false;
  const showPassMark = movementTick === null && gatesEntry && passed === true;

  return (
    <span
      className={cn(
        "inline-flex items-center justify-end gap-0.5 tabular-nums font-semibold transition-colors duration-300",
        movementTick === "up" && "text-teal",
        movementTick === "down" && "text-rose",
        showPassMark && "text-teal",
        showFailMark && "text-rose",
        !movementTick && !showPassMark && !showFailMark && "text-ink",
      )}
    >
      {movementTick ? <ValueTickMark tick={movementTick} /> : null}
      {showFailMark ? <ValueTickMark tick="down" /> : null}
      {showPassMark ? (
        <svg
          aria-hidden
          viewBox="0 0 10 10"
          className="size-2.5 shrink-0 text-teal"
        >
          <path d="M5 1.5 L8.5 7 H1.5 Z" fill="currentColor" />
        </svg>
      ) : null}
      <span>{formatted}</span>
    </span>
  );
}

/** Track up/down ticks; sticky 3s after each change so arrows stay visible. */
function useStickyValueTicks(metrics: SignalMetricRow[]): Map<string, ValueTick> {
  const prevValuesRef = useRef<Map<string, number>>(new Map());
  const stickyRef = useRef<Map<string, { tick: "up" | "down"; until: number }>>(
    new Map(),
  );
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    const now = Date.now();
    let moved = false;

    for (const row of metrics) {
      const cur = row.value;
      if (cur == null || typeof cur !== "number" || Number.isNaN(cur)) {
        continue;
      }
      const prev = prevValuesRef.current.get(row.id);
      if (prev !== undefined && cur !== prev) {
        stickyRef.current.set(row.id, {
          tick: cur > prev ? "up" : "down",
          until: now + TICK_STICKY_MS,
        });
        moved = true;
      }
      prevValuesRef.current.set(row.id, cur);
    }

    for (const [id, entry] of stickyRef.current) {
      if (entry.until <= now) {
        stickyRef.current.delete(id);
        moved = true;
      }
    }

    if (moved) {
      setGeneration((g) => g + 1);
    }

    const timer = window.setInterval(() => {
      const ts = Date.now();
      let expired = false;
      for (const [id, entry] of stickyRef.current) {
        if (entry.until <= ts) {
          stickyRef.current.delete(id);
          expired = true;
        }
      }
      if (expired) {
        setGeneration((g) => g + 1);
      }
    }, 400);

    return () => window.clearInterval(timer);
  }, [metrics]);

  return useMemo(() => {
    const now = Date.now();
    const out = new Map<string, ValueTick>();
    for (const row of metrics) {
      const sticky = stickyRef.current.get(row.id);
      if (sticky && sticky.until > now) {
        out.set(row.id, sticky.tick);
      } else {
        out.set(row.id, null);
      }
    }
    void generation;
    return out;
  }, [metrics, generation]);
}

function formatEvaluatedAt(epoch: number | undefined): string | null {
  if (epoch == null || !Number.isFinite(epoch)) return null;
  const ms = epoch > 1e12 ? epoch : epoch * 1000;
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString();
}

function statusTone(passed: boolean | null | undefined) {
  if (passed === true) return "success" as const;
  if (passed === false) return "danger" as const;
  return "neutral" as const;
}

type BuySignalTone = SignalEntryStatus | "loading";

function buySignalTone(
  state: SignalEngineState | null,
  entry: SignalEntry | null | undefined,
  engineEnabled = true,
): BuySignalTone {
  if (!state) return "loading";
  if (!engineEnabled) return "waiting";
  return entry?.status ?? (state.entry_ready ? "ready" : "waiting");
}

function buySignalLine(entry: SignalEntry | null | undefined): string {
  if (entry?.label) return entry.label;
  return "BUY= —, CE=—, PE=—, EXIT —";
}

function buySignalBadge(tone: BuySignalTone): string {
  switch (tone) {
    case "ready":
      return "GO";
    case "blocked":
      return "NO GO";
    case "waiting":
      return "PENDING";
    default:
      return "…";
  }
}

function buySignalClassName(tone: BuySignalTone): string {
  switch (tone) {
    case "ready":
      return "border-teal/50 bg-teal/10";
    case "blocked":
      return "border-rose/50 bg-rose/10";
    case "waiting":
      return "border-amber-300 bg-amber-50";
    default:
      return "border-line bg-fog/40";
  }
}

function buySignalTextClassName(tone: BuySignalTone): string {
  switch (tone) {
    case "ready":
      return "text-teal";
    case "blocked":
      return "text-rose-800";
    case "waiting":
      return "text-amber-950";
    default:
      return "text-slate-muted";
  }
}

function defaultNotifyTitle(tone: BuySignalTone): string {
  switch (tone) {
    case "ready":
      return "New trading signal — GO";
    case "blocked":
      return "Signal update — NO GO";
    case "waiting":
      return "Signal snapshot — PENDING";
    default:
      return "New trading signal";
  }
}

function defaultNotifyBody(
  entry: SignalEntry | null | undefined,
  state: SignalEngineState | null,
): string {
  const label = entry?.label ?? "BUY= —, CE=—, PE=—, EXIT —";
  const note =
    entry?.status_note ??
    (state ? `${state.passed}/${state.evaluable} rules passing` : "");
  return note ? `${label}\n\n${note}` : label;
}

function notifyBellButtonClass(tone: BuySignalTone): string {
  // Full class sets — do not stack on variant="secondary" (cn has no twMerge;
  // bg-raised + text-white would make the icon invisible).
  const base =
    "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border transition-[color,background-color,border-color,opacity] duration-150 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:ring-offset-canvas";
  switch (tone) {
    case "ready":
      return cn(
        base,
        "border-teal bg-teal text-white hover:bg-teal-bright focus-visible:ring-teal-bright/45",
      );
    case "blocked":
      return cn(
        base,
        "border-rose bg-rose text-white hover:bg-rose/90 focus-visible:ring-rose/45",
      );
    case "waiting":
      return cn(
        base,
        "border-amber-500 bg-amber-500 text-white hover:bg-amber-600 focus-visible:ring-amber-400/45",
      );
    default:
      return cn(
        base,
        "border-line bg-fog text-slate-muted hover:bg-mist focus-visible:ring-line/50",
      );
  }
}

function NotifySignalDialog({
  open,
  tone,
  title,
  body,
  publishing,
  onTitleChange,
  onBodyChange,
  onClose,
  onSend,
}: {
  open: boolean;
  tone: BuySignalTone;
  title: string;
  body: string;
  publishing: boolean;
  onTitleChange: (value: string) => void;
  onBodyChange: (value: string) => void;
  onClose: () => void;
  onSend: () => void;
}) {
  if (!open) return null;

  const statusHint =
    tone === "ready"
      ? "Entry rules pass — desk can act on this signal."
      : tone === "blocked"
        ? "Some gated rules are failing — edit before notifying if needed."
        : "Snapshot only — edit the message before sending.";

  return (
    <AdminFormDialog
      title="Notify all users"
      subtitle={statusHint}
      titleId="notify-signal-title"
      onClose={onClose}
      showCloseButton
      className="max-w-lg"
    >
      <div className="space-y-4">
        <div>
          <Label htmlFor="notify-signal-subject">Notification title</Label>
          <Input
            id="notify-signal-subject"
            value={title}
            onChange={(event) => onTitleChange(event.target.value)}
            maxLength={200}
            disabled={publishing}
          />
        </div>
        <div>
          <Label htmlFor="notify-signal-body" hint="Shown in the in-app bell feed">
            Message
          </Label>
          <Textarea
            id="notify-signal-body"
            value={body}
            onChange={(event) => onBodyChange(event.target.value)}
            maxLength={2000}
            rows={5}
            disabled={publishing}
          />
          <FieldHint>Adjust CE/PE, exit, or add desk notes before sending.</FieldHint>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            variant="secondary"
            size="icon"
            icon={<CloseIcon />}
            aria-label="Cancel"
            title="Cancel"
            onClick={onClose}
            disabled={publishing}
          />
          {tone === "waiting" ? (
            <button
              type="button"
              disabled={publishing || !title.trim() || !body.trim()}
              onClick={onSend}
              className="inline-flex items-center justify-center gap-1.5 rounded-md border border-amber-500 bg-amber-500 px-3.5 py-2 text-sm font-medium text-white transition-[color,background-color,opacity] hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/45 focus-visible:ring-offset-1 focus-visible:ring-offset-canvas"
            >
              <BellIcon />
              {publishing ? "Sending…" : "Send notification"}
            </button>
          ) : (
            <Button
              variant={tone === "blocked" ? "danger" : "accent"}
              icon={<BellIcon />}
              disabled={publishing || !title.trim() || !body.trim()}
              onClick={onSend}
            >
              {publishing ? "Sending…" : "Send notification"}
            </Button>
          )}
        </div>
      </div>
    </AdminFormDialog>
  );
}

function BuySignalBanner({
  state,
  entry,
  engineEnabled,
  entryReady,
  publishing,
  publishMsg,
  onOpenNotify,
}: {
  state: SignalEngineState | null;
  entry: SignalEntry | null | undefined;
  engineEnabled: boolean;
  entryReady: boolean;
  publishing: boolean;
  publishMsg: string | null;
  onOpenNotify: () => void;
}) {
  const tone = buySignalTone(state, entry, engineEnabled);
  const buyLine = engineEnabled
    ? buySignalLine(entry)
    : "Engine stopped — signals paused";
  const note = !engineEnabled
    ? "Start the engine to resume live buy signals"
    : (entry?.status_note ??
      (state
        ? `${state.passed}/${state.evaluable} rules passing`
        : "Loading buy signal…"));
  const statusLine = publishMsg ?? note;
  const canNotify = engineEnabled && !publishing && Boolean(state?.entry);

  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-1.5",
        buySignalClassName(tone),
        buySignalTextClassName(tone),
      )}
      role="status"
      aria-live="polite"
      title={statusLine}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide opacity-80">
          Buy signal
        </span>
        <Badge
          tone={
            !engineEnabled
              ? "warning"
              : tone === "ready"
                ? "success"
                : tone === "blocked"
                  ? "danger"
                  : "warning"
          }
          live={engineEnabled && tone === "ready"}
        >
          {!engineEnabled ? "STOPPED" : buySignalBadge(tone)}
        </Badge>
        <p
          className={cn(
            "shrink-0 font-display text-sm font-semibold leading-none tnum tabular-nums",
            tone === "ready" && "text-teal",
          )}
        >
          {buyLine}
        </p>
        <span className="min-w-0 flex-1 truncate text-[11px] leading-none opacity-80">
          {statusLine}
        </span>
        <button
          type="button"
          disabled={!canNotify}
          aria-label={
            publishing
              ? "Sending notification"
              : "Notify all users"
          }
          title={
            publishing
              ? "Sending notification…"
              : entryReady
                ? "Notify all users — entry rules pass"
                : tone === "blocked"
                  ? "Notify all users — rules failing (edit before send)"
                  : "Notify all users with current signal snapshot"
          }
          onClick={onOpenNotify}
          className={notifyBellButtonClass(tone)}
        >
          <BellIcon />
        </button>
      </div>
    </div>
  );
}

function EditableOverrideValue({
  row,
  configKey,
  config,
  patchConfig,
}: {
  row: SignalMetricRow;
  configKey: keyof SignalEngineAdminConfig;
  config: SignalEngineAdminConfig;
  patchConfig: (patch: Partial<SignalEngineAdminConfig>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const override = config[configKey] as number | null | undefined;

  if (editing) {
    return (
      <Input
        autoFocus
        type="number"
        step={row.id === "pcr" ? "0.01" : "1"}
        className="h-8 py-1 text-sm tnum"
        value={override ?? ""}
        onBlur={() => setEditing(false)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === "Escape") setEditing(false);
        }}
        onChange={(e) =>
          patchConfig({
            [configKey]: e.target.value === "" ? null : Number(e.target.value),
          })
        }
      />
    );
  }

  return (
    <button
      type="button"
      className={cn(
        "w-full truncate text-right text-sm tnum tabular-nums hover:underline",
        override != null && "font-semibold text-info",
      )}
      title={row.hint || "Click to override"}
      onClick={() => setEditing(true)}
    >
      {formatValue(row.value)}
    </button>
  );
}

function MetricCategoryWidget({
  category,
  rows,
  passing,
  open,
  onToggle,
  config,
  patchConfig,
  valueTicks,
}: {
  category: string;
  rows: SignalMetricRow[];
  passing: string;
  open: boolean;
  onToggle: () => void;
  config: SignalEngineAdminConfig | null;
  patchConfig: (patch: Partial<SignalEngineAdminConfig>) => void;
  valueTicks: Map<string, ValueTick>;
}) {
  const failCount = rows.filter((r) => r.gates_entry && r.passed === false).length;

  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-line bg-white/60">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left transition-colors hover:bg-fog/50"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={`metric-category-${category.replace(/\s+/g, "-")}`}
      >
        <span className="flex min-w-0 items-center gap-2">
          <ChevronDownIcon
            className={cn(
              "size-4 shrink-0 text-slate-muted transition-transform duration-200",
              !open && "-rotate-90",
            )}
          />
          <span className="truncate text-sm font-semibold text-ink">{category}</span>
          {failCount > 0 ? (
            <Badge tone="danger" className="px-1.5 py-0 text-[9px]">
              {failCount} fail
            </Badge>
          ) : null}
        </span>
        <span className="shrink-0 text-xs text-slate-muted">{passing}</span>
      </button>
      {open ? (
        <div
          id={`metric-category-${category.replace(/\s+/g, "-")}`}
          className="border-t border-line/60 px-2 pb-2 pt-1"
        >
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {splitMetricColumns(rows).map((column, index) => (
              <div
                key={`${category}-col-${index}`}
                className="min-w-0 overflow-hidden rounded-lg border border-line/70 bg-white/80"
              >
                <MetricTable
                  rows={column}
                  config={config}
                  patchConfig={patchConfig}
                  valueTicks={valueTicks}
                />
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function MetricTable({
  rows,
  config,
  patchConfig,
  valueTicks,
}: {
  rows: SignalMetricRow[];
  config: SignalEngineAdminConfig | null;
  patchConfig: (patch: Partial<SignalEngineAdminConfig>) => void;
  valueTicks: Map<string, ValueTick>;
}) {
  if (rows.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[15.5rem] border-collapse text-sm">
        <thead className="sticky top-0 z-[1] bg-fog/95 backdrop-blur-sm">
          <tr className="border-b border-line text-xs uppercase tracking-wide text-slate-muted">
            <th className="px-2 py-2 text-left font-medium">Metric</th>
            <th className="px-2 py-2 text-right font-medium">Value</th>
            <th className="px-2 py-2 text-left font-medium">Target</th>
            <th className="w-[4.75rem] min-w-[4.75rem] px-1 py-2 text-center font-medium">
              Status
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const overrideKey = CONFIG_OVERRIDE_METRICS[row.id];
            return (
              <tr
                key={row.id}
                className={cn(
                  "border-b border-line/50",
                  row.passed === true && "bg-teal/[0.04]",
                  row.passed === false && "bg-rose/[0.04]",
                )}
                title={row.hint}
              >
                <td className="max-w-[5.5rem] truncate px-2 py-1.5 font-medium">
                  {row.check_no ? (
                    <span className="mr-1 text-[10px] text-slate-muted tnum">
                      {row.check_no}.
                    </span>
                  ) : null}
                  {row.label}
                </td>
                <td className="whitespace-nowrap px-2 py-1.5 text-right tnum">
                  {overrideKey && config ? (
                    <EditableOverrideValue
                      row={row}
                      configKey={overrideKey}
                      config={config}
                      patchConfig={patchConfig}
                    />
                  ) : (
                    <LiveMetricValue
                      value={row.value}
                      tick={valueTicks.get(row.id) ?? null}
                      passed={row.passed}
                      gatesEntry={row.gates_entry}
                    />
                  )}
                </td>
                <td className="max-w-[6.5rem] truncate px-2 py-1.5 text-slate-muted">
                  {row.target}
                </td>
                <td className="w-[4.75rem] min-w-[4.75rem] px-1 py-1.5 text-center">
                  <Badge
                    tone={statusTone(row.passed)}
                    dot={row.passed == null}
                    className="inline-flex min-w-[4rem] justify-center gap-1 whitespace-nowrap px-1 py-0.5 text-[9px]"
                  >
                    {row.passed === true ? (
                      <CheckIcon className="size-2.5 shrink-0" />
                    ) : row.passed === false ? (
                      <CloseIcon className="size-2.5 shrink-0" />
                    ) : null}
                    {row.passed === true
                      ? "Pass"
                      : row.passed === false
                        ? "Fail"
                        : "N/A"}
                  </Badge>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function SignalMetricsPanel({
  deskSnapshot,
  brokerTools = [],
  refreshing,
  onRefreshBooks,
  fetchedAt,
  rangeDays,
}: {
  deskSnapshot?: DomainDashboard["desk_snapshot"];
  brokerTools?: DomainBrokerTool[];
  refreshing?: boolean;
  onRefreshBooks?: () => void;
  fetchedAt?: string;
  rangeDays?: number;
} = {}) {
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const [state, setState] = useState<SignalEngineState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [publishMsg, setPublishMsg] = useState<string | null>(null);
  const [notifyOpen, setNotifyOpen] = useState(false);
  const [notifyTitle, setNotifyTitle] = useState("");
  const [notifyBody, setNotifyBody] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [warningsOpen, setWarningsOpen] = useState(true);
  const [engineBusy, setEngineBusy] = useState(false);
  const [booksOpen, setBooksOpen] = useState(false);
  const [screenerOpen, setScreenerOpen] = useState(false);
  const [screener, setScreener] = useState<OptionsScreenerSnapshot | null>(null);
  const [screenerUniverse, setScreenerUniverse] = useState<
    "indices" | "equities" | "all"
  >("indices");
  const [screenerLoading, setScreenerLoading] = useState(false);
  const [screenerError, setScreenerError] = useState<string | null>(null);
  const [resettingScreener, setResettingScreener] = useState(false);
  const [screenerAtmHint, setScreenerAtmHint] = useState<number | null>(null);
  const mounted = useRef(true);
  const screenerSeq = useRef(0);
  const screenerAbortRef = useRef<AbortController | null>(null);
  const lastDeskInstrumentKey = useRef("");

  const {
    config,
    presets,
    presetKey,
    presetLocked,
    loading: configLoading,
    saveStatus,
    error: configError,
    patchConfig,
    patchConfigImmediate,
    onPresetChange,
    applyInstrumentSelection,
  } = useSignalConfigAutosave(getAccessToken, isLoaded && isSignedIn);

  const configRef = useRef(config);
  configRef.current = config;

  const engineEnabled =
    config?.engine_enabled ?? state?.engine_enabled ?? false;
  const snapshotStale = state?.snapshot_stale;
  const engineActive = engineEnabled && Boolean(state?.engine_active);
  // Prefer server snapshot_stale; unknown age is not "Running" / not live.
  const engineStale = engineActive && snapshotStale === true;
  const engineRunning = engineActive && snapshotStale === false;

  const metrics = useMemo(() => state?.metrics ?? [], [state?.metrics]);
  const failingRules = useMemo(
    () =>
      metrics
        .filter((row) => row.gates_entry && row.passed === false)
        .map((row) =>
          row.check_no ? `#${row.check_no} ${row.label}` : row.label,
        ),
    [metrics],
  );
  const metricsAtmHint = useMemo(() => {
    const atm = metrics.find((row) => row.id === "atm")?.value;
    return atm != null ? Math.round(atm) : null;
  }, [metrics]);
  // H4: SSE can lag behind a screener pick — don't blend old ATM into new FUT CE/PE.
  const metricsAtmAligned =
    metricsAtmHint != null &&
    Boolean(config?.underlying_symbol) &&
    state?.underlying?.symbol === config?.underlying_symbol
      ? metricsAtmHint
      : null;
  const atmHint = metricsAtmAligned ?? screenerAtmHint;
  const metricGroups = useMemo(
    () => groupMetricsByCategory(metrics),
    [metrics],
  );
  const [collapsedCategories, setCollapsedCategories] = useState<Set<string>>(
    () => new Set(),
  );

  const toggleCategory = useCallback((category: string) => {
    setCollapsedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
  }, []);

  const expandAllCategories = useCallback(() => {
    setCollapsedCategories(new Set());
  }, []);

  const collapseAllCategories = useCallback(() => {
    setCollapsedCategories(new Set(metricGroups.map((g) => g.category)));
  }, [metricGroups]);

  const valueTicks = useStickyValueTicks(metrics);

  const refreshOnce = useCallback(async () => {
    try {
      const token = await getAccessToken();
      if (!token || !mounted.current) return;
      const data = await getSignalState(token);
      if (mounted.current) {
        setState(data);
        setError(null);
      }
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error ? err.message : "Failed to load signal state");
      }
    }
  }, [getAccessToken]);

  useEffect(() => {
    if (!booksOpen && !screenerOpen) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setBooksOpen(false);
        setScreenerOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [booksOpen, screenerOpen]);

  const refreshScreener = useCallback(async () => {
    const seq = ++screenerSeq.current;
    screenerAbortRef.current?.abort();
    const controller = new AbortController();
    screenerAbortRef.current = controller;
    setScreenerLoading(true);
    try {
      const token = await getAccessToken();
      if (!token || !mounted.current) return;
      const fast = await getOptionsScreener(
        token,
        screenerUniverse,
        "fast",
        controller.signal,
      );
      if (!mounted.current || seq !== screenerSeq.current) return;
      setScreener(fast);
      setScreenerError(fast.ok ? null : fast.error ?? "Screener unavailable");
      setScreenerLoading(false);

      if (fast.ok && fast.partial === true) {
        const full = await getOptionsScreener(
          token,
          screenerUniverse,
          "full",
          controller.signal,
        );
        if (!mounted.current || seq !== screenerSeq.current) return;
        setScreener(full);
        setScreenerError(full.ok ? null : full.error ?? "Screener unavailable");
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      if (mounted.current && seq === screenerSeq.current) {
        setScreenerError(
          err instanceof Error ? err.message : "Failed to load screener",
        );
      }
    } finally {
      if (mounted.current && seq === screenerSeq.current) {
        setScreenerLoading(false);
      }
    }
  }, [getAccessToken, screenerUniverse]);

  const onResetScreenerBaseline = useCallback(async () => {
    setResettingScreener(true);
    try {
      const token = await getAccessToken();
      if (!token) return;
      await resetOptionsLabScreenerBaseline(token);
      await refreshScreener();
    } finally {
      setResettingScreener(false);
    }
  }, [getAccessToken, refreshScreener]);

  const onSelectScreenerUnderlying = useCallback(
    (row: OptionsScreenerRow) => {
      const preset = presets.find((item) => item.symbol === row.underlying_symbol);
      const strike =
        row.strike_step ??
        preset?.strike_step ??
        config?.strike_step ??
        50;
      applyInstrumentSelection({
        underlying_symbol: row.underlying_symbol,
        underlying_label: row.underlying_label,
        fut_symbol: row.fut_symbol,
        strike_step: strike > 0 ? strike : 50,
      });
      if (row.atm != null) setScreenerAtmHint(Math.round(row.atm));
      setScreenerOpen(false);
    },
    [applyInstrumentSelection, config?.strike_step, presets],
  );

  // Mirror Options Lab stream / setup into Signal Engine when this tab mounts
  // or when Options Lab publishes a new instrument (same browser session).
  useEffect(() => {
    if (configLoading) return;
    const apply = (selection: DeskInstrumentSelection) => {
      const key = deskInstrumentKey(selection);
      if (key === lastDeskInstrumentKey.current) return;
      const current = configRef.current;
      const sameUnderlying =
        current?.underlying_symbol?.trim() === selection.underlying_symbol;
      const sameFut =
        (current?.fut_symbol || "").trim() ===
        (selection.fut_symbol || "").trim();
      const sameCe =
        !selection.ce_symbol ||
        (current?.ce_symbol || "").trim() === selection.ce_symbol.trim();
      const samePe =
        !selection.pe_symbol ||
        (current?.pe_symbol || "").trim() === selection.pe_symbol.trim();
      if (sameUnderlying && sameFut && sameCe && samePe) {
        lastDeskInstrumentKey.current = key;
        return;
      }
      lastDeskInstrumentKey.current = key;
      applyInstrumentSelection({
        underlying_symbol: selection.underlying_symbol,
        underlying_label: selection.underlying_label,
        fut_symbol: selection.fut_symbol,
        strike_step: selection.strike_step,
        ce_symbol: selection.ce_symbol,
        pe_symbol: selection.pe_symbol,
        clearOptions: !selection.ce_symbol && !selection.pe_symbol,
      });
    };

    const existing = readDeskInstrument();
    if (existing) apply(existing);
    return subscribeDeskInstrument(apply);
  }, [applyInstrumentSelection, configLoading]);

  useEffect(() => {
    if (!screenerOpen) return;
    void refreshScreener();
    const timer = window.setInterval(() => void refreshScreener(), SCREENER_POLL_MS);
    return () => {
      window.clearInterval(timer);
      screenerAbortRef.current?.abort();
    };
  }, [refreshScreener, screenerOpen]);

  // Live metrics ATM wins once it matches the configured underlying.
  useEffect(() => {
    if (metricsAtmAligned != null) setScreenerAtmHint(null);
  }, [metricsAtmAligned]);

  useEffect(() => {
    mounted.current = true;
    if (!isLoaded || !isSignedIn) return;

    const controller = new AbortController();
    let cancelled = false;

    void (async () => {
      let backoffMs = 500;
      while (!cancelled && mounted.current) {
        setStreaming(true);
        try {
          const token = await getAccessToken();
          if (!token || cancelled || !mounted.current) return;
          await streamSignalState({
            accessToken: token,
            signal: controller.signal,
            onState: (data) => {
              if (mounted.current) {
                setState(data);
                setError(null);
                setStreaming(true);
              }
            },
          });
          // Clean close — reconnect promptly.
          backoffMs = 500;
        } catch (err) {
          if (!mounted.current || cancelled || controller.signal.aborted) return;
          setError(err instanceof Error ? err.message : "Signal stream failed");
          setStreaming(false);
          void refreshOnce();
        } finally {
          if (mounted.current && !cancelled) setStreaming(false);
        }
        if (cancelled || controller.signal.aborted || !mounted.current) return;
        await new Promise((resolve) => setTimeout(resolve, backoffMs));
        backoffMs = Math.min(backoffMs * 2, 8_000);
      }
    })();

    return () => {
      cancelled = true;
      mounted.current = false;
      controller.abort();
    };
  }, [getAccessToken, isLoaded, isSignedIn, refreshOnce]);

  async function onToggleEngine(nextEnabled: boolean) {
    if (engineBusy) return;
    setEngineBusy(true);
    setError(null);
    try {
      await patchConfigImmediate({ engine_enabled: nextEnabled });
    } catch (err) {
      if (mounted.current) {
        setError(
          err instanceof Error ? err.message : "Failed to update engine state",
        );
      }
    } finally {
      if (mounted.current) setEngineBusy(false);
    }
  }

  async function onPublish(editedTitle: string, editedBody: string) {
    setPublishing(true);
    setPublishMsg(null);
    try {
      const token = await getAccessToken();
      if (!token) {
        setPublishMsg("Not signed in — cannot publish.");
        return;
      }
      const result = await publishSignalEntry(token, {
        title: editedTitle.trim(),
        body: editedBody.trim(),
      });
      if (result.ok) {
        setNotifyOpen(false);
        setPublishMsg(
          result.deduped
            ? "Already notified (deduped)."
            : `Notified ${result.notification?.recipient_count ?? 0} users.`,
        );
      } else {
        setPublishMsg(result.error ?? "Publish failed");
      }
    } catch (err) {
      setPublishMsg(err instanceof Error ? err.message : "Publish failed");
    } finally {
      setPublishing(false);
    }
  }

  const buyTone = buySignalTone(state, state?.entry, engineEnabled);

  function openNotifyDialog() {
    setNotifyTitle(defaultNotifyTitle(buyTone));
    setNotifyBody(defaultNotifyBody(state?.entry, state));
    setNotifyOpen(true);
  }

  const entry = state?.entry;
  const entryReady = Boolean(state?.entry_ready);
  const warnings = state?.live_warnings ?? [];
  const mockMode = Boolean(config?.mock ?? state?.mock);
  const fetchedLabel = formatEvaluatedAt(state?.evaluated_at);
  const feedSource = state?.feed_source ?? null;
  // Only the provisional Start frame is "Starting". A live snapshot with
  // engine_computing=true just means a refresh is in flight under load —
  // do not flip Running → Starting on every lock heartbeat.
  const engineStarting = feedSource === "starting";
  // Prefer server stopped frame over local Start optimism when SSE still says stopped.
  const serverStopped =
    feedSource === "stopped" || state?.engine_enabled === false;
  const showStopped =
    !engineEnabled || (serverStopped && !engineStarting);
  const noLivePrint =
    !engineStarting &&
    (Boolean(state?.live_quote_missing) ||
      warnings.some((w) =>
        /no live print|no live quote|credentials missing|access_token rejected|api_key is required|select an underlying/i.test(
          w,
        ),
      ));
  // Engine/stream can be up while broker LTP is missing — don't call that Running.
  const engineHealthy =
    !showStopped && !engineStarting && engineRunning && !noLivePrint;

  return (
    <section className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-line bg-raised/20 p-2">
      <header className="shrink-0 space-y-1.5 border-b border-line pb-1.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
              <Badge
                tone={
                  showStopped
                    ? "neutral"
                    : engineStarting
                      ? "info"
                      : noLivePrint
                        ? "warning"
                        : engineStale
                          ? "warning"
                          : engineHealthy
                            ? "success"
                            : "warning"
                }
                live={engineHealthy}
              >
                {showStopped
                  ? "Stopped"
                  : engineStarting
                    ? "Starting"
                    : noLivePrint
                      ? "No quote"
                      : engineStale
                        ? "Stale"
                        : engineHealthy
                          ? "Running"
                          : "Reconnecting"}
              </Badge>
              {!showStopped ? (
                <Badge tone={streaming ? "success" : "warning"} dot={false}>
                  Stream {streaming ? "connected" : "…"}
                </Badge>
              ) : null}
              {!showStopped && state?.ticker?.path ? (
                <span
                  title={
                    state.ticker.path === "ws"
                      ? "Quotes from Kite WebSocket book"
                      : "Quotes via REST/sandbox — may lag under load"
                  }
                >
                  <Badge
                    tone={
                      state.ticker.path === "ws"
                        ? "success"
                        : state.ticker.path === "rest"
                          ? "warning"
                          : "neutral"
                    }
                    dot={false}
                  >
                    Quotes {state.ticker.path.toUpperCase()}
                  </Badge>
                </span>
              ) : null}
              {mockMode ? (
                <Badge tone="info">Mock</Badge>
              ) : null}
              {saveStatus === "pending" || saveStatus === "saving" ? (
                <Badge tone="warning">Saving…</Badge>
              ) : saveStatus === "saved" ? (
                <Badge tone="success">Saved</Badge>
              ) : null}
            <span className="text-xs text-slate-muted">
              {state?.underlying?.label ?? "No underlying"} ·{" "}
              {state ? `${state.passed}/${state.evaluable} passing` : "—"} ·{" "}
              {showStopped
                ? "engine stopped"
                : engineStarting
                  ? "starting…"
                  : mockMode
                    ? "mock feed"
                    : (feedSource ?? "…")}
              {state && state.has_broker === false && !mockMode && !showStopped
                ? " · broker not bound"
                : ""}
            </span>
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            <Button
              variant={screenerOpen ? "primary" : "secondary"}
              size="sm"
              icon={<SearchIcon />}
              onClick={() => {
                setBooksOpen(false);
                setScreenerOpen((open) => !open);
              }}
            >
              Screener
            </Button>
            <Button
              variant={booksOpen ? "primary" : "secondary"}
              size="sm"
              icon={<BookIcon />}
              onClick={() => {
                setScreenerOpen(false);
                setBooksOpen((open) => !open);
              }}
            >
              Books
            </Button>
            <label
              htmlFor="signal-mock"
              title="Rehearsal mode — demo metrics without live broker quotes"
              className="flex cursor-pointer items-center gap-1.5 rounded-md border border-line bg-raised px-2 py-1 text-xs font-medium text-ink"
            >
              <input
                id="signal-mock"
                type="checkbox"
                checked={Boolean(config?.mock)}
                onChange={(e) => patchConfig({ mock: e.target.checked })}
                className="size-3.5 shrink-0 rounded border-line text-teal focus-visible:ring-2 focus-visible:ring-teal/30"
              />
              Mock
            </label>
            {engineEnabled ? (
              <Button
                variant="secondary"
                size="sm"
                icon={<PauseIcon />}
                disabled={engineBusy}
                onClick={() => void onToggleEngine(false)}
              >
                {engineBusy ? "Stopping…" : "Stop"}
              </Button>
            ) : (
              <Button
                size="sm"
                icon={<PlayIcon />}
                disabled={engineBusy}
                onClick={() => void onToggleEngine(true)}
              >
                {engineBusy ? "Starting…" : "Start"}
              </Button>
            )}
            <Button
              variant="secondary"
              size="sm"
              icon={<RefreshIcon />}
              onClick={() => void refreshOnce()}
              aria-label="Refresh signal metrics"
            >
              Refresh
            </Button>
          </div>
        </div>
        {failingRules.length > 0 || fetchedLabel ? (
          <div
            className={cn(
              "flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs text-amber-950",
              failingRules.length > 0 ? "justify-between" : "justify-end",
            )}
          >
            {failingRules.length > 0 ? (
              <p className="min-w-0">
                <span className="font-medium">Failing rules:</span>{" "}
                {failingRules.join(" · ")}
              </p>
            ) : null}
            {fetchedLabel ? (
              <span
                className="shrink-0 whitespace-nowrap tnum tabular-nums"
                title={`Last signal fetch: ${fetchedLabel}`}
              >
                <span className="font-medium">Fetched</span> {fetchedLabel}
              </span>
            ) : null}
          </div>
        ) : null}
      </header>

      {error ? <p className="mt-2 text-sm text-rose-600">{error}</p> : null}
      {configError ? (
        <p className="mt-2 text-sm text-rose-600">{configError}</p>
      ) : null}

      {warnings.length > 0 ? (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
          <button
            type="button"
            className="font-medium hover:underline"
            onClick={() => setWarningsOpen((open) => !open)}
          >
            {warnings.length} setup warning{warnings.length === 1 ? "" : "s"}
            {warningsOpen ? " (hide)" : " (show all)"}
          </button>
          {warningsOpen ? (
            <ul className="mt-2 list-inside list-disc space-y-1">
              {warnings.map((msg) => (
                <li key={msg}>{msg}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-xs">{warnings[0]}</p>
          )}
        </div>
      ) : null}

      <BuySignalBanner
        state={state}
        entry={entry}
        engineEnabled={engineEnabled}
        entryReady={entryReady}
        publishing={publishing}
        publishMsg={publishMsg}
        onOpenNotify={openNotifyDialog}
      />

      <NotifySignalDialog
        open={notifyOpen}
        tone={buyTone}
        title={notifyTitle}
        body={notifyBody}
        publishing={publishing}
        onTitleChange={setNotifyTitle}
        onBodyChange={setNotifyBody}
        onClose={() => {
          if (!publishing) setNotifyOpen(false);
        }}
        onSend={() => void onPublish(notifyTitle, notifyBody)}
      />

      <div className="mt-2 shrink-0">
        <SignalSetupBar
          config={config}
          presets={presets}
          presetKey={presetKey}
          presetLocked={presetLocked}
          onPresetChange={onPresetChange}
          patchConfig={patchConfig}
          loading={configLoading}
          atmHint={atmHint}
        />
      </div>

      <div className="mt-2 min-h-0 flex-1 overflow-auto">
        {metrics.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-muted">
            Waiting for signal stream…
          </p>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-end gap-2 px-1">
              <Button
                variant="secondary"
                size="sm"
                icon={<ChevronDownIcon />}
                onClick={expandAllCategories}
              >
                Expand all
              </Button>
              <Button
                variant="secondary"
                size="sm"
                icon={<ArrowUpIcon />}
                onClick={collapseAllCategories}
              >
                Collapse all
              </Button>
            </div>
            {metricGroups.map(({ category, rows, passing }) => (
              <MetricCategoryWidget
                key={category}
                category={category}
                rows={rows}
                passing={passing}
                open={!collapsedCategories.has(category)}
                onToggle={() => toggleCategory(category)}
                config={config}
                patchConfig={patchConfig}
                valueTicks={valueTicks}
              />
            ))}
          </div>
        )}
      </div>

      {state ? (
        <p className="mt-1 shrink-0 text-xs text-slate-muted">
          {metrics.length} checklist metrics · stream {state.poll_ms}ms · broker{" "}
          {state.broker_poll_ms ?? 500}ms
        </p>
      ) : null}

      {booksOpen ? (
        <div className="absolute inset-0 z-30 flex justify-end bg-ink/35">
          <button
            type="button"
            aria-label="Close overlay"
            className="absolute inset-0 cursor-default"
            onClick={() => setBooksOpen(false)}
          />
          <aside
            role="dialog"
            aria-modal="true"
            aria-label="Desk books"
            className="relative z-10 flex h-full w-full max-w-3xl flex-col border-l border-line bg-canvas shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-3 py-2">
              <h2 className="flex items-center gap-2 font-display text-base font-semibold text-ink">
                <BookIcon className="h-4 w-4" />
                Books
              </h2>
              <button
                type="button"
                onClick={() => setBooksOpen(false)}
                aria-label="Close"
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-muted hover:bg-fog/70 hover:text-ink"
              >
                <CloseIcon className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto px-3 pb-3">
              <DeskBooksPanel
                className="mt-2"
                snapshot={deskSnapshot}
                customer={false}
                brokerTools={brokerTools}
                refreshing={refreshing}
                onRefresh={onRefreshBooks}
                fetchedAt={fetchedAt}
                rangeDays={rangeDays}
              />
            </div>
          </aside>
        </div>
      ) : null}

      {screenerOpen ? (
        <div className="absolute inset-0 z-30 flex justify-end bg-ink/35">
          <button
            type="button"
            aria-label="Close overlay"
            className="absolute inset-0 cursor-default"
            onClick={() => setScreenerOpen(false)}
          />
          <aside
            role="dialog"
            aria-modal="true"
            aria-label="Screener"
            className="relative z-10 flex h-full w-full max-w-5xl flex-col border-l border-line bg-canvas shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-3 py-2">
              <h2 className="flex items-center gap-2 font-display text-base font-semibold text-ink">
                <SearchIcon className="h-4 w-4" />
                Screener
              </h2>
              <button
                type="button"
                onClick={() => setScreenerOpen(false)}
                aria-label="Close"
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-muted hover:bg-fog/70 hover:text-ink"
              >
                <CloseIcon className="h-4 w-4" />
              </button>
            </div>
            {screenerError ? (
              <div className="mx-3 mt-2 rounded-md border border-rose/30 bg-rose/10 px-2 py-1 text-xs text-rose">
                {screenerError}
              </div>
            ) : null}
            <div className="min-h-0 flex-1 overflow-auto px-3 pb-3">
              <OptionsLabScreenerPanel
                snapshot={screener}
                loading={screenerLoading}
                universe={screenerUniverse}
                onUniverseChange={setScreenerUniverse}
                onRefresh={() => void refreshScreener()}
                onResetBaseline={() => void onResetScreenerBaseline()}
                resetting={resettingScreener}
                onSelectUnderlying={onSelectScreenerUnderlying}
              />
            </div>
          </aside>
        </div>
      ) : null}
    </section>
  );
}

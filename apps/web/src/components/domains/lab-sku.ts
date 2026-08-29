/**
 * End-user Lab SKU helpers.
 *
 * ``/lab/{instrument}`` is request-scoped: the chain read and the chain stream
 * both carry the segment as ``?underlying=``, so two windows on different
 * underlyings no longer clobber each other. See docs/desk-architecture-roadmap.md.
 */

/**
 * Overlays inside the Lab panel.
 *
 * Declared here rather than in `OptionsLabPanel` so the trader workspace can map
 * `?tool=` without statically importing the panel — that import would defeat the
 * `dynamic()` lazy-load and pull the whole Lab tree into the landing bundle.
 */
export type DeskOverlay =
  | null
  | "screener"
  | "books"
  | "heatmap"
  | "flows"
  | "backtest"
  | "bots"
  | "ideas";

/** Tool names accepted in `?tool=` on the Lab route (instrument → tool flow). */
const TOOL_QUERY_TO_OVERLAY: Record<string, DeskOverlay> = {
  chain: null,
  ideas: "ideas",
  backtest: "backtest",
  bots: "bots",
  automation: "bots",
};

export function overlayForTool(tool: string | null | undefined): DeskOverlay {
  if (!tool) return null;
  return TOOL_QUERY_TO_OVERLAY[tool.trim().toLowerCase()] ?? null;
}

/** Build the instrument-scoped route for one tool pick on the workspace landing. */
export function instrumentToolPath(
  tenantSlug: string,
  symbol: string,
  tool: string,
): string {
  const slug = tenantSlug.trim();
  const seg = encodeURIComponent(symbol.trim());
  const name = tool.trim().toLowerCase();
  if (name === "signal") return `/t/${slug}/signal/${seg}`;
  if (name === "chart") return `/t/${slug}/chart/${seg}`;
  const toolQuery =
    name === "chain" || name === "bots" || name === "automation"
      ? name === "bots" || name === "automation"
        ? "?tool=bots"
        : ""
      : `?tool=${encodeURIComponent(name)}`;
  return `/t/${slug}/lab/${seg}${toolQuery}`;
}

function normalizeSymbol(value: string | null | undefined): string {
  return (value ?? "").trim().toUpperCase();
}

/**
 * True when the URL asks for one instrument but the desk streams another —
 * the case the UI must disclose rather than mislabel.
 *
 * Returns false when either side is unknown: an empty URL segment or a chain
 * that has not loaded yet is not a mismatch, just missing data.
 */
export function isInstrumentMismatch(
  urlInstrument: string | null | undefined,
  streamedUnderlying: string | null | undefined,
): boolean {
  const wanted = normalizeSymbol(urlInstrument);
  const streamed = normalizeSymbol(streamedUnderlying);
  if (!wanted || !streamed) return false;
  return wanted !== streamed;
}

/** Which Lab tool groups a viewer may open, mirroring backend contexts. */
export type LabToolAccess = {
  /** Screener, Heat map, Ideas, Backtest, Bot — TraderContext. */
  automation: boolean;
  /** Flows, Books, Mock, resets, setup edits — AdminContext. */
  admin: boolean;
};

export function labToolAccess({
  readOnly = false,
  automationEnabled,
}: {
  readOnly?: boolean;
  automationEnabled?: boolean;
}): LabToolAccess {
  return {
    // Defaults to !readOnly so the admin desk is unchanged when the flag is
    // not passed; the Lab SKU passes readOnly + automationEnabled together.
    automation: automationEnabled ?? !readOnly,
    admin: !readOnly,
  };
}

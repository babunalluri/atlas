import { describe, expect, it } from "vitest";

import { rankIdeasWithPop } from "@/components/domains/options-lab-ideas";
import type { OptionsScreenerRow } from "@/lib/api/admin";

function row(partial: Partial<OptionsScreenerRow> & Pick<OptionsScreenerRow, "underlying_symbol">): OptionsScreenerRow {
  return {
    underlying_label: partial.underlying_label ?? partial.underlying_symbol,
    fut_symbol: partial.fut_symbol ?? "NFO:NIFTY26AUGFUT",
    spot: partial.spot ?? 24500,
    atm: partial.atm ?? 24500,
    atm_iv: partial.atm_iv ?? 14,
    straddle: partial.straddle ?? 200,
    pcr: partial.pcr ?? 0.7,
    max_pain: partial.max_pain ?? 24500,
    chain_ce_oi: null,
    chain_pe_oi: null,
    oi_pct_chg: partial.oi_pct_chg ?? 1,
    iv_chg: null,
    ivp: partial.ivp ?? 45,
    error: partial.error ?? null,
    ...partial,
  };
}

describe("rankIdeasWithPop", () => {
  const now = new Date("2026-08-22T06:30:00Z"); // IST afternoon-ish for DTE

  it("ranks bullish PCR with PoP and E[PnL]", () => {
    const ideas = rankIdeasWithPop(
      [row({ underlying_symbol: "NSE:NIFTY 50", underlying_label: "Nifty 50", pcr: 0.7, ivp: 40 })],
      {},
      now,
    );
    expect(ideas.length).toBe(1);
    expect(ideas[0]!.templateId).toBe("bull_call_spread");
    expect(ideas[0]!.pop).not.toBeNull();
    expect(ideas[0]!.pop!).toBeGreaterThan(0);
    expect(ideas[0]!.expectedPnl).not.toBeNull();
    expect(ideas[0]!.dte).toBeGreaterThan(4);
    expect(ideas[0]!.dte!).toBeLessThan(6);
  });

  it("skips rows with missing IVP (no invent)", () => {
    const bad = row({ underlying_symbol: "NSE:NIFTY 50" });
    (bad as { ivp: number | null }).ivp = null;
    expect(rankIdeasWithPop([bad], {}, now)).toEqual([]);
  });

  it("filters by universe and min PoP deterministically", () => {
    const rows = [
      row({
        underlying_symbol: "NSE:NIFTY 50",
        underlying_label: "Nifty",
        pcr: 0.7,
        ivp: 40,
      }),
      row({
        underlying_symbol: "NSE:RELIANCE",
        underlying_label: "Reliance",
        pcr: 0.7,
        ivp: 40,
        fut_symbol: "NFO:RELIANCE26AUGFUT",
      }),
    ];
    const indicesOnly = rankIdeasWithPop(rows, { universe: "indices" }, now);
    expect(indicesOnly.every((i) => i.row.underlying_symbol.includes("NIFTY"))).toBe(true);

    const highPop = rankIdeasWithPop(rows, { minPop: 99.9 }, now);
    expect(highPop.length).toBe(0);
  });

  it("suggests iron condor for elevated IVP", () => {
    const ideas = rankIdeasWithPop(
      [row({ underlying_symbol: "NSE:NIFTY 50", pcr: 1.0, ivp: 78 })],
      {},
      now,
    );
    expect(ideas[0]!.templateId).toBe("iron_condor");
  });
});

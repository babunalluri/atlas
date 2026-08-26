import { describe, expect, it } from "vitest";

import {
  mergeResolvedOptionSymbols,
  signalConfigSnapshot,
} from "@/components/domains/signal-resolved-options";
import type { SignalEngineAdminConfig } from "@/lib/api/admin";

function baseConfig(
  overrides: Partial<SignalEngineAdminConfig> = {},
): SignalEngineAdminConfig {
  return {
    underlying_symbol: "NSE:NIFTY 50",
    underlying_label: "NIFTY 50",
    fut_symbol: "NFO:NIFTY26SEPFUT",
    ce_symbol: "",
    pe_symbol: "",
    crude_symbol: "MCX:CRUDEOILM",
    india_vix_symbol: "NSE:INDIA VIX",
    strike_step: 50,
    pcr: null,
    max_pain: null,
    ivp: null,
    dow_change_pct: null,
    ...overrides,
  };
}

describe("mergeResolvedOptionSymbols", () => {
  it("fills empty CE/PE from live ATM", () => {
    const prev = baseConfig();
    const saved = signalConfigSnapshot(prev);
    const next = mergeResolvedOptionSymbols(
      prev,
      {
        ce_symbol: "NFO:NIFTY26SEP24200CE",
        pe_symbol: "NFO:NIFTY26SEP24200PE",
      },
      saved,
    );
    expect(next?.ce_symbol).toBe("NFO:NIFTY26SEP24200CE");
    expect(next?.pe_symbol).toBe("NFO:NIFTY26SEP24200PE");
  });

  it("rolls ATM when local is clean and both sides move", () => {
    const prev = baseConfig({
      ce_symbol: "NFO:NIFTY26SEP24200CE",
      pe_symbol: "NFO:NIFTY26SEP24200PE",
    });
    const saved = signalConfigSnapshot(prev);
    const next = mergeResolvedOptionSymbols(
      prev,
      {
        ce_symbol: "NFO:NIFTY26SEP24300CE",
        pe_symbol: "NFO:NIFTY26SEP24300PE",
      },
      saved,
    );
    expect(next?.ce_symbol).toBe("NFO:NIFTY26SEP24300CE");
    expect(next?.pe_symbol).toBe("NFO:NIFTY26SEP24300PE");
  });

  it("does not overwrite a dirty local edit", () => {
    const prev = baseConfig({
      ce_symbol: "NFO:NIFTY26SEP24100CE",
      pe_symbol: "NFO:NIFTY26SEP24100PE",
    });
    const saved = signalConfigSnapshot(
      baseConfig({
        ce_symbol: "NFO:NIFTY26SEP24200CE",
        pe_symbol: "NFO:NIFTY26SEP24200PE",
      }),
    );
    const next = mergeResolvedOptionSymbols(
      prev,
      {
        ce_symbol: "NFO:NIFTY26SEP24300CE",
        pe_symbol: "NFO:NIFTY26SEP24300PE",
      },
      saved,
    );
    expect(next).toBeNull();
  });

  it("returns null when nothing to apply", () => {
    const prev = baseConfig({
      ce_symbol: "NFO:NIFTY26SEP24200CE",
      pe_symbol: "NFO:NIFTY26SEP24200PE",
    });
    expect(
      mergeResolvedOptionSymbols(
        prev,
        {
          ce_symbol: "NFO:NIFTY26SEP24200CE",
          pe_symbol: "NFO:NIFTY26SEP24200PE",
        },
        signalConfigSnapshot(prev),
      ),
    ).toBeNull();
  });
});

describe("underlying SSE guard contract", () => {
  it("documents that callers must match underlying before merge", () => {
    // Regression for NIFTY→SENSEX: empty SENSEX CE must not be filled from a
    // lagging NIFTY SSE frame. The panel gates with
    // state.underlying.symbol === config.underlying_symbol before calling merge.
    const sensex = baseConfig({
      underlying_symbol: "BSE:SENSEX",
      underlying_label: "SENSEX",
      fut_symbol: "BFO:SENSEX26SEPFUT",
      ce_symbol: "",
      pe_symbol: "",
    });
    const niftyFrame = {
      ce_symbol: "NFO:NIFTY26SEP24200CE",
      pe_symbol: "NFO:NIFTY26SEP24200PE",
    };
    const frameUnderlying = "NSE:NIFTY 50";
    const aligned = frameUnderlying === sensex.underlying_symbol;
    expect(aligned).toBe(false);
    // When misaligned, panel must skip merge entirely.
    const merged = aligned
      ? mergeResolvedOptionSymbols(
          sensex,
          niftyFrame,
          signalConfigSnapshot(sensex),
        )
      : null;
    expect(merged).toBeNull();
    expect(sensex.ce_symbol).toBe("");
  });
});

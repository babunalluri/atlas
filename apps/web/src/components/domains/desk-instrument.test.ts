import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  deskHandoffOverridesLocal,
  deskInstrumentIdentityKey,
  deskInstrumentKey,
  publishDeskInstrument,
  readDeskInstrument,
} from "@/components/domains/desk-instrument";

// Export a test reset if missing — add below if not in module.
vi.stubGlobal("sessionStorage", (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => {
      store[k] = String(v);
    },
    removeItem: (k: string) => {
      delete store[k];
    },
    clear: () => {
      store = {};
    },
  };
})());

vi.stubGlobal("window", {
  ...globalThis,
  sessionStorage: globalThis.sessionStorage,
  dispatchEvent: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
});

describe("desk-instrument", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.mocked(window.dispatchEvent).mockClear();
  });

  it("publishes and reads back the selection", () => {
    const published = publishDeskInstrument({
      underlying_symbol: "NSE:NIFTY MID SELECT",
      underlying_label: "MIDCPNIFTY",
      fut_symbol: "NFO:MIDCPNIFTY26AUGFUT",
      strike_step: 25,
      ce_symbol: "NFO:MIDCPNIFTY26AUG14900CE",
      pe_symbol: "NFO:MIDCPNIFTY26AUG14900PE",
    });
    expect(published?.underlying_symbol).toBe("NSE:NIFTY MID SELECT");
    expect(readDeskInstrument()?.fut_symbol).toBe("NFO:MIDCPNIFTY26AUGFUT");
    expect(window.dispatchEvent).toHaveBeenCalled();
  });

  it("identity key ignores live chain CE/PE", () => {
    const base = {
      underlying_symbol: "NSE:NIFTY 50",
      underlying_label: "NIFTY",
      fut_symbol: "NFO:NIFTY26AUGFUT",
      strike_step: 50,
      updated_at_ms: 1,
      source: "options-lab" as const,
    };
    const a = deskInstrumentIdentityKey(base);
    const b = deskInstrumentIdentityKey({
      ...base,
      ce_symbol: "NFO:NIFTY26AUG24500CE",
      pe_symbol: "NFO:NIFTY26AUG24500PE",
    });
    expect(a).toBe(b);
    expect(deskInstrumentKey({ ...base, ce_symbol: "NFO:X", pe_symbol: "NFO:Y" })).not.toBe(
      deskInstrumentKey(base),
    );
  });

  it("detects when session handoff overrides local Lab config", () => {
    publishDeskInstrument({
      underlying_symbol: "BSE:SENSEX",
      underlying_label: "SENSEX",
      fut_symbol: "BFO:SENSEX26SEPFUT",
      strike_step: 100,
      source: "signal",
    });
    expect(
      deskHandoffOverridesLocal(
        {
          underlying_symbol: "NSE:NIFTY 50",
          fut_symbol: "NFO:NIFTY26SEPFUT",
          strike_step: 50,
        },
        undefined,
        "options-lab",
      ),
    ).toBe(true);
    expect(
      deskHandoffOverridesLocal(
        {
          underlying_symbol: "BSE:SENSEX",
          fut_symbol: "BFO:SENSEX26SEPFUT",
          strike_step: 100,
        },
        undefined,
        "options-lab",
      ),
    ).toBe(false);
    expect(
      deskHandoffOverridesLocal(
        {
          underlying_symbol: "NSE:NIFTY 50",
          fut_symbol: "NFO:NIFTY26SEPFUT",
          strike_step: 50,
        },
        undefined,
        "signal",
      ),
    ).toBe(false);
  });
});

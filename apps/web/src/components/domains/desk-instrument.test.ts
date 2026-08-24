import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  deskInstrumentKey,
  publishDeskInstrument,
  readDeskInstrument,
  resetDeskInstrumentForTests,
  subscribeDeskInstrument,
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

  it("builds a stable fingerprint", () => {
    const key = deskInstrumentKey({
      underlying_symbol: "NSE:NIFTY 50",
      underlying_label: "NIFTY",
      fut_symbol: "NFO:NIFTY26AUGFUT",
      strike_step: 50,
      ce_symbol: "NFO:NIFTY26AUG24500CE",
      pe_symbol: "NFO:NIFTY26AUG24500PE",
      updated_at_ms: 1,
      source: "options-lab",
    });
    expect(key).toContain("NSE:NIFTY 50");
    expect(key).toContain("NFO:NIFTY26AUGFUT");
  });
});

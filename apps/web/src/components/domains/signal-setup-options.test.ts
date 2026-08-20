import { describe, expect, it } from "vitest";

import {
  buildFutOptions,
  buildOptionSideOptions,
  buildStrikeStepOptions,
  deriveOptionSymbol,
  suggestFutSymbol,
} from "@/components/domains/signal-setup-options";

describe("signal setup options", () => {
  it("suggests monthly FUT symbols from underlying presets", () => {
    const august2026 = new Date("2026-08-20T06:00:00+05:30");
    expect(suggestFutSymbol("NSE:NIFTY 50", august2026)).toBe(
      "NFO:NIFTY26AUGFUT",
    );
    expect(suggestFutSymbol("NSE:BANKNIFTY", august2026)).toBe(
      "NFO:BANKNIFTY26AUGFUT",
    );
  });

  it("rolls to next month after monthly FUT expiry (IST)", () => {
    const afterExpiry = new Date("2026-08-28T06:00:00+05:30");
    expect(suggestFutSymbol("NSE:NIFTY 50", afterExpiry)).toBe(
      "NFO:NIFTY26SEPFUT",
    );
    const onExpiry = new Date("2026-08-27T15:30:00+05:30");
    expect(suggestFutSymbol("NSE:NIFTY 50", onExpiry)).toBe(
      "NFO:NIFTY26AUGFUT",
    );
  });

  it("uses IST calendar date, not browser-local midnight", () => {
    const utcEvening = new Date("2026-08-31T20:00:00.000Z");
    expect(suggestFutSymbol("NSE:NIFTY 50", utcEvening)).toBe(
      "NFO:NIFTY26SEPFUT",
    );
  });

  it("buildFutOptions dedupes current and suggested symbols", () => {
    expect(
      buildFutOptions("NFO:NIFTY26AUGFUT", "NSE:NIFTY 50").map((o) => o.value),
    ).toEqual(["NFO:NIFTY26AUGFUT"]);
    expect(buildFutOptions("", "")).toEqual([]);
  });

  it("derives CE/PE symbols from a FUT prefix", () => {
    expect(
      deriveOptionSymbol("NFO:NIFTY26AUGFUT", 24500, "CE"),
    ).toBe("NFO:NIFTY26AUG24500CE");
    expect(
      deriveOptionSymbol("NFO:NIFTY26AUGFUT", 24500, "PE"),
    ).toBe("NFO:NIFTY26AUG24500PE");
  });

  it("builds strike step options from presets and current value", () => {
    const options = buildStrikeStepOptions(
      [{ label: "NIFTY", symbol: "NSE:NIFTY", strike_step: 50 }],
      50,
    );
    expect(options.map((option) => option.value)).toContain("50");
    expect(options.map((option) => option.value)).toContain("100");
  });

  it("suggests ATM-relative option symbols when fut and atm are known", () => {
    const options = buildOptionSideOptions(
      "NFO:NIFTY26AUGFUT",
      "CE",
      24300,
      50,
      "NFO:NIFTY26AUG24500CE",
    );
    expect(options.map((option) => option.value)).toContain(
      "NFO:NIFTY26AUG24300CE",
    );
    expect(options.map((option) => option.value)).toContain(
      "NFO:NIFTY26AUG24500CE",
    );
  });
});

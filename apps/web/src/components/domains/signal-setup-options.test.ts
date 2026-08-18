import { describe, expect, it } from "vitest";

import {
  buildOptionSideOptions,
  buildStrikeStepOptions,
  deriveOptionSymbol,
} from "@/components/domains/signal-setup-options";

describe("signal setup options", () => {
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

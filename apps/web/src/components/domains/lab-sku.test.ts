import { describe, expect, it } from "vitest";

import {
  instrumentToolPath,
  isInstrumentMismatch,
  labToolAccess,
} from "@/components/domains/lab-sku";

describe("isInstrumentMismatch", () => {
  it("is false when the URL matches the streamed underlying", () => {
    expect(isInstrumentMismatch("NSE:NIFTY 50", "NSE:NIFTY 50")).toBe(false);
  });

  it("ignores case and surrounding whitespace", () => {
    expect(isInstrumentMismatch(" nse:nifty 50 ", "NSE:NIFTY 50")).toBe(false);
  });

  it("is true when the URL asks for a different instrument", () => {
    expect(isInstrumentMismatch("BSE:SENSEX", "NSE:NIFTY 50")).toBe(true);
  });

  it("is false while either side is unknown", () => {
    // Chain not loaded yet, or no instrument in the URL — missing, not wrong.
    expect(isInstrumentMismatch("BSE:SENSEX", null)).toBe(false);
    expect(isInstrumentMismatch(null, "NSE:NIFTY 50")).toBe(false);
    expect(isInstrumentMismatch("", "NSE:NIFTY 50")).toBe(false);
    expect(isInstrumentMismatch("  ", "NSE:NIFTY 50")).toBe(false);
  });
});

describe("instrumentToolPath", () => {
  it("routes Lab chain to /lab/{instrument}", () => {
    expect(
      instrumentToolPath("stockbroker", "NSE:NIFTY 50", "chain"),
    ).toBe("/t/stockbroker/lab/NSE%3ANIFTY%2050");
  });

  it("routes SENSEX automation with ?tool=bots", () => {
    expect(instrumentToolPath("stockbroker", "BSE:SENSEX", "bots")).toBe(
      "/t/stockbroker/lab/BSE%3ASENSEX?tool=bots",
    );
  });

  it("routes Signal and Chart to their own windows", () => {
    expect(instrumentToolPath("acme", "BSE:SENSEX", "signal")).toBe(
      "/t/acme/signal/BSE%3ASENSEX",
    );
    expect(instrumentToolPath("acme", "NSE:NIFTY 50", "chart")).toBe(
      "/t/acme/chart/NSE%3ANIFTY%2050",
    );
  });
});

describe("labToolAccess", () => {
  it("admin desk keeps automation and admin tools", () => {
    expect(labToolAccess({ readOnly: false })).toEqual({
      automation: true,
      admin: true,
    });
  });

  it("plain viewer gets neither (unchanged customer desk)", () => {
    expect(labToolAccess({ readOnly: true })).toEqual({
      automation: false,
      admin: false,
    });
  });

  it("Lab SKU gets automation without admin tools", () => {
    expect(labToolAccess({ readOnly: true, automationEnabled: true })).toEqual({
      automation: true,
      admin: false,
    });
  });

  it("automationEnabled=false overrides the readOnly default", () => {
    expect(labToolAccess({ readOnly: false, automationEnabled: false })).toEqual({
      automation: false,
      admin: true,
    });
  });
});

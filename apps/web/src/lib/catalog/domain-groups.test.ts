import { describe, expect, it } from "vitest";

import {
  canonicalCatalogSlug,
  classifyCatalogSlug,
  groupCatalogItems,
  resolveCatalogDomain,
} from "@/lib/catalog/domain-groups";

describe("canonicalCatalogSlug", () => {
  it("strips import/clone copy suffixes", () => {
    expect(canonicalCatalogSlug("learning-guide-copy")).toBe("learning-guide");
    expect(canonicalCatalogSlug("paper-trading-copy-2")).toBe("paper-trading");
  });
});

describe("classifyCatalogSlug", () => {
  it("maps stock broker desks and dental slugs", () => {
    expect(classifyCatalogSlug("live-trader")).toEqual({
      domain: "stock_broker",
      desk: "live",
    });
    expect(classifyCatalogSlug("front-desk-team")).toEqual({
      domain: "dental_clinic",
      desk: null,
    });
  });
});

describe("resolveCatalogDomain", () => {
  it("keeps stock broker packs out of general after a renamed copy slug", () => {
    expect(resolveCatalogDomain("learning-copy", "generic")).toBe(
      "stock_broker",
    );
  });

  it("uses stored domain for custom slugs imported into another org", () => {
    expect(resolveCatalogDomain("research-bot", "stock_broker")).toBe(
      "stock_broker",
    );
  });
});

describe("groupCatalogItems", () => {
  it("groups by domain then stock broker desk", () => {
    const groups = groupCatalogItems([
      { id: "1", slug: "learning-guide", domain: "stock_broker" },
      { id: "2", slug: "paper-trader-copy", domain: "generic" },
      { id: "3", slug: "front-desk", domain: "dental_clinic" },
      { id: "4", slug: "research-bot", domain: "stock_broker" },
      { id: "5", slug: "notes", domain: "generic" },
    ]);
    expect(groups.map((group) => group.domain)).toEqual([
      "stock_broker",
      "dental_clinic",
      "generic",
    ]);
    const broker = groups[0];
    expect(broker.desks.map((desk) => desk.label)).toEqual([
      "Learning",
      "Paper trading",
      "Other",
    ]);
    expect(broker.desks[0].items.map((item) => item.slug)).toEqual([
      "learning-guide",
    ]);
    expect(broker.desks[2].items.map((item) => item.slug)).toEqual([
      "research-bot",
    ]);
  });

  it("filters by domain pill", () => {
    const groups = groupCatalogItems(
      [
        { id: "1", slug: "learning", domain: "stock_broker" },
        { id: "2", slug: "notes", domain: "generic" },
      ],
      "stock_broker",
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].domain).toBe("stock_broker");
  });
});

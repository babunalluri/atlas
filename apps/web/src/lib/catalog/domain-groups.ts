/**
 * Classify catalog entities by workspace domain.
 *
 * Domain comes from the API when present. Slugs from domain starter packs
 * (including `{slug}-copy`) still classify after export → import into another
 * org, so Stock Broker desks are not dumped into General.
 *
 * Admin list pages stay flat. Grouping is only for pack copy (platform import).
 */

export type CatalogDomainId = "stock_broker" | "dental_clinic" | "generic";
export type CatalogDomainFilter = "all" | CatalogDomainId;
export type CatalogDeskId = "learning" | "paper" | "live" | "other";

export const CATALOG_DOMAIN_LABELS: Record<CatalogDomainId, string> = {
  stock_broker: "Stock Broker",
  dental_clinic: "Dental Clinic",
  generic: "General",
};

const DESK_LABELS: Record<Exclude<CatalogDeskId, "other">, string> = {
  learning: "Learning",
  paper: "Paper trading",
  live: "Live trading",
};

const DOMAIN_ORDER: CatalogDomainId[] = [
  "stock_broker",
  "dental_clinic",
  "generic",
];

const DESK_ORDER: CatalogDeskId[] = ["learning", "paper", "live", "other"];

const STOCK_BROKER_SLUGS: Record<string, CatalogDeskId> = {
  learning: "learning",
  "learning-guide": "learning",
  "paper-trading": "paper",
  "paper-trader": "paper",
  "paper-from-signal": "paper",
  "live-trading": "live",
  "live-trader": "live",
  "live-approval": "live",
};

const DENTAL_SLUGS = new Set([
  "front-desk",
  "patient-concierge",
  "clinician-copilot",
  "front-desk-team",
  "patient-support",
  "book-appointment",
  "recall-reminder",
]);

export function canonicalCatalogSlug(slug: string): string {
  return slug.trim().toLowerCase().replace(/-copy(?:-\d+)?$/i, "");
}

export function coerceCatalogDomain(
  value: string | null | undefined,
): CatalogDomainId {
  const raw = (value || "generic").trim().toLowerCase().replace(/-/g, "_");
  if (raw === "general") return "generic";
  if (raw === "stock_broker" || raw === "dental_clinic" || raw === "generic") {
    return raw;
  }
  return "generic";
}

export function classifyCatalogSlug(slug: string): {
  domain: CatalogDomainId;
  desk: CatalogDeskId | null;
} {
  const canonical = canonicalCatalogSlug(slug);
  if (canonical in STOCK_BROKER_SLUGS) {
    return { domain: "stock_broker", desk: STOCK_BROKER_SLUGS[canonical] };
  }
  if (DENTAL_SLUGS.has(canonical)) {
    return { domain: "dental_clinic", desk: null };
  }
  return { domain: "generic", desk: null };
}

export function resolveCatalogDomain(
  slug: string,
  storedDomain?: string | null,
): CatalogDomainId {
  const fromSlug = classifyCatalogSlug(slug).domain;
  if (fromSlug !== "generic") return fromSlug;
  return coerceCatalogDomain(storedDomain);
}

export type CatalogGroupable = {
  id: string;
  slug: string;
  domain?: string | null;
};

export type CatalogDeskGroup<T> = {
  key: string;
  desk: CatalogDeskId | null;
  label: string | null;
  items: T[];
};

export type CatalogDomainGroup<T> = {
  domain: CatalogDomainId;
  label: string;
  desks: CatalogDeskGroup<T>[];
};

export function groupCatalogItems<T extends CatalogGroupable>(
  items: T[],
  filter: CatalogDomainFilter = "all",
): CatalogDomainGroup<T>[] {
  const buckets = new Map<
    CatalogDomainId,
    Map<CatalogDeskId, T[]>
  >();

  for (const item of items) {
    const classified = classifyCatalogSlug(item.slug);
    const domain =
      classified.domain !== "generic"
        ? classified.domain
        : coerceCatalogDomain(item.domain);
    if (filter !== "all" && domain !== filter) continue;
    const desk: CatalogDeskId =
      domain === "stock_broker" ? classified.desk ?? "other" : "other";
    if (!buckets.has(domain)) buckets.set(domain, new Map());
    const desks = buckets.get(domain)!;
    const list = desks.get(desk) ?? [];
    list.push(item);
    desks.set(desk, list);
  }

  return DOMAIN_ORDER.flatMap((domain) => {
    const desks = buckets.get(domain);
    if (!desks || desks.size === 0) return [];
    const showDesks = domain === "stock_broker" && [...desks.keys()].some(
      (desk) => desk !== "other",
    );
    const orderedDesks: CatalogDeskGroup<T>[] = DESK_ORDER.flatMap((desk) => {
      const deskItems = desks.get(desk);
      if (!deskItems?.length) return [];
      return [
        {
          key: `${domain}:${desk}`,
          desk: showDesks ? desk : null,
          label: showDesks
            ? desk === "other"
              ? "Other"
              : DESK_LABELS[desk]
            : null,
          items: deskItems,
        },
      ];
    });
    if (orderedDesks.length === 0) return [];
    return [
      {
        domain,
        label: CATALOG_DOMAIN_LABELS[domain],
        desks: orderedDesks,
      },
    ];
  });
}

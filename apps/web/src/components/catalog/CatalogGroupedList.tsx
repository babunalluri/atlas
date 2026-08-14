import type { ReactNode } from "react";

import {
  groupCatalogItems,
  type CatalogDomainFilter,
  type CatalogGroupable,
} from "@/lib/catalog/domain-groups";

export function CatalogGroupedList<T extends CatalogGroupable>({
  items,
  domainFilter,
  empty,
  filteredEmpty,
  renderItem,
}: {
  items: T[];
  domainFilter: CatalogDomainFilter;
  empty: ReactNode;
  filteredEmpty: ReactNode;
  renderItem: (item: T) => ReactNode;
}) {
  if (items.length === 0) {
    return <>{empty}</>;
  }
  const groups = groupCatalogItems(items, domainFilter);
  if (groups.length === 0) {
    return <>{filteredEmpty}</>;
  }
  return (
    <>
      {groups.map((group) => (
        <li key={group.domain} className="border-b border-line/60 last:border-0">
          <div className="bg-mist/60 px-4 py-1.5">
            <p className="th-label">{group.label}</p>
          </div>
          {group.desks.map((desk) => (
            <div key={desk.key}>
              {desk.label ? (
                <p className="px-4 pt-2 text-[11px] font-medium uppercase tracking-[0.14em] text-slate-muted">
                  {desk.label}
                </p>
              ) : null}
              <ul>
                {desk.items.map((item) => (
                  <li
                    key={item.id}
                    className="border-b border-line/40 last:border-0"
                  >
                    {renderItem(item)}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </li>
      ))}
    </>
  );
}

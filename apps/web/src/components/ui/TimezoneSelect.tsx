"use client";

import { useMemo } from "react";

import { Label } from "@/components/ui/Field";
import { SearchableSelect } from "@/components/ui/SearchableSelect";

/** Curated IANA zones for admin pickers (any valid zone still accepted via API). */
export const COMMON_TIMEZONES = [
  "UTC",
  "Asia/Kolkata",
  "Asia/Dubai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Asia/Shanghai",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Sao_Paulo",
  "America/Argentina/Buenos_Aires",
  "Africa/Cairo",
  "Africa/Johannesburg",
  "Australia/Sydney",
  "Pacific/Auckland",
] as const;

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function TimezoneSelect({
  id,
  label = "Timezone",
  value,
  onChange,
  hint,
}: {
  id: string;
  label?: string;
  value: string;
  onChange: (timezone: string) => void;
  hint?: string;
}) {
  const options = useMemo(() => {
    const zones = new Set<string>(COMMON_TIMEZONES);
    if (value) zones.add(value);
    return [...zones].sort().map((zone) => ({ value: zone, label: zone }));
  }, [value]);

  return (
    <div>
      <Label htmlFor={id} hint={hint}>
        {label}
      </Label>
      <SearchableSelect
        id={id}
        value={value}
        onChange={onChange}
        options={options}
        placeholder="Search timezones…"
        emptyMessage="No matching timezone"
      />
    </div>
  );
}

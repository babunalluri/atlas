"use client";

import { useLocale, useTranslations } from "next-intl";
import { useTransition } from "react";

import { usePathname, useRouter } from "@/i18n/navigation";
import {
  localeLabels,
  locales,
  type AppLocale,
} from "@/i18n/routing";

export function LanguageSwitcher({
  className,
}: {
  className?: string;
}) {
  const t = useTranslations("common");
  const locale = useLocale() as AppLocale;
  const router = useRouter();
  const pathname = usePathname();
  const [pending, startTransition] = useTransition();

  return (
    <label className={className}>
      <span className="sr-only">{t("language")}</span>
      <select
        value={locale}
        disabled={pending}
        aria-label={t("language")}
        onChange={(event) => {
          const next = event.target.value as AppLocale;
          startTransition(() => {
            router.replace(pathname, { locale: next });
          });
        }}
        className="max-w-[9.5rem] truncate rounded-md border border-line bg-raised px-2 py-1.5 text-xs font-medium text-ink outline-none focus:border-teal focus:ring-2 focus:ring-teal/20"
      >
        {locales.map((code) => (
          <option key={code} value={code}>
            {localeLabels[code]}
          </option>
        ))}
      </select>
    </label>
  );
}

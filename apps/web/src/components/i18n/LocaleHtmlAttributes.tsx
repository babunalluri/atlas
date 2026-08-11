"use client";

import { useLocale } from "next-intl";
import { useEffect } from "react";

import { rtlLocales, type AppLocale } from "@/i18n/routing";

/** Sync <html lang/dir> for SSR root layout + client navigations. */
export function LocaleHtmlAttributes() {
  const locale = useLocale() as AppLocale;

  useEffect(() => {
    const root = document.documentElement;
    root.lang = locale === "pt-BR" ? "pt-BR" : locale;
    root.dir = rtlLocales.has(locale) ? "rtl" : "ltr";
    root.dataset.locale = locale;
  }, [locale]);

  return null;
}

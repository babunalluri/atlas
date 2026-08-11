import { defineRouting } from "next-intl/routing";

export const locales = ["en", "hi", "ar", "es", "zh", "fr", "pt-BR"] as const;
export type AppLocale = (typeof locales)[number];
export const defaultLocale: AppLocale = "en";

export const localeLabels: Record<AppLocale, string> = {
  en: "English",
  hi: "हिन्दी",
  ar: "العربية",
  es: "Español",
  zh: "中文",
  fr: "Français",
  "pt-BR": "Português (Brasil)",
};

export const rtlLocales = new Set<AppLocale>(["ar"]);

export const routing = defineRouting({
  locales: [...locales],
  defaultLocale,
  localePrefix: "always",
});

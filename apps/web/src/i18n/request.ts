import { getRequestConfig } from "next-intl/server";

import { mergeMessages } from "@/i18n/merge-messages";
import { defaultLocale, locales, type AppLocale } from "@/i18n/routing";

function resolveLocale(locale: string | undefined): AppLocale {
  if (locale && (locales as readonly string[]).includes(locale)) {
    return locale as AppLocale;
  }
  return defaultLocale;
}

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = resolveLocale(requested);
  const userMessages = (await import(`../../messages/${locale}.json`)).default;
  const enMessages =
    locale === "en"
      ? userMessages
      : (await import(`../../messages/en.json`)).default;

  return {
    locale,
    messages:
      locale === "en"
        ? userMessages
        : mergeMessages(enMessages, userMessages),
  };
});

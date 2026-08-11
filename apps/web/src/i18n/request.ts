import { getRequestConfig } from "next-intl/server";

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
    messages: {
      ...enMessages,
      ...userMessages,
      common: { ...enMessages.common, ...userMessages.common },
      auth: { ...enMessages.auth, ...userMessages.auth },
      nav: {
        ...enMessages.nav,
        ...userMessages.nav,
        groups: { ...enMessages.nav.groups, ...userMessages.nav?.groups },
        items: { ...enMessages.nav.items, ...userMessages.nav?.items },
      },
      home: { ...enMessages.home, ...userMessages.home },
    },
  };
});

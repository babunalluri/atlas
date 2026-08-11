import type { Metadata } from "next";
import { SessionProvider } from "next-auth/react";
import { getLocale } from "next-intl/server";
import {
  IBM_Plex_Mono,
  IBM_Plex_Sans,
  Noto_Sans,
  Noto_Sans_Arabic,
  Noto_Sans_Devanagari,
  Noto_Sans_SC,
  Syne,
} from "next/font/google";

import { rtlLocales, type AppLocale } from "@/i18n/routing";

import "./globals.css";

const syne = Syne({
  subsets: ["latin"],
  variable: "--font-display",
  display: "swap",
});

const plexSans = IBM_Plex_Sans({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap",
});

const noto = Noto_Sans({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-noto",
  display: "swap",
});

const notoDeva = Noto_Sans_Devanagari({
  subsets: ["devanagari"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-deva",
  display: "swap",
});

const notoArabic = Noto_Sans_Arabic({
  subsets: ["arabic"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-arabic",
  display: "swap",
});

const notoSc = Noto_Sans_SC({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-sc",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Atlas Agents",
  description: "Multi-tenant agent configuration and branded customer chat",
};

/**
 * Do not call `auth()` here — that would force every public/embed route dynamic.
 * SessionProvider fetches the session on the client; middleware still protects admin.
 */
export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const locale = (await getLocale()) as AppLocale;
  const lang = locale === "pt-BR" ? "pt-BR" : locale;
  const dir = rtlLocales.has(locale) ? "rtl" : "ltr";

  return (
    <html lang={lang} dir={dir} suppressHydrationWarning>
      <body
        className={`${syne.variable} ${plexSans.variable} ${plexMono.variable} ${noto.variable} ${notoDeva.variable} ${notoArabic.variable} ${notoSc.variable} font-sans`}
      >
        <SessionProvider refetchInterval={4 * 60} refetchOnWindowFocus>
          {children}
        </SessionProvider>
      </body>
    </html>
  );
}

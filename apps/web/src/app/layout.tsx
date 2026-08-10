import type { Metadata } from "next";
import { SessionProvider } from "next-auth/react";
import { IBM_Plex_Mono, IBM_Plex_Sans, Syne } from "next/font/google";

import "./globals.css";

const syne = Syne({
  subsets: ["latin"],
  variable: "--font-display",
  display: "swap",
});

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
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

export const metadata: Metadata = {
  title: "Atlas Agents",
  description: "Multi-tenant agent configuration and branded customer chat",
};

/**
 * Do not call `auth()` here — that would force every public/embed route dynamic.
 * SessionProvider fetches the session on the client; middleware still protects admin.
 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${syne.variable} ${plexSans.variable} ${plexMono.variable} font-sans`}
      >
        <SessionProvider refetchInterval={4 * 60} refetchOnWindowFocus>
          {children}
        </SessionProvider>
      </body>
    </html>
  );
}

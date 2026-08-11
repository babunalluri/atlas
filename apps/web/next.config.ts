import path from "node:path";
import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

import { locales } from "./src/i18n/routing";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const localeSegment = `(${locales.join("|")})`;

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../.."),
  async redirects() {
    const legacyAdminRedirects = [
      { from: "activities", to: "traces" },
      { from: "sessions", to: "traces" },
    ] as const;

    return legacyAdminRedirects.flatMap(({ from, to }) => [
      {
        source: `/admin/${from}`,
        destination: `/admin/${to}`,
        permanent: false,
      },
      {
        source: `/admin/${from}/:id`,
        destination: `/admin/${to}/:id`,
        permanent: false,
      },
      {
        source: `/admin/${from}/:path*`,
        destination: `/admin/${to}`,
        permanent: false,
      },
      {
        source: `/:locale${localeSegment}/admin/${from}`,
        destination: `/:locale/admin/${to}`,
        permanent: false,
      },
      {
        source: `/:locale${localeSegment}/admin/${from}/:id`,
        destination: `/:locale/admin/${to}/:id`,
        permanent: false,
      },
      {
        source: `/:locale${localeSegment}/admin/${from}/:path*`,
        destination: `/:locale/admin/${to}`,
        permanent: false,
      },
    ]);
  },
};

export default withNextIntl(nextConfig);

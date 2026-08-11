import path from "node:path";
import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../.."),
  async redirects() {
    return [
      {
        source: "/:locale/admin/activities",
        destination: "/:locale/admin/traces",
        permanent: false,
      },
      {
        source: "/:locale/admin/activities/:id",
        destination: "/:locale/admin/traces/:id",
        permanent: false,
      },
      {
        source: "/:locale/admin/activities/:path*",
        destination: "/:locale/admin/traces",
        permanent: false,
      },
      {
        source: "/:locale/admin/sessions",
        destination: "/:locale/admin/traces",
        permanent: false,
      },
      {
        source: "/:locale/admin/sessions/:id",
        destination: "/:locale/admin/traces/:id",
        permanent: false,
      },
      {
        source: "/:locale/admin/sessions/:path*",
        destination: "/:locale/admin/traces",
        permanent: false,
      },
    ];
  },
};

export default withNextIntl(nextConfig);

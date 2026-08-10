import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../.."),
  async redirects() {
    return [
      {
        source: "/admin/activities",
        destination: "/admin/traces",
        permanent: false,
      },
      {
        source: "/admin/activities/:id",
        destination: "/admin/traces/:id",
        permanent: false,
      },
      {
        source: "/admin/activities/:path*",
        destination: "/admin/traces",
        permanent: false,
      },
      {
        source: "/admin/sessions",
        destination: "/admin/traces",
        permanent: false,
      },
      {
        source: "/admin/sessions/:id",
        destination: "/admin/traces/:id",
        permanent: false,
      },
      {
        source: "/admin/sessions/:path*",
        destination: "/admin/traces",
        permanent: false,
      },
    ];
  },
};

export default nextConfig;

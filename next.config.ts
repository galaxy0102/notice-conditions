import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  serverExternalPackages: ["cfb", "iconv-lite", "@electric-sql/pglite"],
  outputFileTracingIncludes: {
    "/api/boundaries": ["./data/boundaries/**/*"],
    "/api/legal-dong": ["./data/legal-dong.json"],
    "/api/buildings": ["./data/legal-dong.json"],
  },
  webpack: (config) => {
    config.resolve.extensionAlias = {
      ".js": [".ts", ".tsx", ".js"],
    };
    return config;
  },
};

export default nextConfig;

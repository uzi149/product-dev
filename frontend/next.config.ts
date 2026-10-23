import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The e2e tests set this so their dev server doesn't clash with `npm run dev`.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  /* config options here */
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

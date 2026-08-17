import type { NextConfig } from "next";

/**
 * The Next.js app lives at the repository root; the Python backend sits in
 * `backend/`.
 *
 * Legacy local API screens may use same-origin `/api/...` calls, which Next
 * rewrites to FastAPI during local development. The deployed Vercel website
 * uses Supabase as its control plane and deliberately installs no implicit
 * localhost rewrite.
 *
 * This rewrite is compatibility support for the legacy local API screens and
 * tests. The Supabase-native application routes do not depend on it.
 *
 * `API_PROXY_TARGET` is compiled into the routes manifest, so it is a
 * **build-time** value: the Docker image takes it as a build argument
 * (`http://backend:8000` under Compose). Changing it means rebuilding the image.
 *
 * `NEXT_PUBLIC_API_URL` remains supported only by the unused legacy API client;
 * do not set it for the Vercel deployment.
 */
const IS_VERCEL = process.env.VERCEL === "1";
const API_PROXY_TARGET =
  process.env.API_PROXY_TARGET?.trim() || (IS_VERCEL ? "" : "http://127.0.0.1:8000");

const nextConfig: NextConfig = {
  reactStrictMode: true,

  // Docker uses Next's self-contained server bundle. Vercel builds the app
  // into its own deployment format, so standalone output is unnecessary there
  // and conflicts with Vercel's post-build trace processing.
  ...(IS_VERCEL ? {} : { output: "standalone" as const }),

  // The Python backend and its virtualenv are not part of the web build.
  outputFileTracingExcludes: {
    "*": ["./backend/**", "./.venv/**"],
  },

  async rewrites() {
    if (!API_PROXY_TARGET) return [];
    return [
      {
        source: "/api/:path*",
        destination: `${API_PROXY_TARGET}/api/:path*`,
      },
    ];
  },

  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;

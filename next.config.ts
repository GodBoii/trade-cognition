import type { NextConfig } from "next";

/**
 * The Next.js app lives at the repository root; the Python backend sits in
 * `backend/`.
 *
 * API access is same-origin by default: the browser calls `/api/...`, and Next
 * rewrites that to the backend. Two reasons to prefer this over calling the
 * backend directly from the browser:
 *
 * - no CORS configuration and no credentials crossing origins;
 * - the backend host stays a server-side detail, so it can change (localhost in
 *   development, a service name in Docker, a private address in production)
 *   without rebuilding the bundle.
 *
 * `API_PROXY_TARGET` is compiled into the routes manifest, so it is a
 * **build-time** value: the Docker image takes it as a build argument
 * (`http://backend:8000` under Compose). Changing it means rebuilding the image.
 *
 * If you would rather have the browser talk to the backend directly - a static
 * host with the API elsewhere - set `NEXT_PUBLIC_API_URL` instead. It is also
 * build-time, and takes precedence in the client (see `lib/api/client.ts`).
 *
 * Note on WebSockets: Next's rewrites do not reliably proxy a WebSocket upgrade.
 * The live feed therefore falls back to REST polling when the socket cannot be
 * established (see `state/useStream.ts`). Point `NEXT_PUBLIC_API_URL` at the
 * backend directly if you want the socket.
 */
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";
const IS_VERCEL = process.env.VERCEL === "1";

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

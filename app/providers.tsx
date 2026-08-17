"use client";

import type { ReactNode } from "react";

import { AuthProvider } from "@/state/auth";

/**
 * Client boundary for the whole app.
 *
 * Every screen here is authenticated with a bearer token held in the browser and
 * driven by live broker data, so there is nothing to render on the server ahead
 * of time. Keeping one boundary at the top makes that explicit rather than
 * scattering `"use client"` through the tree.
 */
export function Providers({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}

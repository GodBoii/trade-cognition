import type { Metadata } from "next";

import DashboardView from "@/components/views/DashboardView";

export const metadata: Metadata = { title: "Dashboard · Trade Cognition" };

export default function DashboardPage() {
  return <DashboardView />;
}

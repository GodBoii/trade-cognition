import type { Metadata } from "next";

import TradesView from "@/components/views/TradesView";

export const metadata: Metadata = { title: "Trades · Trade Cognition" };

export default function TradesPage() {
  return <TradesView />;
}

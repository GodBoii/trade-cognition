import type { Metadata } from "next";

import TradeTicketView from "@/components/views/TradeTicketView";

export const metadata: Metadata = { title: "New trade · Trade Cognition" };

export default function TradePage() {
  return <TradeTicketView />;
}

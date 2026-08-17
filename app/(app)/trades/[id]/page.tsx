import type { Metadata } from "next";
import { notFound } from "next/navigation";

import TradeDetailView from "@/components/views/TradeDetailView";

export const metadata: Metadata = { title: "Trade · Trade Cognition" };

/**
 * In Next 15+ `params` is a promise, so the route awaits it and hands the view a
 * plain number. Anything that is not a positive integer is a 404 rather than a
 * request the API would reject.
 */
export default async function TradeDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const tradeId = Number(id);

  if (!Number.isInteger(tradeId) || tradeId <= 0) notFound();

  return <TradeDetailView tradeId={tradeId} />;
}

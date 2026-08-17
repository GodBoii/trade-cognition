import type { Metadata } from "next";
import TradeDetailView from "@/components/views/TradeDetailView";

export const metadata: Metadata = { title: "Trade · Trade Cognition" };

/**
 * In Next 15+ `params` is a promise, so the route awaits it and hands the view a
 * plain UUID. Supabase/RLS decides whether the signed-in user owns it.
 */
export default async function TradeDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <TradeDetailView tradeId={id} />;
}

import type { Metadata } from "next";

import RulesView from "@/components/views/RulesView";

export const metadata: Metadata = { title: "Rules · Trade Cognition" };

export default function RulesPage() {
  return <RulesView />;
}

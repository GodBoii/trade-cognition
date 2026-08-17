import type { Metadata } from "next";

import JournalView from "@/components/views/JournalView";

export const metadata: Metadata = { title: "Journal · Trade Cognition" };

export default function JournalPage() {
  return <JournalView />;
}

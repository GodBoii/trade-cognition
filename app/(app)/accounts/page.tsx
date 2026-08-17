import type { Metadata } from "next";

import ConnectAccountView from "@/components/views/ConnectAccountView";

export const metadata: Metadata = { title: "Accounts · Trade Cognition" };

export default function AccountsPage() {
  return <ConnectAccountView />;
}

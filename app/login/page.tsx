import type { Metadata } from "next";

import SignInView from "@/components/views/SignInView";

export const metadata: Metadata = { title: "Sign in · Trade Cognition" };

export default function LoginPage() {
  return <SignInView />;
}

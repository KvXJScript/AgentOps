import type { Metadata } from "next";
import { ApprovalsPage } from "@/components/approvals/approvals-page";

export const metadata: Metadata = { title: "Approvals" };

export default function Approvals() {
  return <ApprovalsPage />;
}

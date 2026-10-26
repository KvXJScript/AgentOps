import type { Metadata } from "next";
import { ReportsPage } from "@/components/reports/reports-page";

export const metadata: Metadata = { title: "Reports" };

export default function Reports() {
  return <ReportsPage />;
}

import type { Metadata } from "next";
import { TrackerPage } from "@/components/tracker/tracker-page";

export const metadata: Metadata = { title: "Tracker" };

export default function Tracker() {
  return <TrackerPage />;
}

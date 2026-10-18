import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Research Team",
  description:
    "A multi-agent research assistant: a Supervisor delegates to a Researcher, a Writer, a Reviewer and a Fact-checker, and you watch them work live.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Mutual Fund Facts | FAQ Assistant",
  description:
    "A facts-only FAQ assistant for HDFC Mutual Fund scheme information.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

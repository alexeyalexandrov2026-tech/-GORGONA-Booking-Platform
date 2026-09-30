import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Book your appointment",
  description: "Choose your service, artist and time.",
  robots: { index: false, follow: false },
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

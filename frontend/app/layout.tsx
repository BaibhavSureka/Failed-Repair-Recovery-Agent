import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Relay | Repair Recovery",
  description: "A repair recovery control room powered by a controlled backend workflow.",
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}

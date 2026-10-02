import type { Metadata } from "next";
import { Inter } from "next/font/google";
import ChatWidget from "./components/ChatWidget";
import Header from "./components/Header";
import { getProducts, section } from "@/lib/catalog";
import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Stylist",
  description: "Personal styling prototype",
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const sections = [...new Set((await getProducts()).map(section))].sort();
  return (
    <html lang="en" className={`${inter.variable} antialiased`}>
      <body className="flex min-h-screen flex-col">
        <Header sections={sections} />
        <main className="flex-1 pt-header">{children}</main>
        {/* bottom padding keeps the text clear of the chat launcher */}
        <footer className="border-t border-border px-4 pt-6 pb-20 text-xs text-text-secondary lg:px-5">
          Unofficial prototype, not affiliated with or endorsed by Suitsupply. Product data and images belong to
          their owner and link back to suitsupply.com.
        </footer>
        {/* In the layout so the conversation survives navigating between pages */}
        <ChatWidget />
      </body>
    </html>
  );
}

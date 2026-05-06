import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Parallax - AMD Parallel AI Analysis",
  description: "Two minds, one codebase. Parallel AI code intelligence on AMD MI300X.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.className} bg-black text-gray-200 min-h-screen`}>
        <header className="border-b border-gray-800 bg-[#0d0d12] py-4 px-8 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-purple-500">Parallax</h1>
            <span className="px-2 py-1 bg-gray-800 text-xs rounded border border-gray-700">AMD MI300X Demo</span>
          </div>
        </header>
        <main className="p-8">
          {children}
        </main>
      </body>
    </html>
  );
}

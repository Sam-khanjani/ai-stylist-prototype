import Link from "next/link";
import { requireAdmin } from "@/lib/admin";

export const metadata = { title: "Admin · Stylist", robots: { index: false } };

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  await requireAdmin();
  return (
    <div className="flex min-h-screen flex-col bg-gray-200">
      <header className="sticky top-0 z-20 flex h-header items-center gap-8 bg-gray-900 px-4 text-white lg:px-6">
        <Link href="/admin" className="text-lg font-medium tracking-[0.2em] uppercase">
          Stylist <span className="ml-1 rounded bg-white/15 px-1.5 py-0.5 text-[10px] font-medium tracking-normal normal-case">admin</span>
        </Link>
        <span className="flex-1" />
        <a href="/" target="_blank" className="hidden text-xs text-white/60 hover:text-white sm:block">
          Open site ↗
        </a>
        <form method="post" action="/api/admin/logout">
          <button className="text-xs text-white/60 hover:text-white">Sign out</button>
        </form>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 lg:px-6">{children}</main>
    </div>
  );
}

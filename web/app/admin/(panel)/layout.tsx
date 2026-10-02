import Link from "next/link";
import { requireAdmin } from "@/lib/admin";

export const metadata = { title: "Admin · Stylist", robots: { index: false } };

const TABS = [
  ["/admin", "Overview"],
  ["/admin/conversations", "Conversations"],
  ["/admin/gaps", "Gaps & 👎"],
  ["/admin/evals", "Evals"],
  ["/admin/status", "Status"],
];

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  await requireAdmin();
  return (
    <>
      <header className="flex h-header items-center gap-6 border-b border-border px-4 lg:px-5">
        <Link href="/admin" className="text-lg font-medium tracking-[0.2em] uppercase">
          Stylist <span className="text-xs tracking-normal text-text-secondary normal-case">admin</span>
        </Link>
        <nav className="flex flex-1 gap-5 overflow-x-auto text-sm whitespace-nowrap">
          {TABS.map(([href, label]) => (
            <Link key={href} href={href} className="hover:text-text-secondary">
              {label}
            </Link>
          ))}
        </nav>
        <form method="post" action="/api/admin/logout">
          <button className="text-xs text-text-secondary hover:text-text">Sign out</button>
        </form>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>
    </>
  );
}

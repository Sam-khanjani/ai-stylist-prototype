import Link from "next/link";

export default function Header({ sections }: { sections: string[] }) {
  return (
    <header className="fixed inset-x-0 top-0 z-10 flex h-header items-center gap-6 border-b border-border bg-background px-4 lg:px-5">
      <Link href="/" className="shrink-0 text-lg font-medium tracking-[0.2em] uppercase">
        Stylist
      </Link>
      <nav className="flex flex-1 gap-5 overflow-x-auto text-sm whitespace-nowrap [scrollbar-width:none] lg:justify-center-safe">
        {sections.map((s) => (
          <Link key={s} href={`/?section=${s}`} className="capitalize hover:text-text-secondary">
            {s}
          </Link>
        ))}
      </nav>
      <span className="hidden shrink-0 text-xs text-text-secondary md:block">Unofficial demo</span>
    </header>
  );
}

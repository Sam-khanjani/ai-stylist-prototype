import type { ReactNode } from "react";

export function Card({ title, action, children, className = "" }: { title?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-gray-400/70 bg-white p-5 shadow-sm ${className}`}>
      {(title || action) && (
        <div className="mb-4 flex items-baseline justify-between gap-4">
          {title && <h2 className="text-sm font-medium">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export type Tone = "good" | "warning" | "critical";
const TONE: Record<Tone, { icon: string; className: string }> = {
  good: { icon: "✓", className: "bg-good/10 text-good" },
  warning: { icon: "!", className: "bg-warning/15 text-gray-800" },
  critical: { icon: "✕", className: "bg-critical/10 text-critical" },
};

// Status is never color alone: always an icon and a word
export function StatusBadge({ tone, label }: { tone: Tone; label: string }) {
  const t = TONE[tone];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${t.className}`}>
      <span aria-hidden>{t.icon}</span>
      {label}
    </span>
  );
}

export function Stat({ label, value, note, status }: { label: string; value: string; note?: string; status?: ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-400/70 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs text-text-secondary">{label}</p>
        {status}
      </div>
      <p className="mt-2 text-2xl font-medium tracking-heading">{value}</p>
      {note && <p className="mt-1 truncate text-xs text-text-secondary">{note}</p>}
    </div>
  );
}

export const ROUTES = [
  { key: "policy", label: "Policy", className: "bg-route-policy" },
  { key: "product", label: "Product", className: "bg-route-product" },
  { key: "other", label: "Other", className: "bg-route-other" },
] as const;

export function RouteBadge({ route }: { route: string | null }) {
  const r = ROUTES.find((x) => x.key === route);
  if (!r) return null;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-gray-400/70 px-2 py-0.5 text-xs">
      <span className={`size-2 rounded-full ${r.className}`} aria-hidden />
      {r.label}
    </span>
  );
}

export function Legend({ items }: { items: readonly { label: string; className: string }[] }) {
  return (
    <div className="flex gap-4 text-xs text-text-secondary">
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5">
          <span className={`size-2.5 rounded-sm ${i.className}`} aria-hidden />
          {i.label}
        </span>
      ))}
    </div>
  );
}

export const pct = (part: number, whole: number) => (whole ? Math.round((100 * part) / whole) : null);
export const seconds = (ms: number | null | undefined) => (ms == null ? "–" : `${(ms / 1000).toFixed(1)} s`);
export const usd = (v: number | null | undefined) => (v == null ? "–" : `$${v.toFixed(v < 1 ? 4 : 2)}`);

export function ago(iso: string) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} d ago`;
}

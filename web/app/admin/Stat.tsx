export default function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-md border border-border p-4">
      <p className="text-xs text-text-secondary">{label}</p>
      <p className="mt-1 text-2xl font-medium tracking-heading">{value}</p>
      {note && <p className="mt-1 text-xs text-text-secondary">{note}</p>}
    </div>
  );
}

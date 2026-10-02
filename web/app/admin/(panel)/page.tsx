import { adminApi } from "@/lib/admin";

type Day = {
  day: string;
  questions: number;
  policy: number;
  product: number;
  other: number;
  fallbacks: number;
  up: number;
  down: number;
  avg_latency_ms: number | null;
};

const DAYS = 30;
const pct = (part: number, whole: number) => (whole ? `${Math.round((100 * part) / whole)}%` : "–");

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-md border border-border p-4">
      <p className="text-xs text-text-secondary">{label}</p>
      <p className="mt-1 text-2xl font-medium tracking-heading">{value}</p>
      {note && <p className="mt-1 text-xs text-text-secondary">{note}</p>}
    </div>
  );
}

export default async function Overview() {
  const rows = await adminApi<Day[]>(`/overview?days=${DAYS}`);

  // Continuous time axis: days without questions show as empty, not skipped
  const byDay = new Map(rows.map((r) => [r.day, r]));
  const days = Array.from({ length: DAYS }, (_, i) => {
    const day = new Date(Date.now() - (DAYS - 1 - i) * 86_400_000).toISOString().slice(0, 10);
    return byDay.get(day) ?? { day, questions: 0, policy: 0, product: 0, other: 0, fallbacks: 0, up: 0, down: 0, avg_latency_ms: null };
  });

  const sum = (k: keyof Day) => rows.reduce((s, r) => s + Number(r[k] ?? 0), 0);
  const total = sum("questions");
  const votes = sum("up") + sum("down");
  const latency = total ? Math.round(rows.reduce((s, r) => s + (r.avg_latency_ms ?? 0) * r.questions, 0) / total) : null;
  const max = Math.max(1, ...days.map((d) => d.questions));

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-medium tracking-heading">Overview · last {DAYS} days</h1>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <Stat label="Questions" value={String(total)} />
        <Stat label="Fallback rate" value={pct(sum("fallbacks"), total)} note="sent to contact options" />
        <Stat label="Helpful votes" value={pct(sum("up"), votes)} note={`${votes} votes`} />
        <Stat label="Avg response time" value={latency ? `${(latency / 1000).toFixed(1)} s` : "–"} note="until answer complete" />
        <Stat
          label="Route mix"
          value={pct(sum("policy"), total)}
          note={`policy · product ${pct(sum("product"), total)} · other ${pct(sum("other"), total)}`}
        />
      </div>

      <section>
        <h2 className="text-sm font-medium">Questions per day</h2>
        <div className="mt-4 flex h-48 items-end gap-0.5 border-b border-border" role="img" aria-label="Questions per day, last 30 days">
          {days.map((d) => (
            <div key={d.day} className="group relative flex h-full flex-1 items-end">
              {/* Bar, with a hover area as tall as the chart so thin bars are easy to hit */}
              <div
                className="w-full rounded-t bg-gray-800 group-hover:bg-gray-600"
                style={{ height: `${(100 * d.questions) / max}%`, minHeight: d.questions ? 2 : 0 }}
              />
              <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 hidden w-44 -translate-x-1/2 rounded-md border border-border bg-background p-2 text-xs shadow-md group-hover:block">
                <p className="font-medium">{d.day}</p>
                <p>{d.questions} questions</p>
                <p className="text-text-secondary">
                  {d.fallbacks} fallbacks · 👍 {d.up} · 👎 {d.down}
                </p>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-1 flex justify-between text-xs text-text-secondary">
          <span>{days[0].day}</span>
          <span>max {max} / day</span>
          <span>{days[days.length - 1].day}</span>
        </div>
      </section>

      <details>
        <summary className="cursor-pointer text-sm text-text-secondary">Show as table</summary>
        <table className="mt-3 w-full text-left text-sm">
          <thead className="text-xs text-text-secondary">
            <tr>
              {["Day", "Questions", "Policy", "Product", "Other", "Fallbacks", "👍", "👎", "Avg latency"].map((h) => (
                <th key={h} className="py-2 pr-4 font-normal">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.day} className="border-t border-border">
                <td className="py-2 pr-4">{r.day}</td>
                <td className="pr-4">{r.questions}</td>
                <td className="pr-4">{r.policy}</td>
                <td className="pr-4">{r.product}</td>
                <td className="pr-4">{r.other}</td>
                <td className="pr-4">{r.fallbacks}</td>
                <td className="pr-4">{r.up}</td>
                <td className="pr-4">{r.down}</td>
                <td>{r.avg_latency_ms ? `${(r.avg_latency_ms / 1000).toFixed(1)} s` : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      <p className="text-xs text-text-secondary">
        Counts are anonymous (no text, no visitor id) and kept for a year; the chats themselves are deleted with the
        chat retention period. Detailed traces and costs are in Langfuse.
      </p>
    </div>
  );
}

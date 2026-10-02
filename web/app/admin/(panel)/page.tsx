import { adminApi } from "@/lib/admin";
import BarChart, { lastDays } from "../BarChart";
import Stat from "../Stat";

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

export default async function Overview() {
  const rows = await adminApi<Day[]>(`/overview?days=${DAYS}`);

  const byDay = new Map(rows.map((r) => [r.day, r]));
  const bars = lastDays(DAYS).map((day) => {
    const d = byDay.get(day);
    return {
      key: day,
      value: d?.questions ?? 0,
      tooltip: [day, `${d?.questions ?? 0} questions`, `${d?.fallbacks ?? 0} fallbacks · 👍 ${d?.up ?? 0} · 👎 ${d?.down ?? 0}`],
    };
  });

  const sum = (k: keyof Day) => rows.reduce((s, r) => s + Number(r[k] ?? 0), 0);
  const total = sum("questions");
  const votes = sum("up") + sum("down");
  const latency = total ? Math.round(rows.reduce((s, r) => s + (r.avg_latency_ms ?? 0) * r.questions, 0) / total) : null;

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
        <BarChart bars={bars} label={`Questions per day, last ${DAYS} days`} />
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

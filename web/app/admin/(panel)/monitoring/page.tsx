import Link from "next/link";
import { adminApi } from "@/lib/admin";
import BarChart, { lastDays } from "../../BarChart";
import Stat from "../../Stat";

type Row = Record<string, string | number | null>;
type Section = Row[] | { error: string };
type Trace = { timestamp: string; latency: number | null; cost: number | null; session_id: string | null; url: string };
type Data =
  | { enabled: false }
  | {
      enabled: true;
      days: number;
      usage: Section;
      daily: Section;
      models: Section;
      end_to_end: Section;
      steps: Section;
      feedback: Section;
      errors: Section;
      traces: Trace[] | { error: string };
    };

const PERIODS = [1, 7, 30];
const num = (v: unknown) => Number(v ?? 0);
const usd = (v: unknown) => `$${num(v).toFixed(num(v) < 1 ? 4 : 2)}`;
const ms = (v: unknown) => (v == null ? "–" : `${(num(v) / 1000).toFixed(2)} s`);
const rowsOf = (s: Section) => (Array.isArray(s) ? s : []);

function Failed({ section }: { section: Section | Trace[] | { error: string } }) {
  return !Array.isArray(section) ? <p className="mt-2 text-xs text-red-700">Could not load from Langfuse: {section.error}</p> : null;
}

export default async function Monitoring({ searchParams }: PageProps<"/admin/monitoring">) {
  const days = PERIODS.includes(Number((await searchParams).days)) ? Number((await searchParams).days) : 7;
  const data = await adminApi<Data>(`/langfuse?days=${days}`);

  if (!data.enabled) {
    return <p className="text-sm text-text-secondary">Langfuse tracing is off for this api (no keys configured).</p>;
  }

  const usage = rowsOf(data.usage)[0] ?? {};
  const e2e = rowsOf(data.end_to_end)[0] ?? {};
  const feedback = rowsOf(data.feedback)[0] ?? {};
  const errors = rowsOf(data.errors);
  const calls = num(usage.count_count);
  const answers = num(e2e.count_count);
  const daily = new Map(rowsOf(data.daily).map((r) => [String(r.time_dimension).slice(0, 10), r]));
  const bars = lastDays(days).map((day) => {
    const r = daily.get(day);
    return {
      key: day,
      value: Number(num(r?.sum_totalCost).toFixed(4)),
      tooltip: [day, `${usd(r?.sum_totalCost)} cost`, `${num(r?.count_count)} LLM calls`, `${num(r?.sum_inputTokens).toLocaleString()} in · ${num(r?.sum_outputTokens).toLocaleString()} out tokens`],
    };
  });

  return (
    <div className="space-y-10">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-medium tracking-heading">Monitoring</h1>
        <nav className="flex gap-3 text-sm">
          {PERIODS.map((p) => (
            <Link key={p} href={`/admin/monitoring?days=${p}`} className={p === days ? "font-medium underline" : "text-text-secondary hover:text-text"}>
              {p === 1 ? "24 hours" : `${p} days`}
            </Link>
          ))}
        </nav>
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
        <Stat label="LLM cost" value={usd(usage.sum_totalCost)} note={answers ? `${usd(num(usage.sum_totalCost) / answers)} per answer` : undefined} />
        <Stat label="Tokens" value={(num(usage.sum_inputTokens) + num(usage.sum_outputTokens)).toLocaleString()} note={`${num(usage.sum_inputTokens).toLocaleString()} in · ${num(usage.sum_outputTokens).toLocaleString()} out`} />
        <Stat label="Answers" value={String(answers)} note={`${calls} LLM calls`} />
        <Stat label="Latency p50 / p95" value={`${ms(e2e.p50_latency)}`} note={`p95 ${ms(e2e.p95_latency)} · p99 ${ms(e2e.p99_latency)}`} />
        <Stat label="Helpful votes" value={num(feedback.count_count) ? `${Math.round(100 * num(feedback.avg_value))}%` : "–"} note={`${num(feedback.count_count)} votes`} />
        {/* a failed query must not look like "0 errors" */}
        <Stat
          label="Errors"
          value={Array.isArray(data.errors) ? String(errors.reduce((s, r) => s + num(r.count_count), 0)) : "–"}
          note={Array.isArray(data.errors) ? "observations with level ERROR" : "could not load"}
        />
      </div>
      <Failed section={data.usage} />
      <Failed section={data.errors} />

      <section>
        <h2 className="text-sm font-medium">LLM cost per day (USD)</h2>
        <BarChart bars={bars} label={`LLM cost per day, last ${days} days`} />
        {num(usage.sum_totalCost) === 0 && calls > 0 && (
          <p className="mt-2 text-xs text-text-secondary">
            Cost shows 0 while calls exist: add the Gemini models with their prices in Langfuse → Settings → Models.
          </p>
        )}
      </section>

      <div className="grid gap-10 md:grid-cols-2">
        <section>
          <h2 className="text-sm font-medium">By model</h2>
          <table className="mt-3 w-full text-left text-sm">
            <thead className="text-xs text-text-secondary">
              <tr>
                {["Model", "Calls", "Tokens", "Cost", "p95"].map((h) => (
                  <th key={h} className="py-2 pr-4 font-normal">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rowsOf(data.models).map((r) => (
                <tr key={String(r.providedModelName)} className="border-t border-border">
                  <td className="py-2 pr-4">{r.providedModelName ?? "unknown"}</td>
                  <td className="pr-4">{num(r.count_count)}</td>
                  <td className="pr-4">{(num(r.sum_inputTokens) + num(r.sum_outputTokens)).toLocaleString()}</td>
                  <td className="pr-4">{usd(r.sum_totalCost)}</td>
                  <td>{ms(r.p95_latency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Failed section={data.models} />
        </section>

        <section>
          <h2 className="text-sm font-medium">Latency per agent step</h2>
          <table className="mt-3 w-full text-left text-sm">
            <thead className="text-xs text-text-secondary">
              <tr>
                {["Step", "Runs", "p50", "p95"].map((h) => (
                  <th key={h} className="py-2 pr-4 font-normal">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rowsOf(data.steps)
                .sort((a, b) => num(b.p95_latency) - num(a.p95_latency))
                .map((r) => (
                  <tr key={String(r.name)} className="border-t border-border">
                    <td className="py-2 pr-4">{r.name}</td>
                    <td className="pr-4">{num(r.count_count)}</td>
                    <td className="pr-4">{ms(r.p50_latency)}</td>
                    <td>{ms(r.p95_latency)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
          <Failed section={data.steps} />
        </section>
      </div>

      {errors.length > 0 && (
        <section>
          <h2 className="text-sm font-medium">Errors by step</h2>
          <ul className="mt-2 text-sm">
            {errors.map((r) => (
              <li key={String(r.name)}>
                {r.name}: {num(r.count_count)}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2 className="text-sm font-medium">Latest traces</h2>
        <Failed section={data.traces} />
        <table className="mt-3 w-full text-left text-sm">
          <thead className="text-xs text-text-secondary">
            <tr>
              {["Time", "Latency", "Cost", "Session", ""].map((h) => (
                <th key={h} className="py-2 pr-4 font-normal">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(Array.isArray(data.traces) ? data.traces : []).map((t) => (
              <tr key={t.url} className="border-t border-border">
                <td className="py-2 pr-4 whitespace-nowrap">{new Date(t.timestamp).toLocaleString()}</td>
                <td className="pr-4">{t.latency == null ? "–" : `${t.latency.toFixed(2)} s`}</td>
                <td className="pr-4">{t.cost == null ? "–" : usd(t.cost)}</td>
                <td className="pr-4 font-mono text-xs">{t.session_id?.slice(0, 8) ?? "–"}</td>
                <td>
                  <a href={t.url} target="_blank" rel="noopener noreferrer" className="text-xs underline">
                    Open in Langfuse
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <p className="text-xs text-text-secondary">
        From Langfuse (eval runs excluded). Costs are Langfuse estimates in USD; the bill is in GCP Billing.
      </p>
    </div>
  );
}

import Link from "next/link";
import { Suspense } from "react";
import { adminApi } from "@/lib/admin";
import BarChart, { lastDays } from "../BarChart";
import { Card, Legend, ROUTES, RouteBadge, Stat, StatusBadge, ago, pct, seconds, usd, type Tone } from "../ui";

type Day = { day: string; questions: number; policy: number; product: number; other: number; fallbacks: number; up: number; down: number; avg_latency_ms: number | null };
type Row = Record<string, string | number | null>;
type Section = Row[] | { error: string };
type Langfuse = { enabled: false } | { enabled: true; usage: Section; daily: Section; models: Section; end_to_end: Section; steps: Section; errors: Section };
type Run = {
  run: string;
  created_at: string;
  summary: { questions: number; answer_accuracy: number; errors?: number; latency_avg: number; metrics: Record<string, number | null> };
  failures: { id: string; question: string; checks: string }[];
  gate_passed: boolean | null;
  baseline: string | null;
  regressions: { id: string; question: string }[] | null;
  fixed: { id: string; question: string }[] | null;
  saved_as_baseline: boolean | null;
};
type Gap = { created_at: string; question: string; answer: string; fallback: boolean; vote: 0 | 1 | null };
type Request = {
  created_at: string;
  route: string | null;
  fallback: boolean | null;
  latency_ms: number | null;
  vote: 0 | 1 | null;
  tokens_in?: number;
  tokens_out?: number;
  cost?: number;
  llm_calls?: number;
  models?: string[];
};
type Status = { chunks: number; products: number; conversations: number; messages: number; events: number; retention_days: number; chat_model: string; embedding_model: string; tracing: boolean; revision: string };

const PERIODS = [1, 7, 30];
const n = (v: unknown) => Number(v ?? 0);
const rowsOf = (s: Section | undefined) => (Array.isArray(s) ? s : []);
const tone = (value: number | null, good: (v: number) => boolean, ok: (v: number) => boolean): Tone | null =>
  value == null ? null : good(value) ? "good" : ok(value) ? "warning" : "critical";
const TONE_LABEL: Record<Tone, string> = { good: "Good", warning: "Watch", critical: "Poor" };
const badge = (t: Tone | null) => t && <StatusBadge tone={t} label={TONE_LABEL[t]} />;

function Loading({ text }: { text: string }) {
  return <p className="text-sm text-text-secondary">{text}</p>;
}

// A quiet note instead of a raw error: says what is missing, the exact message is on hover
function Unavailable({ what, detail }: { what: string; detail: string }) {
  return (
    <p className="flex items-center gap-1.5 text-xs text-text-secondary" title={detail}>
      <span aria-hidden className="flex size-4 items-center justify-center rounded-full bg-gray-300 text-[10px]">i</span>
      {what} unavailable from Langfuse right now · hover for details
    </p>
  );
}

const SECTION_LABELS: Record<string, string> = {
  usage: "cost and tokens",
  end_to_end: "latency",
  daily: "cost per day",
  steps: "step latency",
  models: "models",
  errors: "error count",
};

// Everything from Langfuse loads on its own: it can be slow, and the rest of the dashboard comes from our database
async function LlmUsage({ days }: { days: number }) {
  const lf = await adminApi<Langfuse>(`/langfuse?days=${days}`).catch((e) => ({ failed: String(e) }));
  if ("failed" in lf) return <Card><Unavailable what="Latency, cost and tokens" detail={lf.failed} /></Card>;
  if (!lf.enabled) return <Card><p className="text-sm text-text-secondary">Langfuse tracing is off for this api.</p></Card>;
  const failed = Object.entries(SECTION_LABELS).filter(([key]) => !Array.isArray(lf[key as keyof typeof lf]));
  const failedDetail = failed.map(([key, label]) => `${label}: ${(lf[key as keyof typeof lf] as { error?: string })?.error}`).join("\n");
  const usage = rowsOf(lf.usage)[0] ?? {};
  const e2e = rowsOf(lf.end_to_end)[0] ?? {};
  const answers = n(e2e.count_count);
  const p95 = e2e.p95_latency != null ? n(e2e.p95_latency) : null;
  const cost = Array.isArray(lf.usage) ? n(usage.sum_totalCost) : null;
  const errors = rowsOf(lf.errors).reduce((s, r) => s + n(r.count_count), 0);
  const costByDay = new Map(rowsOf(lf.daily).map((r) => [String(r.time_dimension).slice(0, 10), r]));

  return (
    <>
      {failed.length > 0 && <Unavailable what={failed.map(([, label]) => label).join(", ").replace(/^./, (c) => c.toUpperCase())} detail={failedDetail} />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Latency p95" value={seconds(p95)} note={`p50 ${seconds(e2e.p50_latency as number | null)} · full answer`} status={badge(tone(p95, (v) => v <= 5000, (v) => v <= 10000))} />
        <Stat label="LLM cost" value={usd(cost)} note={answers && cost != null ? `${usd(cost / answers)} per answer` : "from Langfuse"} />
        <Stat label="Tokens" value={(n(usage.sum_inputTokens) + n(usage.sum_outputTokens)).toLocaleString()} note={`${n(usage.sum_inputTokens).toLocaleString()} in · ${n(usage.sum_outputTokens).toLocaleString()} out`} />
        <Stat
          label="LLM errors"
          value={Array.isArray(lf.errors) ? String(errors) : "–"}
          note={`${n(usage.count_count)} LLM calls`}
          status={Array.isArray(lf.errors) && <StatusBadge tone={errors ? "warning" : "good"} label={errors ? "Errors" : "OK"} />}
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="LLM cost per day (USD)" action={<span className="text-xs text-text-secondary">Langfuse estimate</span>}>
          <BarChart
            label={`LLM cost per day, last ${days} days`}
            format={(v) => usd(v)}
            bars={lastDays(days).map((day) => {
              const r = costByDay.get(day);
              return {
                key: day,
                segments: [{ value: n(r?.sum_totalCost), className: "bg-accent" }],
                tooltip: [day, usd(n(r?.sum_totalCost)), `${n(r?.count_count)} LLM calls`, `${(n(r?.sum_inputTokens) + n(r?.sum_outputTokens)).toLocaleString()} tokens`],
              };
            })}
          />
          {cost === 0 && n(usage.count_count) > 0 && (
            <p className="mt-2 text-xs text-text-secondary">Cost is 0 while calls exist: add the Gemini prices in Langfuse → Settings → Models.</p>
          )}
        </Card>
        <Card title="Latency per agent step">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-text-secondary">
              <tr>
                <th className="pb-2 font-normal">Step</th>
                <th className="pb-2 font-normal">Runs</th>
                <th className="pb-2 font-normal">p50</th>
                <th className="pb-2 font-normal">p95</th>
              </tr>
            </thead>
            <tbody>
              {rowsOf(lf.steps)
                .sort((a, b) => n(b.p95_latency) - n(a.p95_latency))
                .map((r) => (
                  <tr key={String(r.name)} className="border-t border-gray-300">
                    <td className="py-2">{r.name}</td>
                    <td>{n(r.count_count)}</td>
                    <td>{seconds(n(r.p50_latency))}</td>
                    <td>{seconds(n(r.p95_latency))}</td>
                  </tr>
                ))}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-text-secondary">
            Models: {rowsOf(lf.models).map((r) => `${r.providedModelName} (${n(r.count_count)} calls, ${usd(n(r.sum_totalCost))})`).join(" · ") || "–"}
          </p>
        </Card>
      </div>
    </>
  );
}

async function RecentRequests() {
  const data = await adminApi<{ requests: Request[]; langfuse: string }>("/requests?limit=30");
  const totals = data.requests.reduce(
    (t, r) => ({ in: t.in + (r.tokens_in ?? 0), out: t.out + (r.tokens_out ?? 0), cost: t.cost + (r.cost ?? 0) }),
    { in: 0, out: 0, cost: 0 },
  );
  return (
    <>
      {data.langfuse.startsWith("error") && (
        <div className="mb-3">
          <Unavailable what="Tokens and cost" detail={data.langfuse.replace(/^error: /, "")} />
        </div>
      )}
      {data.requests.length === 0 ? (
        <p className="text-sm text-text-secondary">No requests yet.</p>
      ) : (
        <div className="max-h-[28rem] overflow-y-auto">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-white text-xs text-text-secondary">
              <tr>
                {["Time", "Feature", "Latency", "Tokens in", "Tokens out", "Cost", "LLM calls", "Vote"].map((h) => (
                  <th key={h} className="pr-4 pb-2 font-normal whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.requests.map((r, i) => (
                <tr key={i} className="border-t border-gray-300">
                  <td className="py-2 pr-4 whitespace-nowrap">{new Date(r.created_at).toLocaleString()}</td>
                  <td className="pr-4">
                    <span className="flex items-center gap-1.5">
                      <RouteBadge route={r.route} />
                      {r.fallback && <StatusBadge tone="warning" label="Fallback" />}
                    </span>
                  </td>
                  <td className="pr-4">{seconds(r.latency_ms)}</td>
                  <td className="pr-4">{r.tokens_in?.toLocaleString() ?? "–"}</td>
                  <td className="pr-4">{r.tokens_out?.toLocaleString() ?? "–"}</td>
                  <td className="pr-4">{usd(r.cost)}</td>
                  <td className="pr-4 text-xs text-text-secondary" title={r.models?.join(", ")}>{r.llm_calls ?? "–"}</td>
                  <td>{r.vote === 1 ? "👍" : r.vote === 0 ? "👎" : ""}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-gray-400 font-medium">
                <td className="py-2" colSpan={3}>Total ({data.requests.length} requests)</td>
                <td>{totals.in.toLocaleString()}</td>
                <td>{totals.out.toLocaleString()}</td>
                <td>{usd(totals.cost)}</td>
                <td colSpan={2} />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </>
  );
}

export default async function Dashboard({ searchParams }: PageProps<"/admin">) {
  const requested = Number((await searchParams).days);
  const days = PERIODS.includes(requested) ? requested : 7;
  // Our own database only: fast
  const [overview, runs, gaps, status] = await Promise.all([
    adminApi<Day[]>(`/overview?days=${days}`),
    adminApi<Run[]>("/evals"),
    adminApi<Gap[]>("/gaps"),
    adminApi<Status>("/status"),
  ]);

  const sum = (k: keyof Day) => overview.reduce((s, r) => s + n(r[k]), 0);
  const questions = sum("questions");
  const votes = sum("up") + sum("down");
  const helpful = pct(sum("up"), votes);
  const fallbackRate = pct(sum("fallbacks"), questions);
  const latest = runs[0];
  const byDay = new Map(overview.map((r) => [r.day, r]));
  const axis = lastDays(days);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-medium tracking-heading">Dashboard</h1>
        <nav className="flex rounded-lg border border-gray-400/70 bg-white p-0.5 text-sm shadow-sm">
          {PERIODS.map((p) => (
            <Link key={p} href={`/admin?days=${p}`} className={`rounded-md px-3 py-1 ${p === days ? "bg-gray-900 text-white" : "text-text-secondary hover:text-text"}`}>
              {p === 1 ? "24 h" : `${p} days`}
            </Link>
          ))}
        </nav>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Questions" value={String(questions)} note={`${(questions / days).toFixed(1)} per day`} />
        <Stat label="Helpful votes" value={helpful == null ? "–" : `${helpful}%`} note={`${votes} votes`} status={badge(tone(helpful, (v) => v >= 80, (v) => v >= 60))} />
        <Stat label="Fallback rate" value={fallbackRate == null ? "–" : `${fallbackRate}%`} note="sent to contact options" status={badge(tone(fallbackRate, (v) => v <= 15, (v) => v <= 30))} />
        <Stat
          label="Eval gate"
          value={latest ? `${Math.round(100 * latest.summary.answer_accuracy)}%` : "–"}
          note={latest ? `latest run ${ago(latest.created_at)}` : "no runs yet"}
          status={latest && (latest.gate_passed == null ? <StatusBadge tone="warning" label="No baseline" /> : <StatusBadge tone={latest.gate_passed ? "good" : "critical"} label={latest.gate_passed ? "Passed" : "Failed"} />)}
        />
      </div>

      <Suspense key={days} fallback={<Card><Loading text="Loading latency, cost and tokens from Langfuse…" /></Card>}>
        <LlmUsage days={days} />
      </Suspense>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Questions per day" action={<Legend items={ROUTES} />} className="lg:col-span-2">
          <BarChart
            label={`Questions per day by route, last ${days} days`}
            bars={axis.map((day) => {
              const d = byDay.get(day);
              return {
                key: day,
                segments: ROUTES.map((r) => ({ value: n(d?.[r.key]), className: r.className })),
                tooltip: [day, `${n(d?.questions)} questions`, ...ROUTES.map((r) => `${r.label}: ${n(d?.[r.key])}`), `${n(d?.fallbacks)} fallbacks · 👍 ${n(d?.up)} · 👎 ${n(d?.down)}`],
              };
            })}
          />
        </Card>
        <Card title="Content gaps & 👎" action={<span className="text-xs text-text-secondary">fallbacks, downvotes</span>}>
          {gaps.length === 0 ? (
            <p className="text-sm text-text-secondary">Nothing to review right now.</p>
          ) : (
            <ul className="max-h-48 space-y-3 overflow-y-auto">
              {gaps.slice(0, 20).map((g, i) => (
                <li key={i} title={g.answer.replaceAll("**", "")}>
                  <p className="truncate text-sm font-medium">{g.question}</p>
                  <p className="mt-0.5 flex gap-2 text-xs text-text-secondary">
                    {g.fallback && <span>fallback</span>}
                    {g.vote === 0 && <span>👎</span>}
                    <span>{ago(g.created_at)}</span>
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Eval gate history" action={<span className="text-xs text-text-secondary">golden dataset · 30 questions</span>}>
        {runs.length === 0 ? (
          <p className="text-sm text-text-secondary">No eval runs yet. Run <code>python eval/run.py</code>.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-text-secondary">
                <tr>
                  {["Run", "Gate", "Answer accuracy", "Faithfulness", "Fact recall", "Citation precision", "Regressions", "Fixed", "Latency"].map((h) => (
                    <th key={h} className="pr-4 pb-2 font-normal whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 10).map((r, i) => {
                  const acc = r.summary.answer_accuracy;
                  const prev = runs[i + 1]?.summary.answer_accuracy;
                  const delta = prev == null ? null : Math.round(100 * (acc - prev));
                  const m = r.summary.metrics;
                  return (
                    <tr key={r.run} className="border-t border-gray-300">
                      <td className="py-2.5 pr-4 whitespace-nowrap">
                        {new Date(r.created_at).toLocaleString()}
                        {r.saved_as_baseline && <span className="ml-2 rounded bg-gray-900 px-1.5 py-0.5 text-[10px] text-white">baseline</span>}
                      </td>
                      <td className="pr-4">
                        {r.gate_passed == null ? <StatusBadge tone="warning" label="No baseline" /> : <StatusBadge tone={r.gate_passed ? "good" : "critical"} label={r.gate_passed ? "Passed" : "Failed"} />}
                      </td>
                      <td className="pr-4">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-gray-300">
                            <div className="h-full rounded-full bg-accent" style={{ width: `${100 * acc}%` }} />
                          </div>
                          <span className="font-medium">{Math.round(100 * acc)}%</span>
                          {delta != null && delta !== 0 && <span className="text-xs text-text-secondary">{delta > 0 ? `+${delta}` : delta}</span>}
                        </div>
                      </td>
                      <td className="pr-4">{m.faithfulness?.toFixed(2) ?? "–"}</td>
                      <td className="pr-4">{m.fact_recall?.toFixed(2) ?? "–"}</td>
                      <td className="pr-4">{m.citation_precision?.toFixed(2) ?? "–"}</td>
                      <td className="pr-4 text-xs">
                        {r.regressions?.length ? (
                          <span className="text-critical" title={r.regressions.map((x) => `${x.id}: ${x.question}`).join("\n")}>
                            ✕ {r.regressions.map((x) => x.id).join(", ")}
                          </span>
                        ) : (
                          <span className="text-text-secondary">none</span>
                        )}
                      </td>
                      <td className="pr-4 text-xs text-text-secondary" title={r.fixed?.map((x) => `${x.id}: ${x.question}`).join("\n")}>
                        {r.fixed?.length ? r.fixed.map((x) => x.id).join(", ") : "–"}
                      </td>
                      <td className="text-xs text-text-secondary whitespace-nowrap">{r.summary.latency_avg} s</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {latest && latest.failures.length > 0 && (
              <p className="mt-3 text-xs text-text-secondary">
                Failing in the latest run: {latest.failures.map((f) => `${f.id} (${f.checks})`).join(" · ")}
              </p>
            )}
          </div>
        )}
      </Card>

      <Card title="Recent requests" action={<span className="text-xs text-text-secondary">latest 30 · tokens and cost from Langfuse</span>}>
        <Suspense fallback={<Loading text="Loading requests…" />}>
          <RecentRequests />
        </Suspense>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="System">
          <dl className="space-y-2 text-sm">
            {[
              ["Status", "api and database reachable"],
              ["Knowledge base", `${status.chunks} chunks · ${status.products} products`],
              ["Stored chats", `${status.conversations} conversations · ${status.messages} messages`],
              ["Retention", `chats ${status.retention_days} day(s), counts 1 year`],
              ["Chat model", status.chat_model],
              ["Embeddings", status.embedding_model],
              ["Tracing", status.tracing ? "Langfuse on" : "off"],
              ["Revisions", `api ${status.revision} · web ${process.env.K_REVISION ?? "local"}`],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4">
                <dt className="text-text-secondary">{k}</dt>
                <dd className="truncate text-right">{v}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card title="Daily numbers" className="lg:col-span-2">
          <div className="max-h-64 overflow-y-auto">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-white text-xs text-text-secondary">
                <tr>
                  {["Day", "Questions", "Policy", "Product", "Other", "Fallbacks", "👍", "👎", "Avg latency"].map((h) => (
                    <th key={h} className="pb-2 font-normal">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...overview].reverse().map((d) => (
                  <tr key={d.day} className="border-t border-gray-300">
                    <td className="py-2">{d.day}</td>
                    <td>{d.questions}</td>
                    <td>{d.policy}</td>
                    <td>{d.product}</td>
                    <td>{d.other}</td>
                    <td>{d.fallbacks}</td>
                    <td>{d.up}</td>
                    <td>{d.down}</td>
                    <td>{seconds(d.avg_latency_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}

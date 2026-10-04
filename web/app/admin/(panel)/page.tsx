import Link from "next/link";
import { Suspense } from "react";
import { adminApi } from "@/lib/admin";
import BarChart, { lastDays } from "../BarChart";
import { Card, Legend, ROUTES, RouteBadge, Stat, StatusBadge, ago, pct, seconds, usd, type Tone } from "../ui";

type Day = { day: string; questions: number; policy: number; product: number; style: number; other: number; fallbacks: number; up: number; down: number; avg_latency_ms: number | null };
type Row = Record<string, string | number | null>;
type Section = Row[] | { error: string };
type Langfuse = { enabled: false } | { enabled: true; usage: Section; daily: Section; models: Section; end_to_end: Section; steps: Section; errors: Section };
type Run = {
  run: string;
  created_at: string;
  summary: {
    questions: number;
    answer_accuracy: number;
    errors?: number;
    latency_avg: number;
    metrics: Record<string, number | null>;
    categories?: Record<string, number | null>;
  };
  failures: { id: string; question: string; checks: string }[];
  gate_passed: boolean | null;
  baseline: string | null;
  regressions: { id: string; question: string }[] | null;
  fixed: { id: string; question: string }[] | null;
  saved_as_baseline: boolean | null;
};
type Gap = {
  message_id: number;
  conversation_id: string;
  created_at: string;
  question: string;
  answer: string;
  fallback: boolean;
  vote: 0 | 1 | null;
  trace_url: string | null;
  session_url: string | null;
};
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
  trace_url: string | null;
};
type TryOnKind = { uses: number; failures: number; avg_latency_ms: number | null; cost: number };
type TryOnUsage = {
  totals: Partial<Record<"size" | "tryon" | "look", TryOnKind>>;
  daily: { day: string; tryons: number; sizes: number; cost: number }[];
  top_products: { id: string; uses: number; name: string; color?: string | null; section?: string; url?: string }[];
};
type Status = { chunks: number; products: number; conversations: number; messages: number; events: number; retention_days: number; chat_model: string; embedding_model: string; tracing: boolean; revision: string };

const PERIODS = [1, 7, 30];
const n = (v: unknown) => Number(v ?? 0);
const rowsOf = (s: Section | undefined) => (Array.isArray(s) ? s : []);
const tone = (value: number | null, good: (v: number) => boolean, ok: (v: number) => boolean): Tone | null =>
  value == null ? null : good(value) ? "good" : ok(value) ? "warning" : "critical";
const TONE_LABEL: Record<Tone, string> = { good: "Good", warning: "Watch", critical: "Poor" };
const badge = (t: Tone | null) => t && <StatusBadge tone={t} label={TONE_LABEL[t]} />;

function ExternalLink({ href, title, children }: { href: string; title?: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" title={title} className="text-text underline decoration-gray-500 underline-offset-2 hover:decoration-gray-900">
      {children} ↗
    </a>
  );
}

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
                {["Time", "Feature", "Latency", "Tokens in", "Tokens out", "Cost", "LLM calls", "Vote", "Trace"].map((h) => (
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
                  <td className="pr-4">{r.vote === 1 ? "👍" : r.vote === 0 ? "👎" : ""}</td>
                  <td className="text-xs">{r.trace_url ? <ExternalLink href={r.trace_url}>Open</ExternalLink> : "–"}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-gray-400 font-medium">
                <td className="py-2" colSpan={3}>Total ({data.requests.length} requests)</td>
                <td>{totals.in.toLocaleString()}</td>
                <td>{totals.out.toLocaleString()}</td>
                <td>{usd(totals.cost)}</td>
                <td colSpan={3} />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </>
  );
}

// Try-on panel usage from our own table: counts, failures, estimated cost and the products tried on most
function TryOnPanel({ usage, days }: { usage: TryOnUsage; days: number }) {
  const t = usage.totals;
  const tryons = t.tryon?.uses ?? 0;
  const cost = Object.values(t).reduce((sum, k) => sum + (k?.cost ?? 0), 0);
  const failed = pct(t.tryon?.failures ?? 0, tryons);
  const byDay = new Map(usage.daily.map((d) => [d.day, d]));
  const top = usage.top_products;
  const most = Math.max(...top.map((p) => p.uses), 1);
  return (
    <Card title="Try it on" action={<span className="text-xs text-text-secondary">usage and estimated cost · no photos stored</span>}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Size requests" value={String(t.size?.uses ?? 0)} note="from a photo or height and weight" />
        <Stat label="Try-ons" value={String(tryons)} note={`avg ${seconds(t.tryon?.avg_latency_ms)} · ${t.look?.uses ?? 0} looks suggested`} />
        <Stat
          label="Failed try-ons"
          value={failed == null ? "–" : `${failed}%`}
          note={`${t.tryon?.failures ?? 0} of ${tryons}`}
          status={badge(tone(failed, (v) => v <= 5, (v) => v <= 15))}
        />
        <Stat label="Estimated cost" value={usd(cost)} note={tryons ? `${usd((t.tryon?.cost ?? 0) / tryons)} per try-on · list prices` : "list prices"} />
      </div>
      <div className="mt-5 grid gap-6 lg:grid-cols-2">
        <div>
          <p className="mb-2 text-xs text-text-secondary">Try-ons per day</p>
          <BarChart
            label={`Try-ons per day, last ${days} days`}
            bars={lastDays(days).map((day) => {
              const d = byDay.get(day);
              return {
                key: day,
                segments: [{ value: n(d?.tryons), className: "bg-accent" }],
                tooltip: [day, `${n(d?.tryons)} try-ons`, `${n(d?.sizes)} size requests`, `≈ ${usd(n(d?.cost))}`],
              };
            })}
          />
        </div>
        <div>
          <p className="mb-2 text-xs text-text-secondary">Most tried-on products</p>
          {top.length === 0 ? (
            <p className="text-sm text-text-secondary">No try-ons in this period.</p>
          ) : (
            <ol className="space-y-2.5" aria-label="Most tried-on products">
              {top.map((p) => (
                <li key={p.id} className="text-sm" title={[p.name, p.color, p.section, `${p.uses} try-ons`].filter(Boolean).join(" · ")}>
                  <div className="flex justify-between gap-3">
                    {p.url ? (
                      <a href={p.url} target="_blank" rel="noopener noreferrer" className="truncate hover:underline">
                        {p.name}
                        {p.color && <span className="text-text-secondary"> · {p.color}</span>}
                      </a>
                    ) : (
                      <span className="truncate">{p.name}</span>
                    )}
                    <span className="tabular-nums">{p.uses}</span>
                  </div>
                  <div className="mt-1 h-2 rounded-[4px] bg-gray-300">
                    <div className="h-full rounded-[4px] bg-accent" style={{ width: `${(100 * p.uses) / most}%` }} />
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </Card>
  );
}

export default async function Dashboard({ searchParams }: PageProps<"/admin">) {
  const requested = Number((await searchParams).days);
  const days = PERIODS.includes(requested) ? requested : 7;
  // Our own database only: fast
  const [overview, runs, gaps, status, tryonUsage] = await Promise.all([
    adminApi<Day[]>(`/overview?days=${days}`),
    adminApi<Run[]>("/evals"),
    adminApi<Gap[]>("/gaps"),
    adminApi<Status>("/status"),
    adminApi<TryOnUsage>(`/tryon?days=${days}`),
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
        <Card title="Content gaps & 👎" action={<span className="text-xs text-text-secondary">fallbacks, downvotes · open a trace to see why</span>}>
          {gaps.length === 0 ? (
            <p className="text-sm text-text-secondary">Nothing to review right now.</p>
          ) : (
            <ul className="max-h-48 space-y-3 overflow-y-auto">
              {gaps.slice(0, 20).map((g) => (
                <li key={g.message_id} title={g.answer.replaceAll("**", "")}>
                  <p className="truncate text-sm font-medium">{g.question}</p>
                  <p className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-text-secondary">
                    <span className="font-mono" title={`message ${g.message_id} · conversation ${g.conversation_id}`}>#{g.message_id}</span>
                    {g.fallback && <span>fallback</span>}
                    {g.vote === 0 && <span>👎</span>}
                    <span>{ago(g.created_at)}</span>
                    {g.trace_url && <ExternalLink href={g.trace_url} title="This answer step by step: intent, sources, draft, judge">Trace</ExternalLink>}
                    {g.session_url && <ExternalLink href={g.session_url} title="The whole conversation in Langfuse">Chat</ExternalLink>}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <TryOnPanel usage={tryonUsage} days={days} />

      <Card title="Eval gate history" action={<span className="text-xs text-text-secondary">golden dataset · {latest?.summary.questions ?? 40} questions</span>}>
        {runs.length === 0 ? (
          <p className="text-sm text-text-secondary">No eval runs yet. Run <code>python eval/run.py</code>.</p>
        ) : (
          <div className="overflow-x-auto">
            <p className="mb-2 text-xs text-text-secondary">Answer accuracy per run, oldest first · hover for details</p>
            <div className="mb-5 flex h-16 items-end gap-1 border-b border-gray-400" role="img" aria-label="Answer accuracy per eval run, oldest first">
              {[...runs].slice(0, 20).reverse().map((r) => (
                <div
                  key={r.run}
                  className="flex h-full flex-1 items-end"
                  title={`${new Date(r.created_at).toLocaleString()} · ${Math.round(100 * r.summary.answer_accuracy)}% · gate ${r.gate_passed == null ? "no baseline" : r.gate_passed ? "passed" : "failed"}`}
                >
                  <div className="w-full rounded-t-[4px] bg-accent hover:opacity-80" style={{ height: `${100 * r.summary.answer_accuracy}%`, minHeight: 3 }} />
                </div>
              ))}
            </div>
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-text-secondary">
                <tr>
                  {["Run", "Gate", "Answer accuracy", "Intent", "Faithfulness", "Fact recall", "Citation precision", "Multi-turn", "Regressions", "Fixed", "Latency"].map((h) => (
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
                      <td
                        className="pr-4"
                        title={Object.entries(r.summary.categories ?? {})
                          .map(([c, v]) => `${c}: ${v == null ? "–" : `${Math.round(100 * v)}%`}`)
                          .join("\n")}
                      >
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-gray-300">
                            <div className="h-full rounded-full bg-accent" style={{ width: `${100 * acc}%` }} />
                          </div>
                          <span className="font-medium">{Math.round(100 * acc)}%</span>
                          {delta != null && delta !== 0 && <span className="text-xs text-text-secondary">{delta > 0 ? `+${delta}` : delta}</span>}
                        </div>
                      </td>
                      <td className="pr-4">{m.intent_accuracy?.toFixed(2) ?? "–"}</td>
                      <td className="pr-4">{m.faithfulness?.toFixed(2) ?? "–"}</td>
                      <td className="pr-4">{m.fact_recall?.toFixed(2) ?? "–"}</td>
                      <td className="pr-4">{m.citation_precision?.toFixed(2) ?? "–"}</td>
                      <td className="pr-4" title="mid-conversation replies that don't greet again">{m.no_repeat_greeting?.toFixed(2) ?? "–"}</td>
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
                  {["Day", "Questions", "Policy", "Product", "Style", "Other", "Fallbacks", "👍", "👎", "Avg latency"].map((h) => (
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
                    <td>{d.style}</td>
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

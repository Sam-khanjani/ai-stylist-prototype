import { adminApi } from "@/lib/admin";

type Run = {
  run: string;
  created_at: string;
  summary: { questions: number; answer_accuracy: number; errors?: number; latency_avg: number; metrics: Record<string, number | null> };
  failures: { id: string; question: string; checks: string }[];
};

const COLUMNS = ["fact_recall", "retrieval_recall", "citation_recall", "faithfulness", "citation_precision"];
const fmt = (v: number | null | undefined) => (v == null ? "–" : v.toFixed(2));

export default async function Evals() {
  const runs = await adminApi<Run[]>("/evals");
  const latest = runs[0];
  return (
    <div>
      <h1 className="text-2xl font-medium tracking-heading">Evaluation runs</h1>
      <p className="mt-1 text-sm text-text-secondary">
        Golden dataset results from <code>python eval/run.py</code> (full runs only, newest first).
      </p>
      {!latest ? (
        <p className="mt-8 text-sm text-text-secondary">No runs yet. Run the eval once to see results here.</p>
      ) : (
        <>
          <table className="mt-6 w-full text-left text-sm">
            <thead className="text-xs text-text-secondary">
              <tr>
                <th className="py-2 pr-4 font-normal">Run</th>
                <th className="pr-4 font-normal">Answer accuracy</th>
                {COLUMNS.map((c) => (
                  <th key={c} className="pr-4 font-normal">{c.replace("_", " ")}</th>
                ))}
                <th className="pr-4 font-normal">Avg latency</th>
                <th className="font-normal">Errors</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.run} className="border-t border-border">
                  <td className="py-2 pr-4 whitespace-nowrap">{new Date(r.created_at).toLocaleString()}</td>
                  <td className="pr-4 font-medium">{fmt(r.summary.answer_accuracy)}</td>
                  {COLUMNS.map((c) => (
                    <td key={c} className="pr-4">{fmt(r.summary.metrics[c])}</td>
                  ))}
                  <td className="pr-4">{r.summary.latency_avg} s</td>
                  <td>{r.summary.errors ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h2 className="mt-10 text-sm font-medium">Failing questions in the latest run</h2>
          {latest.failures.length === 0 ? (
            <p className="mt-2 text-sm text-text-secondary">All {latest.summary.questions} questions passed.</p>
          ) : (
            <ul className="mt-2 divide-y divide-border text-sm">
              {latest.failures.map((f) => (
                <li key={f.id} className="py-2">
                  <span className="mr-2 text-text-secondary">{f.id}</span>
                  {f.question}
                  <span className="ml-2 text-xs text-text-secondary">{f.checks}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

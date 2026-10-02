import { adminApi } from "@/lib/admin";

type Status = {
  chunks: number;
  products: number;
  conversations: number;
  messages: number;
  events: number;
  oldest_conversation: string | null;
  db_time: string;
  retention_days: number;
  event_retention_days: number;
  chat_model: string;
  embedding_model: string;
  tracing: boolean;
  revision: string;
};

export default async function StatusPage() {
  const s = await adminApi<Status>("/status");
  const rows: [string, string][] = [
    ["api", `OK · revision ${s.revision}`],
    ["web", `OK · revision ${process.env.K_REVISION ?? "local"}`],
    ["Database", `OK · ${new Date(s.db_time).toLocaleString()}`],
    ["Knowledge base", `${s.chunks} chunks · ${s.products} products`],
    ["Chat history", `${s.conversations} conversations · ${s.messages} messages`],
    ["Oldest stored chat", s.oldest_conversation ? new Date(s.oldest_conversation).toLocaleString() : "–"],
    ["Retention", `chats ${s.retention_days} day(s) · anonymous counts ${s.event_retention_days} days (${s.events} rows)`],
    ["Chat model", `${s.chat_model} (Vertex AI, eu)`],
    ["Embedding model", `${s.embedding_model} (Vertex AI, europe-west4)`],
    ["Langfuse tracing", s.tracing ? "on" : "off"],
  ];
  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-medium tracking-heading">Production status</h1>
      <dl className="mt-6 divide-y divide-border text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="flex gap-4 py-3">
            <dt className="w-44 shrink-0 text-text-secondary">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-6 text-xs text-text-secondary">
        If this page loads, the web app, the api and the database are all reachable. For error rates, latency and
        costs see Cloud Run metrics, Langfuse and GCP Billing.
      </p>
    </div>
  );
}

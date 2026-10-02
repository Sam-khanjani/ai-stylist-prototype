import Link from "next/link";
import { notFound } from "next/navigation";
import { adminApi, requireAdmin } from "@/lib/admin";

type Message = {
  role: "user" | "assistant";
  text: string;
  sources: { n: number; title: string; url: string }[] | null;
  products: { n: number; name: string; url: string }[] | null;
  fallback: boolean | null;
  vote: 0 | 1 | null;
  created_at: string;
};

export default async function ConversationDetail({ params }: PageProps<"/admin/conversations/[id]">) {
  const { id } = await params;
  await requireAdmin(); // before the catch below, which would otherwise swallow the login redirect
  const data = await adminApi<{ title: string; summary: string; messages: Message[] }>(`/conversations/${id}`).catch(
    () => null,
  );
  if (!data) notFound();

  return (
    <div className="max-w-3xl">
      <Link href="/admin/conversations" className="text-xs text-text-secondary hover:text-text">
        ← Conversations
      </Link>
      <h1 className="mt-2 text-2xl font-medium tracking-heading">{data.title}</h1>
      {data.summary && (
        <div className="mt-4 rounded-md bg-surface p-3 text-xs whitespace-pre-line">
          <p className="mb-1 text-text-secondary">Memory (summary the bot uses)</p>
          {data.summary}
        </div>
      )}

      <ol className="mt-6 space-y-4">
        {data.messages.map((m, i) => (
          <li key={i} className={m.role === "user" ? "ml-auto w-fit max-w-[80%] rounded-md bg-gray-800 px-3 py-2 text-sm text-white" : "text-sm"}>
            <p className="whitespace-pre-wrap">{m.text}</p>
            {m.role === "assistant" && (
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-text-secondary">
                {m.fallback && <span className="rounded bg-surface px-1.5">fallback</span>}
                {m.vote !== null && <span>{m.vote ? "👍" : "👎"}</span>}
                {m.products?.map((p) => (
                  <a key={p.n} href={p.url} target="_blank" rel="noopener noreferrer" className="underline">
                    [{p.n}] {p.name}
                  </a>
                ))}
                {m.sources
                  ?.filter((s) => !m.products?.some((p) => p.n === s.n))
                  .map((s) => (
                    <a key={s.n} href={s.url} target="_blank" rel="noopener noreferrer" className="underline">
                      [{s.n}] {s.title}
                    </a>
                  ))}
                <span>{new Date(m.created_at).toLocaleTimeString()}</span>
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

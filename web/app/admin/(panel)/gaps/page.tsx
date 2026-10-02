import Link from "next/link";
import { adminApi } from "@/lib/admin";

type Gap = { conversation_id: string; created_at: string; question: string; answer: string; fallback: boolean; vote: 0 | 1 | null };

export default async function Gaps() {
  const gaps = await adminApi<Gap[]>("/gaps");
  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-medium tracking-heading">Content gaps & 👎</h1>
      <p className="mt-1 text-sm text-text-secondary">
        Questions the assistant couldn&apos;t answer (fallback) and answers that got a thumbs down. These show what the
        knowledge base is missing or gets wrong.
      </p>
      {gaps.length === 0 ? (
        <p className="mt-8 text-sm text-text-secondary">Nothing to review right now.</p>
      ) : (
        <ul className="mt-6 divide-y divide-border">
          {gaps.map((g, i) => (
            <li key={i} className="py-4 text-sm">
              <div className="flex items-baseline justify-between gap-4">
                <p className="font-medium">{g.question}</p>
                <span className="shrink-0 text-xs text-text-secondary">
                  {g.fallback && "fallback "}
                  {g.vote === 0 && "👎 "}
                  {new Date(g.created_at).toLocaleString()}
                </span>
              </div>
              <p className="mt-1 line-clamp-3 text-text-secondary">{g.answer.replaceAll("**", "")}</p>
              <Link href={`/admin/conversations/${g.conversation_id}`} className="mt-1 inline-block text-xs underline">
                Open conversation
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

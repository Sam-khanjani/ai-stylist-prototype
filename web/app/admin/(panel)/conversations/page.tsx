import Link from "next/link";
import { adminApi } from "@/lib/admin";

type Row = { id: string; title: string; updated_at: string; messages: number; fallback: boolean; up: number; down: number };

export default async function Conversations() {
  const rows = await adminApi<Row[]>("/conversations");
  return (
    <div>
      <h1 className="text-2xl font-medium tracking-heading">Conversations</h1>
      <p className="mt-1 text-sm text-text-secondary">Only chats within the retention period are kept.</p>
      {rows.length === 0 ? (
        <p className="mt-8 text-sm text-text-secondary">No conversations right now.</p>
      ) : (
        <table className="mt-6 w-full text-left text-sm">
          <thead className="text-xs text-text-secondary">
            <tr>
              <th className="py-2 pr-4 font-normal">First question</th>
              <th className="pr-4 font-normal">Last activity</th>
              <th className="pr-4 font-normal">Messages</th>
              <th className="pr-4 font-normal">Fallback</th>
              <th className="font-normal">Votes</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-border">
                <td className="py-2 pr-4">
                  <Link href={`/admin/conversations/${r.id}`} className="underline hover:text-text-secondary">
                    {r.title}
                  </Link>
                </td>
                <td className="pr-4 whitespace-nowrap">{new Date(r.updated_at).toLocaleString()}</td>
                <td className="pr-4">{r.messages}</td>
                <td className="pr-4">{r.fallback ? "yes" : ""}</td>
                <td className="whitespace-nowrap">{r.up || r.down ? `👍 ${r.up} · 👎 ${r.down}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

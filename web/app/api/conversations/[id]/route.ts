import { apiFetch } from "@/lib/api";
import { visitorId } from "@/lib/visitor";

export async function GET(_req: Request, ctx: RouteContext<"/api/conversations/[id]">) {
  const { id } = await ctx.params;
  const res = await apiFetch(`/conversations/${encodeURIComponent(id)}`, {
    headers: { "X-Visitor-Id": await visitorId() },
  });
  return new Response(res.body, { status: res.status, headers: { "Content-Type": "application/json" } });
}

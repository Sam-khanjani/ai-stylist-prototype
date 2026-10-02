import { apiFetch } from "@/lib/api";
import { visitorId } from "@/lib/visitor";

export async function POST(req: Request) {
  const res = await apiFetch("/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Visitor-Id": await visitorId() },
    body: await req.text(),
  });
  return new Response(res.body, { status: res.status, headers: { "Content-Type": "application/json" } });
}

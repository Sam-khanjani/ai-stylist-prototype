import { apiFetch } from "@/lib/api";
import { visitorId } from "@/lib/visitor";

// The browser can't call the private api, so this route forwards the request and pipes the SSE stream back
export async function POST(req: Request) {
  const res = await apiFetch("/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Visitor-Id": await visitorId() },
    body: await req.text(),
  });
  return new Response(res.body, {
    status: res.status,
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
  });
}

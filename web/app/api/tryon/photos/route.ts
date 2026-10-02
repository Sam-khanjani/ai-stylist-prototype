import { apiFetch } from "@/lib/api";
import { visitorId } from "@/lib/visitor";

async function forward(path: string, method: string) {
  const res = await apiFetch(path, { method, headers: { "X-Visitor-Id": await visitorId() } });
  return new Response(res.body, { status: res.status, headers: { "Content-Type": "application/json" } });
}

// Signed upload URL for a try-on photo
export async function POST() {
  return forward("/tryon/photos", "POST");
}

export async function DELETE(req: Request) {
  const id = new URL(req.url).searchParams.get("id") ?? "";
  return forward(`/tryon/photos/${encodeURIComponent(id)}`, "DELETE");
}

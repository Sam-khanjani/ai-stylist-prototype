import { apiFetch } from "@/lib/api";

export async function POST(req: Request) {
  const res = await apiFetch("/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await req.text(),
  });
  return new Response(res.body, { status: res.status, headers: { "Content-Type": "application/json" } });
}

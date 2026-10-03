import { apiFetch } from "@/lib/api";

// Catalog items that complete the try-on outfit
export async function POST(req: Request) {
  const res = await apiFetch("/tryon/look", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await req.text(),
  });
  return new Response(res.body, { status: res.status, headers: { "Content-Type": "application/json" } });
}

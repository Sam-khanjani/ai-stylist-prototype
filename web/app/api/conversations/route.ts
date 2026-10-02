import { apiFetch } from "@/lib/api";
import { visitorId } from "@/lib/visitor";

async function forward(method: "GET" | "DELETE") {
  const res = await apiFetch("/conversations", { method, headers: { "X-Visitor-Id": await visitorId() } });
  return new Response(res.body, { status: res.status, headers: { "Content-Type": "application/json" } });
}

// List of this browser's chats
export const GET = () => forward("GET");

// "Delete my chats"
export const DELETE = () => forward("DELETE");

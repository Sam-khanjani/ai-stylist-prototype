import { endSession } from "@/lib/admin";

export async function POST() {
  await endSession();
  return new Response(null, { status: 303, headers: { Location: "/admin/login" } });
}

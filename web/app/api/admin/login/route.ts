import { passwordMatches, startSession } from "@/lib/admin";

// Relative redirects: behind Cloud Run, req.url can carry the container's internal host
const go = (path: string) => new Response(null, { status: 303, headers: { Location: path } });

export async function POST(req: Request) {
  const form = await req.formData();
  if (!passwordMatches(String(form.get("password") ?? ""))) {
    await new Promise((r) => setTimeout(r, 1000)); // slow down guessing
    return go("/admin/login?error=1");
  }
  await startSession();
  return go("/admin");
}

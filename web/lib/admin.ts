import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { apiFetch } from "./api";

// Single admin password from Secret Manager. Without it the dashboard stays locked.
const PASSWORD = process.env.ADMIN_PASSWORD;
const COOKIE = "admin_session";
const SESSION_SECONDS = 12 * 60 * 60;

// The session cookie is "<expiry>.<signature>"; signing with the password means changing it logs everyone out
function sign(expiry: string) {
  return createHmac("sha256", PASSWORD!).update(`admin:${expiry}`).digest("hex");
}

function equal(a: string, b: string) {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

export function passwordMatches(input: string) {
  return Boolean(PASSWORD) && equal(sign(input), sign(PASSWORD!));
}

export async function startSession() {
  const expiry = String(Math.floor(Date.now() / 1000) + SESSION_SECONDS);
  (await cookies()).set(COOKIE, `${expiry}.${sign(expiry)}`, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    maxAge: SESSION_SECONDS,
    path: "/",
  });
}

export async function endSession() {
  (await cookies()).delete(COOKIE);
}

export async function isAdmin() {
  if (!PASSWORD) return false;
  const [expiry, signature] = ((await cookies()).get(COOKIE)?.value ?? "").split(".");
  return Boolean(expiry && signature) && Number(expiry) > Date.now() / 1000 && equal(signature, sign(expiry));
}

export async function requireAdmin() {
  if (!(await isAdmin())) redirect("/admin/login");
}

// Every data fetch checks the session itself, so no page can render admin data without it
export async function adminApi<T>(path: string): Promise<T> {
  await requireAdmin();
  const res = await apiFetch(`/admin${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`api /admin${path} returned ${res.status}`);
  return res.json();
}

import { cookies } from "next/headers";

const COOKIE = "visitor_id";
const MAX_AGE = 30 * 24 * 60 * 60; // matches the 30 day chat retention
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Anonymous id for chat history: random, httpOnly (page scripts can't read it), nothing personal in it
export async function visitorId() {
  const store = await cookies();
  const current = store.get(COOKIE)?.value;
  const id = current && UUID.test(current) ? current : crypto.randomUUID();
  // Refreshed on every use, so the 30 days count from the last visit
  store.set(COOKIE, id, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: MAX_AGE,
    path: "/",
  });
  return id;
}

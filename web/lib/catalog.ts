import { readFile } from "node:fs/promises";
import path from "node:path";

export type Product = {
  id: string;
  url: string;
  name: string;
  category: string;
  color: string | null;
  material: string | null;
  description: string | null;
  price: number | null;
  currency: string | null;
  images: string[];
};

const API_URL = process.env.API_URL;
// Fallback for local dev without the api: output of ingest/crawl.py
const CATALOG = path.join(process.cwd(), "..", "data", "raw", "products.jsonl");

// On Cloud Run the api is private, so calls carry an identity token from the metadata server
async function authHeaders(): Promise<HeadersInit> {
  if (!process.env.K_SERVICE || !API_URL) return {};
  const res = await fetch(
    `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${API_URL}`,
    { headers: { "Metadata-Flavor": "Google" } },
  );
  return { Authorization: `Bearer ${await res.text()}` };
}

export async function getProducts(): Promise<Product[]> {
  if (API_URL) {
    const res = await fetch(`${API_URL}/products`, { headers: await authHeaders() });
    if (!res.ok) throw new Error(`api /products returned ${res.status}`);
    return res.json();
  }
  const text = await readFile(CATALOG, "utf-8").catch(() => "");
  return text
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

// Top-level section from the product url, e.g. .../men/suits/... -> "suits"
export function section(p: Product) {
  return new URL(p.url).pathname.split("/")[3];
}

export function formatPrice(p: Product) {
  if (p.price == null) return "";
  return new Intl.NumberFormat("en-NL", {
    style: "currency",
    currency: p.currency ?? "EUR",
    maximumFractionDigits: 0,
  }).format(p.price);
}

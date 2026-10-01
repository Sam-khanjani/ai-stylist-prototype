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

// Output of ingest/crawl.py; will move behind the api service later
const CATALOG = path.join(process.cwd(), "..", "data", "raw", "products.jsonl");

export async function getProducts(): Promise<Product[]> {
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

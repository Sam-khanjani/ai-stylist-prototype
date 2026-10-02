import { readFile } from "node:fs/promises";
import path from "node:path";
import { apiFetch, hasApi } from "./api";
import type { Card } from "./card";

export type Product = Omit<Card, "image"> & {
  category: string;
  description: string | null;
  images: string[];
};

// Fallback for local dev without the api: output of ingest/crawl.py
const CATALOG = path.join(process.cwd(), "..", "data", "raw", "products.jsonl");

export async function getProducts(): Promise<Product[]> {
  if (hasApi) {
    const res = await apiFetch("/products");
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

export function toCard(p: Product): Card {
  return { ...p, image: p.images[0]?.replace("w_1200", "w_600") ?? null };
}

import TryOn from "../components/TryOn";
import { getProducts, section, toCard } from "@/lib/catalog";

export default async function Home({ searchParams }: PageProps<"/">) {
  const { section: selected } = await searchParams;
  const products = (await getProducts()).filter((p) => !selected || section(p) === selected);

  return (
    <div className="px-2 py-8 lg:px-5">
      <div className="mb-6 flex items-baseline justify-between px-2">
        <h1 className="text-2xl font-medium tracking-heading capitalize">{selected ?? "All products"}</h1>
        <span className="text-sm text-text-secondary">{products.length} items</span>
      </div>
      <TryOn products={products.map(toCard)} />
    </div>
  );
}

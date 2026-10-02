import { formatPrice, type Card } from "@/lib/card";

export default function ProductCard({ product }: { product: Card }) {
  return (
    <a href={product.url} target="_blank" rel="noopener noreferrer" className="group block">
      <div className="aspect-5/6 overflow-hidden bg-surface">
        {product.image && (
          // Images are linked from the source site, not re-hosted
          <img
            src={product.image}
            alt={product.name}
            loading="lazy"
            className="h-full w-full object-contain mix-blend-multiply transition-transform duration-500 group-hover:scale-[1.03]"
          />
        )}
      </div>
      <div className="mt-3 px-1">
        <h3 className="text-sm font-medium">{product.name}</h3>
        <p className="text-sm text-text-secondary">{[product.color, product.material].filter(Boolean).join(" · ")}</p>
        <p className="mt-1 text-sm">{formatPrice(product.price, product.currency)}</p>
      </div>
    </a>
  );
}

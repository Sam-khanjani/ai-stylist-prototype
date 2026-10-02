// Shared by the server-rendered grid and the client-side chat, so no Node imports here

export type Card = {
  id: string;
  url: string;
  name: string;
  color: string | null;
  material: string | null;
  price: number | null;
  currency: string | null;
  image: string | null;
};

export function formatPrice(price: number | null, currency: string | null) {
  if (price == null) return "";
  return new Intl.NumberFormat("en-NL", {
    style: "currency",
    currency: currency ?? "EUR",
    maximumFractionDigits: 0,
  }).format(price);
}

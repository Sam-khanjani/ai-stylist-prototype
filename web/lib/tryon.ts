import type { Card } from "./card";
import type { Body } from "./pose";

export type Fit = "slim" | "regular" | "relaxed";
export type Profile = { heightCm: number; weightKg: number | null; fit: Fit };
export type Size = { eu: number; length: "short" | "regular" | "long"; label: string };
export type SizeAdvice = {
  jacket: Size;
  trousers: Size;
  confidence: "high" | "medium" | "low";
  reasons: string[];
  notes: string[];
  measurements: Record<string, number>;
};
export type TryOnResult = { size: SizeAdvice | null; image: string | null };

// Every size in the catalog's EU system (same as the api): regular 44-60, short = N/2, long = 2N-2.
// US numbers are N-10 for jackets (chest) and N-16 for trousers (waist in inches).
export function sizeOptions(usOffset: number): Size[] {
  return (["short", "regular", "long"] as const).flatMap((length) =>
    [44, 46, 48, 50, 52, 54, 56, 58, 60]
      .filter((n) => length !== "long" || n > 44)
      .map((n) => {
        const eu = { short: n / 2, regular: n, long: 2 * n - 2 }[length];
        const suffix = { short: "S", regular: "", long: "L" }[length];
        return { eu, length, label: `EU ${eu} / US ${n - usOffset}${suffix}` };
      }),
  );
}

const MAX_SIDE = 2048;

// Re-encoding in the browser drops EXIF data such as the GPS location, and shrinks phone photos before upload
async function toJpeg(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Couldn't read this photo."))), "image/jpeg", 0.9),
  );
}

// Uploads straight to the private bucket with a signed URL from the api; returns the photo id
export async function uploadPhoto(file: File): Promise<string> {
  const jpeg = await toJpeg(file);
  const res = await fetch("/api/tryon/photos", { method: "POST" });
  if (!res.ok) throw new Error("Couldn't prepare the upload. Please try again.");
  const { photo_id, upload_url, headers } = await res.json();
  const put = await fetch(upload_url, { method: "PUT", headers, body: jpeg });
  if (!put.ok) throw new Error("The photo upload failed. Please try again.");
  return photo_id;
}

export async function deletePhoto(photoId: string) {
  await fetch(`/api/tryon/photos?id=${encodeURIComponent(photoId)}`, { method: "DELETE" });
}

// Without products: size advice from the photo's measurements and/or height and weight.
// With products (up to two): the try-on image (a data URL, not stored anywhere).
export async function tryOn(
  photoId: string | null,
  productIds: string[],
  profile: Profile,
  body: Body | null,
): Promise<TryOnResult> {
  const res = await fetch("/api/tryon", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      photo_id: photoId,
      product_ids: productIds,
      height_cm: profile.heightCm,
      weight_kg: profile.weightKg,
      fit: profile.fit,
      body,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The try-on didn't work. Please try again.");
  return data;
}

export type LookItem = Card & { reason: string | null };

// 2-3 catalog items that go with the outfit; an empty list when the suggestions aren't available
export async function completeTheLook(productIds: string[]): Promise<LookItem[]> {
  const res = await fetch("/api/tryon/look", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ product_ids: productIds }),
  });
  return res.ok ? (await res.json()).items : [];
}

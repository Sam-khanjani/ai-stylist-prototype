export type Body = { heightCm: number; weightKg: number | null };
export type TryOnResult = { size: string | null; image: string | null };

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

// Size from the photo and body; with a product also the try-on image (a data URL, not stored anywhere)
export async function tryOn(photoId: string, productId: string | null, body: Body): Promise<TryOnResult> {
  const res = await fetch("/api/tryon", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ photo_id: photoId, product_id: productId, height_cm: body.heightCm, weight_kg: body.weightKg }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The try-on didn't work. Please try again.");
  return data;
}

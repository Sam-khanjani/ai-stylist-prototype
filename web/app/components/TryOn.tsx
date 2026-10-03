"use client";

import { useEffect, useRef, useState } from "react";
import ProductCard from "./ProductCard";
import { formatPrice, type Card } from "@/lib/card";
import { checkPhoto, preloadPose, type Body } from "@/lib/pose";
import { deletePhoto, sizeOptions, tryOn, uploadPhoto, type Fit, type SizeAdvice } from "@/lib/tryon";

const MAX_PHOTO_MB = 20;
const HEIGHT = { min: 140, max: 220 };
const WEIGHT = { min: 40, max: 200 };
const BUSY_TEXT = {
  check: "Checking your photo…",
  upload: "Uploading your photo…",
  size: "Estimating your size…",
  render: "Dressing you in",
};
const JACKETS = sizeOptions(10);
const TROUSERS = sizeOptions(16);

// Catalog grid plus the try-on panel: pick a product from the grid, upload a photo in the panel
export default function TryOn({ products }: { products: Card[] }) {
  const [product, setProduct] = useState<Card | null>(null);
  const [consent, setConsent] = useState(false);
  const [height, setHeight] = useState("");
  const [weight, setWeight] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoId, setPhotoId] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [fit, setFit] = useState<Fit>("regular");
  const [body, setBody] = useState<Body | null>(null);
  const [size, setSize] = useState<SizeAdvice | null>(null);
  const [chosen, setChosen] = useState<{ jacket?: string; trousers?: string }>({});
  const [result, setResult] = useState<string | null>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const [busy, setBusy] = useState<keyof typeof BUSY_TEXT | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const panel = useRef<HTMLElement>(null);

  const heightCm = Number(height);
  const weightKg = Number(weight) || null;
  const heightOk = heightCm >= HEIGHT.min && heightCm <= HEIGHT.max;
  const weightOk = !weight || (weightKg! >= WEIGHT.min && weightKg! <= WEIGHT.max);
  const bodyOk = heightOk && weightOk;

  useEffect(() => {
    if (!photo) return setPreview(null);
    const url = URL.createObjectURL(photo);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  async function work(kind: keyof typeof BUSY_TEXT, task: () => Promise<void>) {
    setBusy(kind);
    setError(null);
    try {
      await task();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function run(kind: "size" | "render", id = photoId, measured = body) {
    if (!bodyOk) return;
    await work(kind, async () => {
      const r = await tryOn(id, kind === "render" ? product!.id : null, { heightCm, weightKg, fit }, measured);
      if (r.size) {
        setSize(r.size);
        setChosen({});
      }
      if (r.image) {
        setResult(r.image);
        setShowOriginal(false);
      }
    });
  }

  async function choosePhoto(file: File | undefined) {
    if (!file) return;
    if (!file.type.startsWith("image/")) return setError("Please choose an image file.");
    if (file.size > MAX_PHOTO_MB * 1024 * 1024) return setError(`Please choose a photo under ${MAX_PHOTO_MB} MB.`);
    setPhoto(file);
    setSize(null);
    setResult(null);
    setWarnings([]);
    let id: string | null = null;
    let measured: Body | null = null;
    await work("check", async () => {
      const checked = await checkPhoto(file);
      if ("reason" in checked) throw new Error(checked.reason);
      measured = checked.body;
      setWarnings(checked.warnings);
      setBusy("upload");
      id = await uploadPhoto(file);
      setPhotoId(id);
      setBody(measured);
    });
    if (!id) return setPhoto(null);
    if (measured || weightKg) await run("size", id, measured); // otherwise the size needs a weight first
  }

  function removePhoto() {
    if (photoId) deletePhoto(photoId); // the bucket would delete it within a day anyway
    setPhoto(null);
    setPhotoId(null);
    setBody(null);
    setSize(null);
    setWarnings([]);
    setResult(null);
    setError(null);
  }

  function pick(p: Card) {
    setProduct(p);
    setResult(null);
    // On small screens the panel sits above the grid, so bring it into view
    if (window.matchMedia("(max-width: 1023px)").matches) panel.current?.scrollIntoView({ behavior: "smooth" });
  }

  const shown = result && !showOriginal ? result : preview;
  const canUpload = consent && bodyOk;

  return (
    <div className="flex flex-col gap-8 lg:flex-row lg:items-start">
      <ul className="grid flex-1 grid-cols-2 gap-x-2 gap-y-10 md:grid-cols-3 lg:gap-y-16">
        {products.map((p) => (
          <li key={p.id}>
            <ProductCard product={p} />
            <button
              onClick={() => pick(p)}
              className={`mx-1 mt-2 rounded-md border px-3 py-1 text-xs ${
                product?.id === p.id ? "border-gray-800 bg-gray-800 text-white" : "border-border hover:border-gray-600"
              }`}
            >
              {product?.id === p.id ? "Selected for try-on" : "Try it on"}
            </button>
          </li>
        ))}
      </ul>

      <aside
        ref={panel}
        className="order-first shrink-0 scroll-mt-16 space-y-4 rounded-md border border-border p-4 lg:sticky lg:top-16 lg:order-last lg:w-96"
      >
        <div>
          <h2 className="text-lg font-medium tracking-heading">Try it on</h2>
          <p className="text-sm text-text-secondary">Add your height and a photo to get your size, then see yourself in any product.</p>
        </div>

        <div className="flex gap-3">
          <NumberField label="Height (cm)" value={height} onChange={setHeight} range={HEIGHT} invalid={!!height && !heightOk} />
          <NumberField label="Weight (kg)" value={weight} onChange={setWeight} range={WEIGHT} invalid={!weightOk} />
          <label className="flex-1 text-xs text-text-secondary">
            Fit
            <select
              value={fit}
              onChange={(e) => setFit(e.target.value as Fit)}
              className="mt-1 block w-full rounded-md border border-border px-1 py-1 text-sm text-text"
            >
              <option value="slim">Slim</option>
              <option value="regular">Regular</option>
              <option value="relaxed">Relaxed</option>
            </select>
          </label>
        </div>

        {!photo ? (
          <>
            <label className="flex items-start gap-2 text-xs text-text-secondary">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => {
                  setConsent(e.target.checked);
                  // Start the photo check download now, so it's ready when the photo is picked
                  if (e.target.checked) preloadPose().catch(() => {});
                }}
                className="mt-0.5"
              />
              <span>
                I agree that my photo is used only to suggest a size and create the try-on image. It is stored privately,
                never shared, and deleted automatically after one day, or right away with “Delete photo”.
              </span>
            </label>
            <label
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (canUpload) choosePhoto(e.dataTransfer.files[0]);
              }}
              className={`flex aspect-3/4 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-gray-500 bg-gray-100 p-6 text-center text-sm ${
                canUpload ? "cursor-pointer hover:border-gray-700" : "opacity-50"
              }`}
            >
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                disabled={!canUpload}
                onChange={(e) => choosePhoto(e.target.files?.[0])}
                className="sr-only"
              />
              <span className="font-medium">Upload a full-body front photo</span>
              <span className="text-xs text-text-secondary">
                {!heightOk
                  ? "Enter your height first."
                  : !consent
                    ? "Tick the box above first."
                    : "Stand straight facing the camera, whole body in frame, arms slightly away from your sides, fitted clothes, plain background. At least 512 × 1024 px."}
              </span>
            </label>
          </>
        ) : (
          <div className="relative aspect-3/4 overflow-hidden rounded-md bg-surface">
            {shown && <img src={shown} alt={shown === result ? "Try-on result" : "Your photo"} className="h-full w-full object-contain" />}
            {busy && (
              <div className="absolute inset-0 flex items-center justify-center bg-white/70 px-6 text-center text-sm">
                {busy === "render" ? `${BUSY_TEXT.render} ${product?.name}…` : BUSY_TEXT[busy]}
              </div>
            )}
            <div className="absolute inset-x-2 top-2 flex justify-between text-xs">
              {result ? (
                <button onClick={() => setShowOriginal(!showOriginal)} className="rounded bg-white/90 px-2 py-1">
                  {showOriginal ? "Show try-on" : "Show my photo"}
                </button>
              ) : (
                <span />
              )}
              <button
                onClick={removePhoto}
                disabled={busy === "check" || busy === "upload"}
                className="rounded bg-white/90 px-2 py-1"
              >
                Delete photo
              </button>
            </div>
          </div>
        )}

        {/* Right under the photo, so the user sees why a photo was rejected or what could be better */}
        {warnings.map((w) => (
          <p key={w} className="flex items-start gap-1.5 text-xs">
            <span aria-hidden className="flex size-4 shrink-0 items-center justify-center rounded-full bg-warning text-[10px] text-gray-900">
              !
            </span>
            {w}
          </p>
        ))}
        {error && (
          <p className="flex items-start gap-1.5 text-xs text-text-secondary">
            <span aria-hidden className="flex size-4 shrink-0 items-center justify-center rounded-full bg-gray-300 text-[10px]">
              i
            </span>
            {error}
          </p>
        )}

        <div className="space-y-3 border-t border-border pt-4">
          {size && <SizeCard advice={size} chosen={chosen} onChoose={(c) => setChosen({ ...chosen, ...c })} />}
          <button
            onClick={() => run("size")}
            disabled={!bodyOk || !!busy || (!body && !weightKg)}
            className="rounded-md border border-border px-3 py-1.5 text-xs hover:border-gray-600 disabled:opacity-40"
          >
            {busy === "size"
              ? "Estimating…"
              : body || photoId
                ? size
                  ? "Update my size"
                  : "Get my size"
                : "Get my size without a photo"}
          </button>
          {!body && !weightKg && (
            <p className="text-xs text-text-secondary">
              {photoId ? "Add your weight to get your size." : "No photo? Enter your height and weight to get a size."}
            </p>
          )}
        </div>

        <div className="flex items-center gap-3 border-t border-border pt-4">
          {product ? (
            <>
              {product.image && <img src={product.image} alt="" className="h-16 w-14 bg-surface object-contain mix-blend-multiply" />}
              <div className="min-w-0 flex-1 text-sm">
                <p className="truncate font-medium">{product.name}</p>
                <p className="text-text-secondary">{formatPrice(product.price, product.currency)}</p>
              </div>
              <button
                onClick={() => {
                  setProduct(null);
                  setResult(null);
                }}
                className="text-xs text-text-secondary hover:text-text"
              >
                Clear
              </button>
            </>
          ) : (
            <p className="text-sm text-text-secondary">Pick a product with “Try it on” in the catalog.</p>
          )}
        </div>

        <button
          onClick={() => run("render")}
          disabled={!photoId || !product || !bodyOk || !!busy}
          className="w-full rounded-md bg-gray-800 py-2 text-sm font-medium text-white hover:bg-gray-900 disabled:opacity-40"
        >
          {!photoId ? "Upload a photo first" : !product ? "Pick a product first" : "Try it on"}
        </button>
      </aside>
    </div>
  );
}

// Recommended sizes, which the user can change; changes stay in this browser tab
function SizeCard({
  advice,
  chosen,
  onChoose,
}: {
  advice: SizeAdvice;
  chosen: { jacket?: string; trousers?: string };
  onChoose: (c: { jacket?: string; trousers?: string }) => void;
}) {
  const m = advice.measurements;
  const measured = [
    m.chest && `chest ${m.chest}`,
    m.waist && `waist ${m.waist}`,
    m.arm && `arm ${m.arm}`,
    m.inseam && `inseam ${m.inseam}`,
  ].filter(Boolean);
  return (
    <div className="space-y-3">
      <p className="text-sm font-medium">Your size</p>
      <div className="grid grid-cols-2 gap-3">
        <SizePick title="Jacket" options={JACKETS} recommended={advice.jacket.label} value={chosen.jacket} onChange={(jacket) => onChoose({ jacket })} />
        <SizePick title="Trousers" options={TROUSERS} recommended={advice.trousers.label} value={chosen.trousers} onChange={(trousers) => onChoose({ trousers })} />
      </div>
      {advice.reasons.length > 0 && <p className="text-xs text-text-secondary">{advice.reasons.join(" ")}</p>}
      <ul className="list-disc space-y-1 pl-4 text-xs">
        {advice.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
      {measured.length > 0 && <p className="text-xs text-text-secondary">Estimated (cm): {measured.join(" · ")}</p>}
    </div>
  );
}

function SizePick({
  title,
  options,
  recommended,
  value,
  onChange,
}: {
  title: string;
  options: { label: string }[];
  recommended: string;
  value?: string;
  onChange: (label: string | undefined) => void;
}) {
  const changed = value && value !== recommended;
  return (
    <label className="text-xs text-text-secondary">
      {title}
      <select
        value={value ?? recommended}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 block w-full rounded-md border border-border px-1 py-1 text-sm font-medium text-text"
      >
        {options.map((o) => (
          <option key={o.label} value={o.label}>
            {o.label}
          </option>
        ))}
      </select>
      {changed ? (
        <button type="button" onClick={() => onChange(undefined)} className="mt-1 underline">
          Changed by you · reset
        </button>
      ) : (
        <span className="mt-1 block">Recommended</span>
      )}
    </label>
  );
}

function NumberField({
  label,
  value,
  onChange,
  range,
  invalid,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  range: { min: number; max: number };
  invalid: boolean;
}) {
  return (
    <label className="flex-1 text-xs text-text-secondary">
      {label}
      <input
        type="number"
        inputMode="numeric"
        min={range.min}
        max={range.max}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`mt-1 block w-full rounded-md border px-2 py-1 text-sm text-text ${invalid ? "border-critical" : "border-border"}`}
      />
      {invalid && <span className="mt-1 block">Between {range.min} and {range.max}</span>}
    </label>
  );
}

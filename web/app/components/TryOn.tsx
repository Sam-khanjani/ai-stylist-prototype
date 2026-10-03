"use client";

import { useEffect, useRef, useState } from "react";
import ProductCard from "./ProductCard";
import { formatPrice, type Card } from "@/lib/card";
import { checkPhoto, preloadPose, type Body } from "@/lib/pose";
import {
  completeTheLook,
  sizeOptions,
  tryOn,
  preparePhoto,
  type Fit,
  type LookItem,
  type SizeAdvice,
} from "@/lib/tryon";

const MAX_PHOTO_MB = 20;
const HEIGHT = { min: 140, max: 220 };
const WEIGHT = { min: 40, max: 200 };
const BUSY_TEXT = {
  check: "Checking your photo…",
  size: "Estimating your size…",
  render: "Dressing you in",
};
// Same as the api: up to two items, no accessories (the try-on model doesn't support them), and no two items in the
// same slot (a suit is jacket and trousers; a coat over a suit or jacket can't be shown)
const MAX_ITEMS = 2;
const SLOTS: Record<string, string[]> = {
  trousers: ["legs"],
  shorts: ["legs"],
  shoes: ["feet"],
  shirts: ["shirt"],
  knitwear: ["knit"],
  waistcoats: ["vest"],
  suits: ["legs", "outer"],
  jackets: ["outer"],
  coats: ["outer"],
};
const sectionOf = (c: Card) => new URL(c.url).pathname.split("/")[3];
const clashes = (a: Card, b: Card) => SLOTS[sectionOf(a)].some((slot) => SLOTS[sectionOf(b)].includes(slot));
const JACKETS = sizeOptions(10);
const TROUSERS = sizeOptions(16);

// Catalog grid plus the try-on panel: pick up to two items from the grid, add a photo in the panel
export default function TryOn({ products }: { products: Card[] }) {
  const [outfit, setOutfit] = useState<Card[]>([]);
  const [look, setLook] = useState<LookItem[] | null>([]); // null while loading
  const [consent, setConsent] = useState(false);
  const [height, setHeight] = useState("");
  const [weight, setWeight] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoData, setPhotoData] = useState<string | null>(null); // the prepared JPEG, only in this browser
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

  // "Complete the look" follows the outfit; a stale answer for an older outfit is ignored
  const outfitKey = outfit.map((p) => p.id).join(",");
  useEffect(() => {
    if (!outfitKey) return setLook([]);
    let stale = false;
    setLook(null);
    completeTheLook(outfitKey.split(","))
      .catch(() => [])
      .then((items) => !stale && setLook(items));
    return () => {
      stale = true;
    };
  }, [outfitKey]);

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

  async function run(kind: "size" | "render", data = photoData, measured = body) {
    if (!bodyOk) return;
    await work(kind, async () => {
      const r = await tryOn(data, kind === "render" ? outfit.map((p) => p.id) : [], { heightCm, weightKg, fit }, measured);
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
    let data: string | null = null;
    let measured: Body | null = null;
    await work("check", async () => {
      const checked = await checkPhoto(file);
      if ("reason" in checked) throw new Error(checked.reason);
      measured = checked.body;
      setWarnings(checked.warnings);
      data = await preparePhoto(file);
      setPhotoData(data);
      setBody(measured);
    });
    if (!data) return setPhoto(null);
    if (measured || weightKg) await run("size", data, measured); // otherwise the size needs a weight first
  }

  function removePhoto() {
    setPhoto(null);
    setPhotoData(null);
    setBody(null);
    setSize(null);
    setWarnings([]);
    setResult(null);
    setError(null);
  }

  // Adds or removes an item; a second item of the same kind (e.g. trousers) replaces the first
  function toggle(p: Card) {
    setResult(null);
    if (outfit.some((o) => o.id === p.id)) return setOutfit(outfit.filter((o) => o.id !== p.id));
    setOutfit([...outfit.filter((o) => sectionOf(o) !== sectionOf(p)), p].slice(-MAX_ITEMS));
    // On small screens the panel sits above the grid, so bring it into view
    if (window.matchMedia("(max-width: 1023px)").matches) panel.current?.scrollIntoView({ behavior: "smooth" });
  }

  // "Add to try-on" under a product card, with the reason when it can't be added
  function tryButton(p: Card) {
    if (!SLOTS[sectionOf(p)]) return null; // accessories can't be tried on
    const added = outfit.some((o) => o.id === p.id);
    const swaps = outfit.some((o) => sectionOf(o) === sectionOf(p)); // same kind: replaces it
    const clash = !added && !swaps && outfit.find((o) => clashes(o, p));
    const full = !added && !swaps && outfit.length >= MAX_ITEMS;
    return (
      <button
        onClick={() => toggle(p)}
        disabled={full || !!clash}
        title={clash ? `Can't be worn together with ${clash.name}` : undefined}
        className={`mx-1 mt-2 rounded-md border px-3 py-1 text-xs disabled:opacity-40 ${
          added ? "border-gray-800 bg-gray-800 text-white" : "border-border hover:border-gray-600"
        }`}
      >
        {added
          ? "✓ In your try-on"
          : clash
            ? `Doesn't go with your ${clash.name}`
            : full
              ? `Try-on is full (${MAX_ITEMS} items)`
              : swaps
                ? "Swap into try-on"
                : "Add to try-on"}
      </button>
    );
  }

  const shown = result && !showOriginal ? result : preview;
  const canUpload = consent && bodyOk;

  return (
    <div className="flex flex-col gap-8 lg:flex-row lg:items-start">
      <ul className="grid flex-1 grid-cols-2 gap-x-2 gap-y-10 md:grid-cols-3 lg:gap-y-16">
        {products.map((p) => (
          <li key={p.id}>
            <ProductCard product={p} />
            {tryButton(p)}
          </li>
        ))}
      </ul>

      <aside
        ref={panel}
        className="order-first shrink-0 scroll-mt-16 space-y-4 rounded-md border border-border p-4 lg:sticky lg:top-16 lg:order-last lg:max-h-[calc(100dvh-5rem)] lg:w-96 lg:overflow-y-auto"
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
                I agree that my photo is used only to suggest my size and create the try-on image. It is never saved
                anywhere: my size is worked out in this browser, and for a try-on the photo is sent encrypted to Google&apos;s
                AI service in the EU only to create the image, then discarded.
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
              <span className="font-medium">Add a full-body front photo</span>
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
                {busy === "render" ? `${BUSY_TEXT.render} ${outfit.map((p) => p.name).join(" and ")}…` : BUSY_TEXT[busy]}
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
                disabled={busy === "check"}
                className="rounded bg-white/90 px-2 py-1"
              >
                Remove photo
              </button>
            </div>
          </div>
        )}

        {photo && (
          <p className="text-xs text-text-secondary">
            Your photo isn&apos;t saved anywhere: it stays in this browser and is only sent, encrypted, to create a try-on
            image, then discarded.
          </p>
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
              : body || photoData
                ? size
                  ? "Update my size"
                  : "Get my size"
                : "Get my size without a photo"}
          </button>
          {!body && !weightKg && (
            <p className="text-xs text-text-secondary">
              {photoData ? "Add your weight to get your size." : "No photo? Enter your height and weight to get a size."}
            </p>
          )}
        </div>

        <div className="space-y-2 border-t border-border pt-4">
          {outfit.map((p) => (
            <div key={p.id} className="flex items-center gap-3">
              {p.image && <img src={p.image} alt="" className="h-16 w-14 bg-surface object-contain mix-blend-multiply" />}
              <div className="min-w-0 flex-1 text-sm">
                <p className="truncate font-medium">{p.name}</p>
                <p className="text-text-secondary">{formatPrice(p.price, p.currency)}</p>
              </div>
              <button onClick={() => toggle(p)} className="text-xs text-text-secondary hover:text-text">
                Remove
              </button>
            </div>
          ))}
          {outfit.length < MAX_ITEMS && (
            <p className="text-sm text-text-secondary">
              {outfit.length
                ? "Add one more item to try them together, e.g. trousers with a knit."
                : `Add up to ${MAX_ITEMS} items with “Add to try-on” in the catalog.`}
            </p>
          )}
        </div>

        {outfit.length > 0 && look?.length !== 0 && (
          <div className="space-y-2 border-t border-border pt-4">
            <p className="text-sm font-medium">Complete the look</p>
            {look === null ? (
              <p className="text-xs text-text-secondary">Finding pieces that go with it…</p>
            ) : (
              <ul className="grid grid-cols-3 gap-2">
                {look.map((p) => (
                  <li key={p.id} className="text-xs">
                    <ProductCard product={p} />
                    {p.reason && <p className="mt-1 px-1 text-text-secondary">{p.reason}</p>}
                    {tryButton(p)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* The panel scrolls on its own on desktop (it's sticky); keep the main action in view at its bottom */}
        <div className="bg-background lg:sticky lg:-bottom-4 lg:-mx-4 lg:-mb-4 lg:px-4 lg:pt-3 lg:pb-4">
          <button
            onClick={() => run("render")}
            disabled={!photoData || !outfit.length || !bodyOk || !!busy}
            className="w-full rounded-md bg-gray-800 py-2 text-sm font-medium text-white hover:bg-gray-900 disabled:opacity-40"
          >
            {!photoData ? "Add a photo first" : !outfit.length ? "Add an item first" : "Try it on"}
          </button>
        </div>
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

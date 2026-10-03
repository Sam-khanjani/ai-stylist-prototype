// Photo checks with MediaPipe Pose, run in the browser. Only unusable photos are rejected (and never uploaded);
// for the rest the user gets warnings, and photos that can't be measured fall back to height and weight for the size.
import type { MPMask, NormalizedLandmark, PoseLandmarker } from "@mediapipe/tasks-vision";

const WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1/wasm"; // same version as package.json
const MODEL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task";

const MIN_SIDES = [512, 1024]; // short and long side in px; smaller gets a warning (the try-on image is as small)
const TINY = 256; // shorter side in px below which the photo is rejected
const VISIBLE = 0.5; // landmark visibility needed to count a body part as seen
const MIN_BODY = 0.5; // nose to heels, as a share of the photo height
const MIN_SHOULDERS = 0.4; // shoulder width / torso length; lower means turned sideways
const MAX_CHEST = 1.3; // body width at the chest / shoulder joints; wider means the arms touch the body
// Down the torso from the shoulder joints to the hip joints
const CHEST_AT = 0.35;
const WAIST_AT = 0.75;

// Measurements for the size advice, as fractions of the eye-to-heel distance; the api scales them by the height
export type Body = { shoulder: number; chest: number; waist: number; arm: number; leg: number };

// Landmark indices: https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker#pose_landmarker_model
// Without the upper body there's nothing to try a jacket on; without the legs we can't measure (no heels for scale)
const UPPER: [string, number[]][] = [
  ["face", [0, 2, 5]],
  ["shoulders", [11, 12]],
  ["hips", [23, 24]],
];
const LOWER: [string, number[]][] = [
  ["knees", [25, 26]],
  ["feet", [27, 28, 29, 30]],
];

export type Checked = { reason: string } | { body: Body | null; warnings: string[] };

let landmarker: Promise<PoseLandmarker> | null = null;

// Called when the user shows interest in try-on, so the ~6 MB download is done by the time a photo is picked
export function preloadPose() {
  landmarker ??= import("@mediapipe/tasks-vision")
    .then(async ({ FilesetResolver, PoseLandmarker }) =>
      PoseLandmarker.createFromOptions(await FilesetResolver.forVisionTasks(WASM), {
        baseOptions: { modelAssetPath: MODEL },
        runningMode: "IMAGE",
        numPoses: 2, // to notice a second person
        outputSegmentationMasks: true, // body outline, for chest and waist width
      }),
    )
    .catch(() => {
      landmarker = null; // try again next time
      throw new Error("We couldn't load the photo check. Please check your connection and try again.");
    });
  return landmarker;
}

const list = (items: string[]) => items.join(", ").replace(/, ([^,]*)$/, " and $1");

// Width of the body outline along one row, going out from (x, y) until the mask ends.
// x and y are fractions of the image; the result is in image pixels (the mask can have its own size).
function outlineWidth(mask: Float32Array, cols: number, rows: number, x: number, y: number, width: number) {
  const r = Math.min(rows - 1, Math.round(y * rows)) * cols;
  let left = Math.min(cols - 1, Math.round(x * cols));
  let right = left;
  if (!(mask[r + left] > 0.5)) return 0;
  while (left > 0 && mask[r + left - 1] > 0.5) left--;
  while (right < cols - 1 && mask[r + right + 1] > 0.5) right++;
  return ((right - left + 1) / cols) * width;
}

// Rejects only photos the try-on can't use. Anything else passes with warnings; the body measurements are
// left out (null) when the photo can't be measured reliably, and the size then comes from height and weight.
export async function checkPhoto(file: File): Promise<Checked> {
  const image = await createImageBitmap(file);
  let masks: MPMask[] = [];
  try {
    const { width, height } = image;
    if (Math.min(width, height) < TINY)
      return { reason: `This photo is too small (${width} × ${height} px). Please use a larger photo.` };
    const warnings: string[] = [];
    let measurable = true;
    const warn = (text: string, spoilsMeasuring = true) => {
      warnings.push(text);
      if (spoilsMeasuring) measurable = false;
    };
    if (Math.min(width, height) < MIN_SIDES[0] || Math.max(width, height) < MIN_SIDES[1])
      warn(`This photo is small (${width} × ${height} px), so the try-on image will be small too.`, false);

    const result = (await preloadPose()).detect(image);
    masks = result.segmentationMasks ?? [];
    const poses = result.landmarks;
    if (poses.length === 0) return { reason: "We couldn't find a person in this photo. Please use a photo of yourself." };
    if (poses.length > 1) return { reason: "There's more than one person in this photo. Please use a photo with only you in it." };
    const p = poses[0];

    const seen = (l: NormalizedLandmark) => l.visibility > VISIBLE && l.x >= 0 && l.x <= 1 && l.y >= 0 && l.y <= 1;
    const missing = (parts: [string, number[]][]) =>
      parts.filter(([, ids]) => !ids.every((i) => seen(p[i]))).map(([name]) => name);
    const upper = missing(UPPER);
    if (upper.length) return { reason: `We can't see your ${list(upper)}. Please use a photo of you facing the camera.` };
    const lower = missing(LOWER);
    if (lower.length) warn(`We can't see your ${list(lower)}, so we can't measure you from this photo.`);

    // In pixels, since x and y are normalized to different sides
    const px = (i: number) => ({ x: p[i].x * width, y: p[i].y * height });
    const mid = (a: number, b: number) => ({ x: (px(a).x + px(b).x) / 2, y: (px(a).y + px(b).y) / 2 });
    const dist = (a: number, b: number) => Math.hypot(px(a).x - px(b).x, px(a).y - px(b).y);
    const [shoulders, hips, eyes, heels] = [mid(11, 12), mid(23, 24), mid(2, 5), mid(29, 30)];
    if (shoulders.y >= hips.y) return { reason: "Please use a photo where you're standing upright." };
    if (!lower.length && heels.y - px(0).y < MIN_BODY * height)
      warn("You're quite far from the camera. A closer photo gives a sharper try-on and a more precise size.", false);
    const shoulderWidth = Math.abs(px(11).x - px(12).x);
    if (shoulderWidth < MIN_SHOULDERS * (hips.y - shoulders.y))
      warn("You're turned to the side, so we can't measure you accurately. Facing the camera works best.");

    // Chest and waist width from the body outline, on rows down the torso
    const mask = masks[0];
    const data = mask?.getAsFloat32Array();
    const across = (share: number) => {
      if (!mask || !data) return 0;
      const y = shoulders.y + share * (hips.y - shoulders.y);
      const x = shoulders.x + share * (hips.x - shoulders.x);
      return outlineWidth(data, mask.width, mask.height, x / width, y / height, width);
    };
    const [chest, waist] = [across(CHEST_AT), across(WAIST_AT)];
    if (!chest || !waist) warn("We couldn't see your body outline clearly. A plain background works best.");
    else if (chest > MAX_CHEST * shoulderWidth)
      warn("Your arms are close to your body, so we can't measure your chest and waist. Hold them slightly away for a better size.");
    if (!measurable) warnings.push("Your size will be based on your height and weight instead.");

    const unit = heels.y - eyes.y; // the api turns this into cm with the stated height
    const body = measurable
      ? {
          shoulder: shoulderWidth / unit,
          chest: chest / unit,
          waist: waist / unit,
          arm: (dist(11, 13) + dist(13, 15) + dist(12, 14) + dist(14, 16)) / 2 / unit,
          leg: (heels.y - hips.y) / unit,
        }
      : null;
    return { body, warnings };
  } finally {
    masks.forEach((m) => m.close());
    image.close();
  }
}

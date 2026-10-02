// Photo checks with MediaPipe Pose, run in the browser: a rejected photo is never uploaded
import type { NormalizedLandmark, PoseLandmarker } from "@mediapipe/tasks-vision";

const WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1/wasm"; // same version as package.json
const MODEL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task";

const MIN_SIDES = [512, 1024]; // short and long side in px; the try-on image has the photo's resolution
const VISIBLE = 0.5; // landmark visibility needed to count a body part as seen
const MIN_BODY = 0.5; // nose to ankles, as a share of the photo height
const MIN_SHOULDERS = 0.4; // shoulder width / torso length; lower means turned sideways

// Landmark indices: https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker#pose_landmarker_model
const PARTS: [string, number[]][] = [
  ["face", [0, 2, 5]],
  ["shoulders", [11, 12]],
  ["hips", [23, 24]],
  ["knees", [25, 26]],
  ["feet", [27, 28]],
];

let landmarker: Promise<PoseLandmarker> | null = null;

// Called when the user shows interest in try-on, so the ~6 MB download is done by the time a photo is picked
export function preloadPose() {
  landmarker ??= import("@mediapipe/tasks-vision")
    .then(async ({ FilesetResolver, PoseLandmarker }) =>
      PoseLandmarker.createFromOptions(await FilesetResolver.forVisionTasks(WASM), {
        baseOptions: { modelAssetPath: MODEL },
        runningMode: "IMAGE",
        numPoses: 2, // to notice a second person
      }),
    )
    .catch(() => {
      landmarker = null; // try again next time
      throw new Error("We couldn't load the photo check. Please check your connection and try again.");
    });
  return landmarker;
}

const list = (items: string[]) => items.join(", ").replace(/, ([^,]*)$/, " and $1");

// Returns why the photo can't be used, or null when it's fine
export async function checkPhoto(file: File): Promise<string | null> {
  const image = await createImageBitmap(file);
  try {
    const { width, height } = image;
    if (Math.min(width, height) < MIN_SIDES[0] || Math.max(width, height) < MIN_SIDES[1])
      return `This photo is too small (${width} × ${height} px). Please use one of at least ${MIN_SIDES[0]} × ${MIN_SIDES[1]} px.`;

    const poses = (await preloadPose()).detect(image).landmarks;
    if (poses.length === 0) return "We couldn't find a person in this photo. Please use a full-body photo of yourself.";
    if (poses.length > 1) return "There's more than one person in this photo. Please use a photo with only you in it.";
    const p = poses[0];

    const seen = (l: NormalizedLandmark) => l.visibility > VISIBLE && l.x >= 0 && l.x <= 1 && l.y >= 0 && l.y <= 1;
    const missing = PARTS.filter(([, ids]) => !ids.every((i) => seen(p[i]))).map(([name]) => name);
    if (missing.length) return `We can't see your ${list(missing)}. Please face the camera with your whole body in the photo.`;

    // In pixels, since x and y are normalized to different sides
    const mid = (a: number, b: number) => ({ x: ((p[a].x + p[b].x) / 2) * width, y: ((p[a].y + p[b].y) / 2) * height });
    const [shoulders, hips, ankles] = [mid(11, 12), mid(23, 24), mid(27, 28)];
    if (!(shoulders.y < hips.y && hips.y < ankles.y)) return "Please stand upright in the photo.";
    if (ankles.y - p[0].y * height < MIN_BODY * height)
      return "You're too far from the camera. Please step closer so your body fills most of the photo.";
    if (Math.abs(p[11].x - p[12].x) * width < MIN_SHOULDERS * (hips.y - shoulders.y))
      return "You're turned sideways. Please face the camera straight on.";
    return null;
  } finally {
    image.close();
  }
}

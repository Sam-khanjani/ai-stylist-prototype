"""Body measurements and size advice.

Measurements come from the photo's pose landmarks (as fractions of the eye-to-heel distance, measured in the
browser) scaled by the stated height, and/or from height and weight alone.

Sizes: the public product pages list EU sizes in three lengths that follow the standard EU (German) system:
regular N = half the chest in cm (44-60), short = N/2 (EU 22-30), long = 2N-2 (EU 90-118), and US = N-10 for
jackets. Trouser sizes use the same EU numbers with the US waist in inches = N-16. The fit notes follow the public
"The Perfect Fit: Jackets" and "Trousers Fit Guide" pages.
"""
from typing import Literal

from pydantic import BaseModel, Field

Fit = Literal["slim", "regular", "relaxed"]

EYE_HEIGHT = 0.936  # eye height / stature (anthropometric average); the landmarks have no top of the head
CIRCUMFERENCE = 3.0  # front width -> circumference, chest and waist (torso is closer to a rounded box than an ellipse)
SHOULDER_EDGE = 1.12  # landmarks sit on the shoulder joints, inside the shoulder edge
CROTCH_DROP = 0.065  # hip joint sits this share of the height above the crotch
ARM_SHARE = 0.332  # shoulder to wrist / stature
# Height a regular size is cut for; short sizes fit ~6 cm shorter, long ~6 cm taller
REGULAR_HEIGHT = {44: 168, 46: 171, 48: 174, 50: 177, 52: 180, 54: 182, 56: 184, 58: 186, 60: 188}
LENGTH_STEP = 6


class Body(BaseModel):
    """From the photo, in units of the eye-to-heel distance."""

    shoulder: float = Field(gt=0, lt=1)
    chest: float = Field(gt=0, lt=1)
    waist: float = Field(gt=0, lt=1)
    arm: float = Field(gt=0, lt=1)
    leg: float = Field(gt=0, lt=1)


def from_photo(body: Body, height: int) -> dict:
    scale = EYE_HEIGHT * height
    return {
        "shoulder": body.shoulder * scale * SHOULDER_EDGE,
        "chest": body.chest * scale * CIRCUMFERENCE,
        "waist": body.waist * scale * CIRCUMFERENCE,
        "arm": body.arm * scale,
        "inseam": body.leg * scale - CROTCH_DROP * height,
    }


def from_weight(height: int, weight: int) -> dict:
    """Rough average-build estimate; good enough for a size, not for tailoring."""
    return {"chest": 0.62 * weight + 0.05 * height + 40, "waist": 0.9 * weight + 16 - 0.2 * (height - 178)}


def even(x: float, low: int, high: int) -> int:
    return max(low, min(high, 2 * round(x / 2)))


def length_of(height: int, n: int) -> str:
    gap = height - REGULAR_HEIGHT[n]
    if gap < -LENGTH_STEP:
        return "short"
    if gap > LENGTH_STEP and n > 44:  # no long size under EU 90 (= 46)
        return "long"
    return "regular"


def label(n: int, length: str, us: int) -> dict:
    eu, suffix = {"short": (n // 2, "S"), "regular": (n, ""), "long": (2 * n - 2, "L")}[length]
    return {"eu": eu, "length": length, "label": f"EU {eu} / US {us}{suffix}"}


def advise(height: int, weight: int | None, fit: Fit, body: Body | None) -> dict:
    photo = from_photo(body, height) if body else None
    guess = from_weight(height, weight) if weight else None
    if not photo and not guess:
        raise ValueError("a photo or a weight is needed")
    m = dict(photo or guess)
    reasons, notes = [], []
    score = 1.0
    if photo and guess:
        gap = abs(photo["chest"] - guess["chest"])
        # Two independent estimates; averaging them evens out a loose shirt or an odd pose
        m["chest"], m["waist"] = (photo["chest"] + guess["chest"]) / 2, (photo["waist"] + guess["waist"]) / 2
        if gap > 8:
            score -= 0.3
            reasons.append("The photo and your weight point to different sizes.")
    elif photo:
        score -= 0.3
        reasons.append("Adding your weight makes the estimate more reliable.")
    else:
        score -= 0.45
        reasons.append("Based on height and weight only; a photo makes it more precise.")

    lean = {"slim": -2, "regular": 0, "relaxed": 2}[fit]  # between sizes, lean the preferred way
    n = even((m["chest"] + lean) / 2, 44, 60)
    t = even(m["waist"] / 2.54 + 16 + lean / 2, 42, 60)
    if abs(m["chest"] / 2 - n) > 0.75 and n not in (44, 60):
        score -= 0.15
        reasons.append("You're between two jacket sizes.")
        notes.append("Between sizes, let the shoulders decide: if they fit, the waist can be taken in at a store.")
    jacket = label(n, length_of(height, n), n - 10)
    trousers = label(t, jacket["length"] if t >= 44 else "regular", t - 16)  # EU 42 has no short or long

    if photo:
        cut_for = REGULAR_HEIGHT[n] + {"short": -LENGTH_STEP, "regular": 0, "long": LENGTH_STEP}[jacket["length"]]
        sleeve = round(m["arm"] - ARM_SHARE * cut_for)
        if abs(sleeve) >= 2:
            way = "lengthened" if sleeve > 0 else "shortened"
            notes.append(f"Your arms are about {abs(sleeve)} cm {'longer' if sleeve > 0 else 'shorter'} than this "
                         f"size is cut for; the sleeves can be {way} so about 1.5 cm of shirt cuff shows.")
        if m["shoulder"] > m["chest"] * 0.47:
            notes.append("Your shoulders are broad for your chest: if the jacket feels tight there, try one size up.")
        notes.append(f"Trousers arrive with extra length, to be hemmed to your inseam of about {round(m['inseam'])} cm.")
    else:
        notes.append("Trousers arrive with extra length, so they can be hemmed to your leg length in store.")
    if fit == "relaxed":
        notes.append("For a roomier cut, the Relaxed Fit Roma jackets fit wider through the chest and waist.")
    elif fit == "slim":
        notes.append("Tailored Fit Havana and Milano jackets are cut slim through the chest and waist.")

    return {
        "jacket": jacket,
        "trousers": trousers,
        "confidence": "high" if score >= 0.75 else "medium" if score >= 0.5 else "low",
        "reasons": reasons,
        "notes": notes,
        "measurements": {k: round(v) for k, v in m.items()},
    }

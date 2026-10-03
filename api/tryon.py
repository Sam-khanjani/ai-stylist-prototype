"""Try it on: the virtual try-on model.

The photo is never saved: it comes with the try-on request, stays in memory while the models dress it, and is gone
when the response is sent. The try-on image is returned inline and isn't stored either.
"""
import base64
import binascii
import os
from functools import cache

import httpx
from google import genai
from google.genai import types

MAX_BYTES = 10 * 1024 * 1024  # the model's limit per image
MODEL = "virtual-try-on-001"
LOCATION = os.getenv("TRYON_LOCATION", "europe-west4")
# An outer layer (jacket, suit, waistcoat, coat) over anything already tried on: the try-on model repaints the whole
# area the garment covers from its product photo (empty interior included, and the legs for a long coat), so the item
# underneath is lost. That step uses an image model we can instruct instead. LAYER_MODEL="" switches it off. Fallback if 3.1 isn't served: gemini-2.5-flash-image @ europe-west4.
LAYER_MODEL = os.getenv("LAYER_MODEL", "gemini-3.1-flash-image")
LAYER_LOCATION = os.getenv("LAYER_LOCATION", "eu")
OUTER = {"waistcoats", "suits", "jackets", "coats"}
LAYER_PROMPT = (
    "The first image is a photo of a person. The second image is a product photo of this item: {name}. "
    "Edit the first photo so the person wears this item over the clothes they already have on. "
    "Everything they already wear stays exactly as it is wherever the item doesn't cover it: a shirt or sweater "
    "at the collar and in the front opening, trousers and shoes below the hem. "
    "If the item includes trousers, they replace the person's trousers. "
    "Reproduce the item's colour, fabric, pattern and details exactly. "
    "Keep the person's face, hair, body shape, pose, the rest of their clothing and the background unchanged. "
    "Return only the edited photo."
)


class Blocked(Exception):
    """The model returned no image for this product (safety filter or an unusable photo)."""


MAX_ITEMS = 2
# Dressing order, inside out: each item is put on the result of the one before.
# Accessories (ties, belts, socks...) aren't supported by the model.
LAYER = {"trousers": 0, "shorts": 0, "shoes": 1, "shirts": 2, "knitwear": 3, "waistcoats": 4, "suits": 5, "jackets": 5, "coats": 6}
# Where each item sits on the body; two items sharing a slot can't be worn together (a suit is jacket and trousers,
# a coat goes over a suit or jacket, which the model can't show)
SLOTS = {
    "trousers": {"legs"}, "shorts": {"legs"}, "shoes": {"feet"}, "shirts": {"shirt"}, "knitwear": {"knit"},
    "waistcoats": {"vest"}, "suits": {"legs", "outer"}, "jackets": {"outer"}, "coats": {"outer"},
}


def clash(sections: list[str]) -> bool:
    taken = [slot for s in sections for slot in SLOTS[s]]
    return len(taken) != len(set(taken))


def photo_bytes(data_url: str) -> bytes:
    """The photo from the request (a JPEG or PNG data URL), checked before it goes to the model."""
    try:
        data = base64.b64decode(data_url.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("not base64")
    if len(data) > MAX_BYTES or not (data.startswith(b"\xff\xd8") or data.startswith(b"\x89PNG")):
        raise ValueError("not a JPEG or PNG under 10 MB")
    return data


@cache
def client(location: str = LOCATION) -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=os.getenv("GOOGLE_CLOUD_PROJECT", "ai-stylist-proto"),
        location=location,
        http_options=types.HttpOptions(timeout=120_000, retry_options=types.HttpRetryOptions(attempts=3)),
    )


def mime(data: bytes) -> str:
    return "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"  # Gemini may answer in PNG


def jpeg(data: bytes) -> types.Image:
    return types.Image(image_bytes=data, mime_type=mime(data))


def garment(product: dict) -> bytes:
    # The CDN picks WebP/AVIF for f_auto; the models only take JPEG or PNG
    url = product["images"][0].replace("f_auto", "f_jpg")
    return httpx.get(url, timeout=20, follow_redirects=True).raise_for_status().content


# List prices per generated image (USD, October 2026), for the dashboard's cost estimate
PRICE_USD = {"dress": 0.06, "layer": 0.067}


def plan(outfit: list[tuple[str, dict]]) -> list[tuple]:
    """(step, product) per model call, dressing inside out. An outer layer over something already tried on goes to
    the instructable image model; everything else to the try-on model, which takes one product image per request."""
    steps, worn = [], set()
    for section, product in sorted(outfit, key=lambda item: LAYER[item[0]]):
        steps.append((layer if LAYER_MODEL and section in OUTER and worn else dress, product))
        worn.add(section)
    return steps


def cost(outfit: list[tuple[str, dict]]) -> float:
    return sum(PRICE_USD[step.__name__] for step, _ in plan(outfit))


def render(person: bytes, outfit: list[tuple[str, dict]]) -> str:
    """The person wearing the outfit, as a data URL. `outfit` is (section, product) pairs."""
    for step, product in plan(outfit):
        person = step(person, product)
    return f"data:{mime(person)};base64," + base64.b64encode(person).decode()


def dress(person: bytes, product: dict) -> bytes:
    response = client().models.recontext_image(
        model=MODEL,
        source=types.RecontextImageSource(
            person_image=jpeg(person), product_images=[types.ProductImage(product_image=jpeg(garment(product)))]
        ),
        config=types.RecontextImageConfig(
            number_of_images=1, person_generation=types.PersonGeneration.ALLOW_ADULT, output_mime_type="image/jpeg"
        ),
    )
    images = [g.image for g in response.generated_images or [] if g.image and g.image.image_bytes]
    if not images:
        raise Blocked(product["name"])
    return images[0].image_bytes


def layer(person: bytes, product: dict) -> bytes:
    """Puts an open outer layer on, keeping the shirt or knit underneath visible."""
    item = garment(product)
    response = client(LAYER_LOCATION).models.generate_content(
        model=LAYER_MODEL,
        contents=[
            types.Part.from_bytes(data=person, mime_type=mime(person)),
            types.Part.from_bytes(data=item, mime_type=mime(item)),
            LAYER_PROMPT.format(name=product["name"]),
        ],
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    parts = response.parts or []  # empty when the safety filter blocks the answer
    image = next((p.inline_data.data for p in parts if p.inline_data and p.inline_data.data), None)
    if not image:
        raise Blocked(product["name"])
    return image

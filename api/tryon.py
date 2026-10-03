"""Try it on: photo uploads and the virtual try-on model.

The browser uploads straight to the private user-photos bucket with a short-lived signed URL. Each photo sits
under the visitor's id, and the bucket deletes everything after a day. The try-on image is returned inline and
never stored.
"""
import base64
import os
import uuid
from datetime import timedelta
from functools import cache

import google.auth
import httpx
from google import genai
from google.api_core.exceptions import NotFound
from google.auth.transport.requests import Request
from google.cloud import storage
from google.genai import types

BUCKET = os.getenv("PHOTOS_BUCKET", "ai-stylist-proto-user-photos")
MAX_BYTES = 10 * 1024 * 1024  # also the model's limit per image
URL_MINUTES = 10
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


class PhotoMissing(Exception):
    """Never uploaded, deleted, or removed by the bucket's one-day rule."""


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


@cache
def bucket() -> storage.Bucket:
    return storage.Client().bucket(BUCKET)


def blob(visitor_id: str, photo_id: str) -> storage.Blob:
    return bucket().blob(f"{visitor_id}/{photo_id}.jpg")


def upload_url(visitor_id: str) -> dict:
    """A PUT URL for one JPEG of at most MAX_BYTES; the browser must send exactly these headers."""
    photo_id = str(uuid.uuid4())
    headers = {"Content-Type": "image/jpeg", "x-goog-content-length-range": f"0,{MAX_BYTES}"}
    # Cloud Run has no key file, so the URL is signed through the IAM API with an access token.
    # Locally the credentials are a user's, which can't sign: PHOTO_SIGNER names the service account to sign as.
    credentials, _ = google.auth.default()
    credentials.refresh(Request())
    url = blob(visitor_id, photo_id).generate_signed_url(
        version="v4",
        method="PUT",
        expiration=timedelta(minutes=URL_MINUTES),
        headers=headers,
        service_account_email=os.getenv("PHOTO_SIGNER") or credentials.service_account_email,
        access_token=credentials.token,
    )
    return {"photo_id": photo_id, "upload_url": url, "headers": headers}


def delete(visitor_id: str, photo_id: str):
    try:
        blob(visitor_id, photo_id).delete()
    except NotFound:
        pass  # never uploaded, or already removed by the lifecycle rule


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


def render(visitor_id: str, photo_id: str, outfit: list[tuple[str, dict]]) -> str:
    """The visitor wearing the outfit, as a JPEG data URL. `outfit` is (section, product) pairs."""
    try:
        person = blob(visitor_id, photo_id).download_as_bytes()
    except NotFound:
        raise PhotoMissing
    # One model call per item: the try-on model takes one product image per request
    worn: set[str] = set()
    for section, product in sorted(outfit, key=lambda item: LAYER[item[0]]):
        over_something = LAYER_MODEL and section in OUTER and worn
        person = (layer if over_something else dress)(person, product)
        worn.add(section)
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

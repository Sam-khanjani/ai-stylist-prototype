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


class PhotoMissing(Exception):
    """Never uploaded, deleted, or removed by the bucket's one-day rule."""


class Blocked(Exception):
    """The model's safety filter returned no image."""


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
def client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=os.getenv("GOOGLE_CLOUD_PROJECT", "ai-stylist-proto"),
        location=LOCATION,
        http_options=types.HttpOptions(timeout=120_000, retry_options=types.HttpRetryOptions(attempts=3)),
    )


def jpeg(data: bytes) -> types.Image:
    return types.Image(image_bytes=data, mime_type="image/jpeg")


def render(visitor_id: str, photo_id: str, product: dict) -> str:
    """The visitor wearing the product, as a JPEG data URL."""
    try:
        person = blob(visitor_id, photo_id).download_as_bytes()
    except NotFound:
        raise PhotoMissing
    # The CDN picks WebP/AVIF for f_auto; the model only takes JPEG or PNG
    url = product["images"][0].replace("f_auto", "f_jpg")
    garment = httpx.get(url, timeout=20, follow_redirects=True).raise_for_status().content
    response = client().models.recontext_image(
        model=MODEL,
        source=types.RecontextImageSource(
            person_image=jpeg(person), product_images=[types.ProductImage(product_image=jpeg(garment))]
        ),
        config=types.RecontextImageConfig(
            number_of_images=1, person_generation=types.PersonGeneration.ALLOW_ADULT, output_mime_type="image/jpeg"
        ),
    )
    images = [g.image for g in response.generated_images or [] if g.image and g.image.image_bytes]
    if not images:
        raise Blocked
    return "data:image/jpeg;base64," + base64.b64encode(images[0].image_bytes).decode()

import json
import os
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

CATALOG = Path(os.getenv("CATALOG_PATH", Path(__file__).resolve().parent.parent / "data/raw/products.jsonl"))
SECTIONS = ["accessories", "coats", "jackets", "knitwear", "shirts", "shoes", "shorts", "suits", "trousers", "waistcoats"]


@cache
def products() -> list[dict]:
    if not CATALOG.exists():
        return []
    return [json.loads(line) for line in CATALOG.read_text().splitlines() if line]


def section_of(product: dict) -> str:
    # .../en-nl/men/suits/... -> "suits"
    return urlparse(product["url"]).path.split("/")[3]


def card(p: dict) -> dict:
    """The fields the web app needs to render a product card."""
    return {
        "id": p["id"],
        "name": p["name"],
        "color": p["color"],
        "material": p["material"],
        "price": p["price"],
        "currency": p["currency"],
        "image": p["images"][0].replace("w_1200", "w_600") if p["images"] else None,
        "url": p["url"],
    }

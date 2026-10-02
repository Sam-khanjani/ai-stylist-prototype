"""Collect a small sample of public product data from suitsupply.com.

Product URLs come from the public sitemap and every page is checked against
robots.txt before it is fetched. Requests are slow on purpose. The data is for
private, non-commercial use only and is not redistributed.
"""
import argparse
import json
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests
from protego import Protego

SITE = "https://suitsupply.com"
LOCALE = "en-nl"
UA = "ai-stylist-proto/0.1 (personal non-commercial research; +https://github.com/Sam-khanjani/ai-stylist-prototype)"
DELAY = 3.0
IMAGE_DELAY = 1.0
OUT = Path(__file__).resolve().parent.parent / "data" / "raw"
PRODUCT_URL = re.compile(rf"^{SITE}/{LOCALE}/(men|women)/[^/]+/.+/[A-Z0-9-]+\.html$")

session = requests.Session()
session.headers["User-Agent"] = UA
robots = None


def fetch(url, delay):
    time.sleep(delay)
    r = session.get(url, timeout=30)
    if r.status_code in (403, 429, 503):
        sys.exit(f"Got {r.status_code} on {url}, stopping.")
    r.raise_for_status()
    # Server sends no charset, so requests would fall back to latin-1
    r.encoding = "utf-8"
    return r


def get_page(url):
    global robots
    if robots is None:
        robots = Protego.parse(fetch(f"{SITE}/robots.txt", 0).text)
    if not robots.can_fetch(url, UA):
        raise PermissionError("blocked by robots.txt")
    return fetch(url, DELAY)


def product_urls():
    cache = OUT / "urls.json"
    if cache.exists():
        return json.loads(cache.read_text())

    index = get_page(f"{SITE}/sitemap_index.xml").text
    urls = set()
    for sitemap in re.findall(r"<loc>(.*?)</loc>", index):
        locs = re.findall(r"<loc>(.*?)</loc>", get_page(sitemap).text)
        urls.update(u for u in locs if PRODUCT_URL.match(u))
        print(f"{sitemap}: {len(urls)} product urls")

    urls = sorted(urls)
    cache.write_text(json.dumps(urls, indent=1))
    return urls


def balanced(urls):
    """Round-robin over top-level categories so the sample covers all of them."""
    groups = defaultdict(list)
    for u in urls:
        groups["/".join(u.split("/")[4:6])].append(u)
    rng = random.Random(42)
    for g in groups.values():
        rng.shuffle(g)
    print(f"{len(groups)} categories: {', '.join(sorted(groups))}")

    while any(groups.values()):
        for g in groups.values():
            if g:
                yield g.pop()


def ld_json(html):
    for block in re.findall(r'(?s)<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        yield from data.get("@graph", [data])


def parse(html, url):
    nodes = list(ld_json(html))
    p = next(n for n in nodes if n.get("@type") in ("ProductGroup", "Product"))
    crumbs = next((n for n in nodes if n.get("@type") == "BreadcrumbList"), {})

    variants = p.get("hasVariant") or [p]
    offers = [v["offers"] for v in variants if v.get("offers")]
    offers = [o[0] if isinstance(o, list) else o for o in offers]
    images = p.get("image") or []

    return {
        "id": p.get("productGroupID") or p.get("sku"),
        "url": url,
        "name": p.get("name"),
        "breadcrumb": [i["name"] for i in crumbs.get("itemListElement", [])][1:-1],
        "category": p.get("category"),
        "gender": (p.get("audience") or {}).get("suggestedGender"),
        "color": p.get("color"),
        "material": p.get("material"),
        "description": p.get("description"),
        "price": min((float(o["price"]) for o in offers if o.get("price")), default=None),
        "currency": offers[0].get("priceCurrency") if offers else None,
        "sizes": [v["size"] for v in variants if v.get("size")],
        "in_stock": any(o.get("availability", "").endswith("InStock") for o in offers),
        "images": [images] if isinstance(images, str) else images,
    }


def save_images(product, limit):
    folder = OUT / "images" / product["id"]
    folder.mkdir(parents=True, exist_ok=True)
    for i, src in enumerate(product["images"][:limit]):
        dest = folder / f"{i}.jpg"
        if not dest.exists():
            dest.write_bytes(fetch(src.replace("f_auto", "f_jpg"), IMAGE_DELAY).content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--images", type=int, default=4, help="images per product, 0 to skip")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)

    out_file = OUT / "products.jsonl"
    saved = [json.loads(line) for line in out_file.open()] if out_file.exists() else []
    seen_urls = {p["url"] for p in saved}
    seen_ids = {p["id"] for p in saved}

    with out_file.open("a") as f:
        for url in balanced(product_urls()):
            if len(seen_ids) >= args.limit:
                break
            if url in seen_urls:
                continue
            try:
                product = parse(get_page(url).text, url)
            except Exception as e:
                print(f"skip {url}: {e}")
                continue
            if product["id"] in seen_ids:
                continue
            if args.images:
                save_images(product, args.images)
            f.write(json.dumps(product, ensure_ascii=False) + "\n")
            f.flush()
            seen_ids.add(product["id"])
            print(f"[{len(seen_ids)}/{args.limit}] {product['category']}: {product['name']}")


if __name__ == "__main__":
    main()

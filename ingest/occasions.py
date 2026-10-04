"""Save Suitsupply's public occasion pages and the products they feature.

The page text goes to knowledge/pages, so embed.py chunks it like any other page. New products are added to
products.jsonl, and occasions.json maps each occasion to its page and products for the style advisor.
Uses the same polite fetching as crawl.py.
"""
import json
import re

from crawl import LOCALE, OUT, SITE, get_page, parse, save_images
from pages import KNOWLEDGE, front_matter, page_to_md

# The site's "Occasion" menu
OCCASIONS = {
    "black-tie": "men/black-tie-collection",
    "business": "men/campaign/business-essentials",
    "clubbing": "men/campaign/clubbing-collection",
    "resort": "men/campaign/resort-collection",
    "wedding": "men/wedding",
}
PRODUCT_LINK = re.compile(rf"/{LOCALE}/men/[a-z-]+/[a-z0-9-]+/[A-Z0-9-]+\.html")


def main():
    products_file = OUT / "products.jsonl"
    saved = [json.loads(line) for line in products_file.read_text().splitlines() if line]
    id_of = {p["url"]: p["id"] for p in saved}
    known = set(id_of.values())

    occasions = {}
    with products_file.open("a") as f:
        for name, path in OCCASIONS.items():
            url = f"{SITE}/{LOCALE}/{path}"
            html = get_page(url).text
            title, md = page_to_md(html)
            (KNOWLEDGE / "pages" / f"occasion_{name}.md").write_text(front_matter(title, url) + md + "\n")

            ids = []
            for link in dict.fromkeys(PRODUCT_LINK.findall(html)):
                product_url = SITE + link
                if product_url not in id_of:
                    try:
                        product = parse(get_page(product_url).text, product_url)
                    except Exception as e:
                        print(f"skip {product_url}: {e}")
                        continue
                    id_of[product_url] = product["id"]
                    if product["id"] not in known:
                        save_images(product, 4)
                        f.write(json.dumps(product, ensure_ascii=False) + "\n")
                        f.flush()
                        known.add(product["id"])
                        print(f"  + {product['category']}: {product['name']}")
                ids.append(id_of[product_url])
            occasions[name] = {"title": title, "url": url, "products": list(dict.fromkeys(ids))}
            print(f"{name}: {len(occasions[name]['products'])} products")

    (OUT / "occasions.json").write_text(json.dumps(occasions, indent=1))


if __name__ == "__main__":
    main()

"""Save public customer service pages and store details as markdown.

Uses the same polite fetching as crawl.py (robots.txt check, slow requests).
"""
import json
import re
from datetime import date

from bs4 import BeautifulSoup
from markdownify import markdownify

from crawl import LOCALE, OUT, SITE, get_page

# Products and stores have their own collectors; account pages are private
SKIP = re.compile(rf"^/{LOCALE}/(men|women|stores|account|wishlist|cart|login)(/|$)")
MAX_DEPTH = 2
MAX_PAGES = 80
KNOWLEDGE = OUT / "knowledge"
TODAY = date.today().isoformat()


def front_matter(title, url):
    return f"---\ntitle: {title}\nsource: {url}\nfetched: {TODAY}\n---\n\n"


def page_to_md(html):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup.body
    for tag in main.find_all(["script", "style", "svg", "img", "nav", "form", "noscript"]):
        tag.decompose()
    # FAQ questions are accordion buttons, turn them into headings
    for button in main.select('button[aria-controls^="accordion"]'):
        button.name = "h3"
        button.attrs = {}
    title = soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else ""
    md = markdownify(str(main), heading_style="ATX", strip=["a", "button"])
    md = re.sub(r"\n\s*\n+", "\n\n", md).strip()
    return title, md


def content_links(html):
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        path = a["href"].split("#")[0].split("?")[0]
        if path.startswith(f"/{LOCALE}/") and not SKIP.match(path):
            yield path


def save_contact(home_html):
    """Customer service contacts only appear in the footer."""
    footer = BeautifulSoup(home_html, "html.parser").find("footer")
    lines = {
        a.get_text(strip=True): a["href"]
        for a in footer.find_all("a", href=True)
        if a["href"].startswith(("tel:", "mailto:", "https://api.whatsapp.com"))
    }
    md = "# Customer service contact\n\n" + "\n".join(
        f"- {'WhatsApp' if 'whatsapp' in href else 'Phone' if href.startswith('tel:') else 'Email'}: {text}"
        for text, href in lines.items()
    )
    (KNOWLEDGE / "pages" / "contact.md").write_text(front_matter("Customer service contact", f"{SITE}/{LOCALE}") + md + "\n")


def save_pages():
    """Follow links from the home page (header, footer and every page they lead to)."""
    folder = KNOWLEDGE / "pages"
    folder.mkdir(parents=True, exist_ok=True)

    home = get_page(f"{SITE}/{LOCALE}").text
    save_contact(home)
    queue = [(path, 1) for path in dict.fromkeys(content_links(home))]
    seen = {path for path, _ in queue}

    saved = 0
    while queue and saved < MAX_PAGES:
        path, depth = queue.pop(0)
        url = f"{SITE}{path}"
        try:
            html = get_page(url).text
            title, md = page_to_md(html)
        except Exception as e:
            print(f"skip {url}: {e}")
            continue
        name = path.removeprefix(f"/{LOCALE}/").removesuffix(".html").replace("/", "_")
        (folder / f"{name}.md").write_text(front_matter(title, url) + md + "\n")
        saved += 1
        print(f"[{saved}] {name}")

        if depth < MAX_DEPTH:
            for link in content_links(html):
                if link not in seen:
                    seen.add(link)
                    queue.append((link, depth + 1))


def countries(html):
    """Country slugs are only in the page's embedded data, right after "_slug"."""
    pos = html.index('\\"_slug\\"') + len('\\"_slug\\"')
    pair = re.compile(r',\\"([a-z-]+)\\",\\"[^"\\]+\\"')
    slugs = []
    while m := pair.match(html, pos):
        slugs.append(m.group(1))
        pos = m.end()
    return slugs


def parse_store(html, url, country):
    soup = BeautifulSoup(html, "html.parser")
    hours = {
        p["data-testid"].rsplit("-", 1)[1]: p.get_text(strip=True)
        for p in soup.select('[data-testid^="store-detail-opening-hours-time-"]')
    }
    address = soup.find("h2", string="Address")
    if not hours or not address:
        return None
    section = address.find_parent("section")
    link = address.find_next("a")
    email = section.select_one('a[href^="mailto:"]')
    phone = section.select_one('a[href^="tel:"]')
    coords = re.search(r"@(-?[\d.]+),(-?[\d.]+)", link.get("href", ""))
    return {
        "name": soup.find("h1").get_text(" ", strip=True),
        "country": country,
        "address": [p.get_text(strip=True) for p in link.find_all("p")],
        "email": email.get_text(strip=True) if email else None,
        "phone": phone.get_text(strip=True) if phone else None,
        "opening_hours": hours,
        "lat": float(coords[1]) if coords else None,
        "lng": float(coords[2]) if coords else None,
        "url": url,
    }


def save_stores():
    KNOWLEDGE.mkdir(parents=True, exist_ok=True)
    json_file = KNOWLEDGE / "stores.json"
    if json_file.exists():
        return

    # /stores redirects to the first country, which is left out of the embedded list
    landing = get_page(f"{SITE}/{LOCALE}/stores")
    slugs = countries(landing.text)
    first = landing.url.rstrip("/").rsplit("/", 1)[1]
    if first not in slugs and first != "stores":
        slugs.insert(0, first)

    stores = []
    for country in slugs:
        listing = get_page(f"{SITE}/{LOCALE}/stores/{country}/all").text
        for slug in sorted(set(re.findall(rf'href="/{LOCALE}/stores/([a-z0-9-]+)"', listing))):
            url = f"{SITE}/{LOCALE}/stores/{slug}"
            try:
                store = parse_store(get_page(url).text, url, country)
            except Exception as e:
                print(f"skip {url}: {e}")
                continue
            if store:
                stores.append(store)
                print(f"[{len(stores)}] {country}: {store['name']}")

    json_file.write_text(json.dumps(stores, indent=1, ensure_ascii=False))

    md = [front_matter("Stores", f"{SITE}/{LOCALE}/stores") + "# Stores\n"]
    for s in stores:
        md.append(f"\n## {s['name']} ({s['country'].replace('-', ' ').title()})\n")
        md.append(f"- Address: {', '.join(s['address'])}")
        md.append(f"- Email: {s['email']}")
        md.append(f"- Phone: {s['phone']}")
        md.append(f"- Opening hours (week of {TODAY}): " + "; ".join(f"{d.title()} {h}" for d, h in s["opening_hours"].items()))
        md.append(f"- Page: {s['url']}")
    (KNOWLEDGE / "stores.md").write_text("\n".join(md) + "\n")


if __name__ == "__main__":
    save_pages()
    save_stores()

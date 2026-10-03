"""Complete the look: 2-3 catalog items that go with the try-on outfit, picked by the chat model."""
from functools import lru_cache

from pydantic import BaseModel, Field

from agent import flush, llm, run_config
from catalog import card, products

# Categories that can complete a look. One already worn isn't suggested again, nor what a worn item already covers.
LOOK_CATEGORIES = {"Shirts", "Ties", "Shoes", "Belts", "Knitwear", "Trousers", "Jackets", "Waistcoats"}
COVERS = {"Suits": {"Jackets", "Trousers"}, "Shorts": {"Trousers"}, "Coats": {"Jackets"}}
COST_USD = 0.001  # rough estimate of one flash-lite call (~3K tokens in, 150 out), for the dashboard
# Used when the model returns fewer than two usable items
FALLBACK_ORDER = ["Shirts", "Ties", "Shoes", "Trousers", "Knitwear", "Belts"]

PROMPT = """You are a menswear stylist. The customer is trying on:
{outfit}

Pick 2 or 3 items from the catalog below that complete this look: each from a different category, matching in
colour, formality and season. For a suit or jacket, prefer a shirt, a tie and shoes. Use only ids from the list.

Catalog (id | category | name | colour | material):
{catalog}"""


class Pick(BaseModel):
    id: str
    reason: str = Field(description="Why it goes with the outfit, at most 12 words")


class Look(BaseModel):
    items: list[Pick]


def describe(p: dict) -> str:
    return " | ".join(str(p.get(k) or "-") for k in ("id", "category", "name", "color", "material"))


@lru_cache(maxsize=256)  # same outfit, same suggestions; keeps repeat views free
def complete(product_ids: tuple[str, ...]) -> list[dict]:
    by_id = {p["id"]: p for p in products()}
    outfit = [by_id[i] for i in product_ids if i in by_id]
    taken = {p["category"] for p in outfit}
    taken |= {c for p in outfit for c in COVERS.get(p["category"], ())}
    candidates = {p["id"]: p for p in products() if p["category"] in LOOK_CATEGORIES - taken and p["in_stock"]}
    if not outfit or not candidates:
        return []

    _, config = run_config(None)
    config["run_name"] = "complete-the-look"  # kept apart from the chat agent's traces on the dashboard
    prompt = PROMPT.format(
        outfit="\n".join(describe(p) for p in outfit), catalog="\n".join(describe(p) for p in candidates.values())
    )
    try:
        picks = llm.with_structured_output(Look).invoke(prompt, config=config).items
    except Exception:
        picks = []  # still suggest something below
    finally:
        flush()

    look, categories = [], set()
    for pick in picks:
        p = candidates.get(pick.id)
        if p and p["category"] not in categories and len(look) < 3:
            look.append(card(p) | {"reason": pick.reason})
            categories.add(p["category"])
    for category in FALLBACK_ORDER:
        if len(look) >= 2:
            break
        p = next((p for p in candidates.values() if p["category"] == category and category not in categories), None)
        if p:
            look.append(card(p) | {"reason": None})
            categories.add(category)
    return look

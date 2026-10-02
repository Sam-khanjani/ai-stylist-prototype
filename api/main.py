import json
import os
from contextlib import asynccontextmanager
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI
from pydantic import BaseModel

from agent import flush, run_agent

CATALOG = Path(os.getenv("CATALOG_PATH", Path(__file__).resolve().parent.parent / "data/raw/products.jsonl"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    flush()  # send pending traces before the instance stops


app = FastAPI(title="Stylist API", lifespan=lifespan)


@cache
def products() -> list[dict]:
    if not CATALOG.exists():
        return []
    return [json.loads(line) for line in CATALOG.read_text().splitlines() if line]


def section_of(product: dict) -> str:
    # .../en-nl/men/suits/... -> "suits"
    return urlparse(product["url"]).path.split("/")[3]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/products")
def list_products(section: str | None = None):
    items = products()
    if section:
        items = [p for p in items if section_of(p) == section]
    return items


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@app.post("/chat")
def chat(req: ChatRequest):
    reply = run_agent(req.message, req.session_id)
    flush()  # Cloud Run throttles CPU after the response, so send traces now
    return {"reply": reply}

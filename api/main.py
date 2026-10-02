from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from agent import flush, run_agent
from catalog import products, section_of


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    flush()  # send pending traces before the instance stops


app = FastAPI(title="Stylist API", lifespan=lifespan)


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
    result = run_agent(req.message, req.session_id)
    flush()  # Cloud Run throttles CPU after the response, so send traces now
    return result

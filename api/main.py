import json
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import flush, run_agent, score, stream_agent
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


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    def events():
        for event, data in stream_agent(req.message, req.session_id):
            yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
        flush()

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class Feedback(BaseModel):
    trace_id: str
    value: Literal[0, 1]  # thumbs down / up
    comment: str | None = None


@app.post("/feedback")
def feedback(fb: Feedback):
    score(fb.trace_id, fb.value, fb.comment)
    flush()
    return {"ok": True}

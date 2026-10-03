import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import admin
import history
import look
import sizing
import tryon
from agent import delete_traces, flush, run_agent, score, stream_agent, summarize
from catalog import products, section_of


@asynccontextmanager
async def lifespan(app: FastAPI):
    history.init()
    history.cleanup()  # also runs daily via Cloud Scheduler, see /maintenance/cleanup
    yield
    flush()  # send pending traces before the instance stops


app = FastAPI(title="Stylist API", lifespan=lifespan)
app.include_router(admin.router)


def visitor(x_visitor_id: str = Header()) -> str:
    """Anonymous visitor id from the web app's cookie. Must be a UUID so it can't be used to inject anything."""
    try:
        return str(uuid.UUID(x_visitor_id))
    except ValueError:
        raise HTTPException(400, "invalid visitor id")


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
    message: str = Field(min_length=1, max_length=1000)
    conversation_id: uuid.UUID | None = None


@app.post("/chat")
def chat(req: ChatRequest):
    """Single question without history, handy for curl tests."""
    result = run_agent(req.message)
    flush()  # Cloud Run throttles CPU after the response, so send traces now
    return result


@app.post("/chat/stream")
def chat_stream(req: ChatRequest, visitor_id: str = Depends(visitor)):
    if req.conversation_id:
        conversation_id = str(req.conversation_id)
        conversation = history.get_conversation(visitor_id, conversation_id)
        if not conversation:
            raise HTTPException(404, "conversation not found")
        memory = conversation["summary"]
    else:
        conversation_id, memory = history.create_conversation(visitor_id, req.message), ""
    history.add_message(conversation_id, "user", req.message)
    start = time.monotonic()

    def events():
        yield f"event: conversation\ndata: {json.dumps(conversation_id)}\n\n"
        for event, data in stream_agent(req.message, memory, conversation_id):
            if event == "done":
                latency_ms = int((time.monotonic() - start) * 1000)
                history.add_message(conversation_id, "assistant", data["reply"], data, latency_ms)
                history.record_event(data["route"], data["fallback"], latency_ms, data["trace_id"])
            yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
        # Update the memory after the answer is sent, so the customer doesn't wait for it
        history.set_summary(conversation_id, summarize(history.recent_messages(conversation_id)))
        flush()

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/conversations")
def conversations(visitor_id: str = Depends(visitor)):
    return history.list_conversations(visitor_id)


@app.get("/conversations/{conversation_id}")
def conversation(conversation_id: uuid.UUID, visitor_id: str = Depends(visitor)):
    found = history.get_conversation(visitor_id, str(conversation_id))
    if not found:
        raise HTTPException(404, "conversation not found")
    return found


@app.delete("/conversations")
def delete_conversations(visitor_id: str = Depends(visitor)):
    """'Delete my chats': removes the visitor's history here and the matching traces in Langfuse."""
    trace_ids = history.delete_visitor(visitor_id)
    return {"deleted": True, "traces_deleted": delete_traces(trace_ids)}


class Feedback(BaseModel):
    trace_id: str
    value: Literal[0, 1]  # thumbs down / up
    comment: str | None = None


@app.post("/feedback")
def feedback(fb: Feedback, visitor_id: str = Depends(visitor)):
    history.set_vote(visitor_id, fb.trace_id, fb.value)
    score(fb.trace_id, fb.value, fb.comment)
    flush()
    return {"ok": True}


@app.post("/tryon/photos")
def new_photo(visitor_id: str = Depends(visitor)):
    """Signed URL for the browser to upload a try-on photo to the bucket."""
    return tryon.upload_url(visitor_id)


@app.delete("/tryon/photos/{photo_id}")
def delete_photo(photo_id: uuid.UUID, visitor_id: str = Depends(visitor)):
    tryon.delete(visitor_id, str(photo_id))
    return {"deleted": True}


class TryOnRequest(BaseModel):
    photo_id: uuid.UUID | None = None  # without a photo only the height/weight size advice is possible
    product_ids: list[str] = Field([], max_length=tryon.MAX_ITEMS)  # with products: the try-on image instead of the size
    height_cm: int = Field(ge=140, le=220)
    weight_kg: int | None = Field(None, ge=40, le=200)
    fit: sizing.Fit = "regular"
    body: sizing.Body | None = None  # pose measurements from the photo, taken in the browser


@app.post("/tryon")
def try_on(req: TryOnRequest, visitor_id: str = Depends(visitor)):
    if not req.product_ids:
        if not req.body and not req.weight_kg:
            raise HTTPException(422, "Please add a photo or your weight.")
        return {"size": sizing.advise(req.height_cm, req.weight_kg, req.fit, req.body), "image": None}
    if not req.photo_id:
        raise HTTPException(422, "Please upload a photo first.")
    by_id = {p["id"]: p for p in products()}
    outfit = [(section_of(by_id[i]), by_id[i]) for i in dict.fromkeys(req.product_ids) if i in by_id]
    if len(outfit) < len(set(req.product_ids)) or any(s not in tryon.LAYER or not p["images"] for s, p in outfit):
        raise HTTPException(404, "One of these products can't be tried on.")
    if tryon.clash([s for s, _ in outfit]):
        raise HTTPException(422, "These items can't be worn together. Please pick one of them.")
    try:
        return {"size": None, "image": tryon.render(visitor_id, str(req.photo_id), outfit)}
    except tryon.PhotoMissing:
        raise HTTPException(404, "Your photo has expired. Please upload it again.")
    except tryon.Blocked as e:
        raise HTTPException(422, f"We couldn't put the {e} on this photo. Please try another photo or item.")


class LookRequest(BaseModel):
    product_ids: list[str] = Field(min_length=1, max_length=tryon.MAX_ITEMS)


@app.post("/tryon/look")
def complete_the_look(req: LookRequest):
    """2-3 catalog items that complete the try-on outfit, each with a short reason."""
    return {"items": look.complete(tuple(sorted(set(req.product_ids))))}


@app.post("/maintenance/cleanup")
def maintenance_cleanup():
    """Called daily by Cloud Scheduler to enforce the retention period."""
    return {"deleted_conversations": history.cleanup()}

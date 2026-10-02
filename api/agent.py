import os
import re
from typing import Literal

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import BaseModel, Field

import retrieval
from catalog import SECTIONS, card

llm = ChatGoogleGenerativeAI(
    # Flash-lite: routing and answering from given sources don't need the bigger model, and it is much faster
    model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
    vertexai=True,
    project=retrieval.PROJECT,
    # Gemini 3.x is not offered in single EU regions; the "eu" multi-region keeps processing in the EU
    location=os.getenv("GEMINI_LOCATION", "eu"),
    temperature=0,
)

ROUTER_PROMPT = """Classify the customer's message for a menswear store assistant.
- "policy": orders, shipping, delivery, returns, refunds, payments, sizing, alterations, stores, opening hours,
  services, gift cards or company information.
- "product": the customer wants product suggestions, prices or details about clothing and accessories.
- "other": anything unrelated to the store, its products or its services (weather, general knowledge, other brands, coding),
  and requests the assistant cannot handle such as checking a specific order or account.
For product questions, also fill in the filters that the customer mentions. Leave the others empty.
If the customer asks about stores in a whole country, set country to its English name."""

NO_ANSWER = "NO_ANSWER"

ANSWER_PROMPT = f"""You are the customer service assistant of an unofficial demo store built on public Suitsupply information.
Answer only from the numbered sources below and cite every fact with its number in square brackets, one number per bracket, e.g. [2] or [1][3].
Every answer needs at least one citation, even a short one, e.g. "Yes, return shipping is free [2]."
If the sources do not contain the answer, reply with exactly {NO_ANSWER} and nothing else. Never invent policies, prices or products.
Keep the answer short and friendly. Prices are in EUR, were collected for a demo and may have changed.

Sources:
{{sources}}"""

PRODUCT_PROMPT = f"""You are the styling assistant of an unofficial demo store built on public Suitsupply information.
Recommend products from the numbered list below that fit the customer's request, at most 4.
Briefly explain each choice using only that product's details (name, color, material, price, description),
e.g. why linen or a light color suits a summer event. Cite every product with its number in square brackets, one number per bracket, e.g. [2].
If none of the listed products fits the request, for example the customer asks for something the store does not sell,
reply with exactly {NO_ANSWER} and nothing else. Never invent products, prices or details.
Keep it short and friendly. Prices are in EUR, were collected for a demo and may have changed.

Products:
{{sources}}"""

FALLBACK_PROMPT = """You are the customer service assistant of an unofficial demo store built on public Suitsupply information.
You cannot answer the customer's message, either because it is outside what you can help with or because the information is not available.
Say so honestly in one sentence without guessing an answer. Then offer to help via customer service (phone, WhatsApp, email)
and suggest visiting or booking an appointment in a store, using only the numbered sources below.
Write the contact details out in full: the WhatsApp number and the email address, plus the phone number for the customer's country if they mention one.
Cite every fact with its number in square brackets, one number per bracket. Keep it short and friendly.

Sources:
{sources}"""

STORE_FINDER = {
    "title": "Find a store",
    "url": "https://suitsupply.com/en-nl/stores",
    "text": "Store finder listing every store with its address and opening hours; each store page has a button to book an appointment.",
}


class Route(BaseModel):
    route: Literal["policy", "product", "other"]
    section: Literal[tuple(SECTIONS)] | None = None
    color: str | None = None
    max_price: float | None = Field(None, description="maximum price in EUR")
    country: str | None = Field(None, description="country name in English, only for questions about stores in a country")


class State(MessagesState):
    route: Route
    sources: list[dict]
    fallback: bool


router = llm.with_structured_output(Route)


def question(state: State) -> str:
    return state["messages"][-1].content


def route(state: State):
    return {"route": router.invoke([SystemMessage(ROUTER_PROMPT), HumanMessage(question(state))])}


def policy_search(state: State):
    # "Which stores are in X" needs every store there, not just the top search hits
    country = state["route"].country
    stores = retrieval.stores_in(country) if country else []
    return {"sources": stores or retrieval.search(question(state))}


def product_search(state: State):
    r = state["route"]
    found = retrieval.search_products(question(state), r.section, r.color, r.max_price)
    return {"sources": [
        {
            "title": p["name"],
            "url": p["url"],
            "text": f"{p['name']} - {p['color']}, {p['material']}, EUR {p['price']:.0f}. {p['description']}",
            "product": card(p),
        }
        for p in found
    ]}


def numbered(sources: list[dict]) -> str:
    return "\n\n".join(
        f"[{i}] {s['title']}" + (f" > {s['heading']}" if s.get("heading") else "") + f"\n{s['text']}"
        for i, s in enumerate(sources, 1)
    ) or "(none)"


def answer(state: State):
    # Product questions need recommendations, which a strict "only what the sources say" prompt refuses to give
    prompt = PRODUCT_PROMPT if state["route"].route == "product" else ANSWER_PROMPT
    reply = llm.invoke([SystemMessage(prompt.format(sources=numbered(state["sources"]))), HumanMessage(question(state))])
    if NO_ANSWER in reply.text:
        return {"fallback": True}
    return {"messages": [AIMessage(reply.text)]}


def fallback(state: State):
    # Contact options and appointment info come from the crawled pages, so they stay citable
    sources = retrieval.contact_sources() + [STORE_FINDER]
    reply = llm.invoke([SystemMessage(FALLBACK_PROMPT.format(sources=numbered(sources))), HumanMessage(question(state))])
    return {"messages": [AIMessage(reply.text)], "sources": sources, "fallback": True}


builder = StateGraph(State)
builder.add_node(route)
builder.add_node(policy_search)
builder.add_node(product_search)
builder.add_node(answer)
builder.add_node(fallback)
builder.add_edge(START, "route")
builder.add_conditional_edges(
    "route", lambda s: s["route"].route, {"policy": "policy_search", "product": "product_search", "other": "fallback"}
)
builder.add_edge("policy_search", "answer")
builder.add_edge("product_search", "answer")
builder.add_conditional_edges("answer", lambda s: "fallback" if s.get("fallback") else END, ["fallback", END])
builder.add_edge("fallback", END)
graph = builder.compile()

# Tracing is optional so the api runs locally without Langfuse keys
tracing = bool(os.getenv("LANGFUSE_SECRET_KEY"))
if tracing:
    from langfuse import Langfuse, get_client
    from langfuse.langchain import CallbackHandler


def run_config(session_id: str | None) -> tuple[str | None, dict]:
    # A handler per request with our own trace id, so feedback can be attached to exactly this trace
    trace_id = Langfuse.create_trace_id() if tracing else None
    return trace_id, {
        "run_name": "stylist-agent",
        "callbacks": [CallbackHandler(trace_context={"trace_id": trace_id})] if tracing else [],
        "metadata": {"langfuse_session_id": session_id} if session_id else {},
    }


def summary(state: dict, trace_id: str | None) -> dict:
    reply = state["messages"][-1].content
    # Only return the sources the answer actually cites
    # Handles [2] as well as [2, 5] in case the model groups citations
    cited = {int(n) for group in re.findall(r"\[([\d,\s]+)\]", reply) for n in re.findall(r"\d+", group)}
    used = [(i, s) for i, s in enumerate(state["sources"], 1) if i in cited]
    return {
        "reply": reply,
        "route": state["route"].route,
        "fallback": state.get("fallback", False),
        "trace_id": trace_id,
        "sources": [{"n": i, "title": s["title"], "url": s["url"]} for i, s in used],
        # Cards for the products the answer recommends, in source order
        "products": [{"n": i, **s["product"]} for i, s in used if "product" in s],
    }


def run_agent(message: str, session_id: str | None = None) -> dict:
    trace_id, config = run_config(session_id)
    return summary(graph.invoke({"messages": [HumanMessage(message)]}, config=config), trace_id)


def stream_agent(message: str, session_id: str | None = None):
    """Yield ("token", text) while the reply is written, then ("done", summary)."""
    trace_id, config = run_config(session_id)
    held, state = "", None
    for mode, data in graph.stream({"messages": [HumanMessage(message)]}, config=config, stream_mode=["messages", "values"]):
        if mode == "values":
            state = data
            continue
        chunk, meta = data
        node = meta.get("langgraph_node")
        # Skip the router, and the finished message LangGraph re-emits after the tokens
        if node not in ("answer", "fallback") or not isinstance(chunk, AIMessageChunk):
            continue
        text = chunk.text
        # Hold back the start of the answer until it can't be the NO_ANSWER marker
        if node == "answer" and held is not None:
            held += text
            if NO_ANSWER.startswith(held.strip()):
                continue
            text, held = held, None
        if text:
            yield "token", text
    yield "done", summary(state, trace_id)


def score(trace_id: str, value: int, comment: str | None = None):
    if tracing:
        get_client().create_score(name="user_feedback", value=value, trace_id=trace_id, data_type="BOOLEAN", comment=comment)


def flush():
    if tracing:
        get_client().flush()

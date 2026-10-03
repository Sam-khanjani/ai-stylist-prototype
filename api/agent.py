import os
import re
from typing import Literal

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import BaseModel, Field

import retrieval
from catalog import COLORS, SECTIONS, card, products

llm = ChatGoogleGenerativeAI(
    # Flash-lite: routing and answering from given sources don't need the bigger model, and it is much faster
    model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
    vertexai=True,
    project=retrieval.PROJECT,
    # Gemini 3.x is not offered in single EU regions; the "eu" multi-region keeps processing in the EU
    location=os.getenv("GEMINI_LOCATION", "eu"),
    # No temperature: Gemini 3.x uses fixed sampling settings and ignores it (with a warning on every call)
)

# The memory is written by a cheaper, older model: condensing a chat doesn't need the newest one.
# Gemini 2.5 is offered in single EU regions, so it stays in the Netherlands like the embeddings.
summary_llm = ChatGoogleGenerativeAI(
    model=os.getenv("SUMMARY_MODEL", "gemini-2.5-flash-lite"),
    vertexai=True,
    project=retrieval.PROJECT,
    location=os.getenv("SUMMARY_LOCATION", "europe-west4"),
    temperature=0,
)

INTENT_PROMPT = """Detect the intent of the customer's latest message for a menswear store assistant.
- "greeting": hello, thanks, goodbye, small talk, or comments and questions about the assistant itself
  (who are you, you're great, you're useless), with no question about the store.
- "policy": shipping, delivery, returns, refunds, payments, gift cards, sizing, alterations, services such as
  Custom Made or Size Passport, materials, customer service contact details or company information.
- "store": store locations, addresses, opening hours, which stores are in a city or country, appointments.
- "product": the customer wants product suggestions, prices or details about clothing and accessories.
- "order_status": anything about a specific order or account: where is my order, change or cancel an order,
  my account, my payment. The assistant cannot look these up.
- "human": the customer asks for a person, a stylist or customer service, or makes a complaint.
- "conversation": questions about this chat itself: what did I ask, summarize our conversation, what did you say
  or recommend earlier, what was the second product you mentioned.
- "out_of_scope": unrelated to the store, its products or its services (weather, general knowledge, other brands, coding).
Follow-ups that ask for more on your previous answer ("explain more", "tell me more", "what do you mean?", "more details")
keep the intent of the question before and set elaborate to true.
For product questions, also fill in the filters that the customer mentions. Leave the others empty.
If the customer asks about stores in a whole country, set country to its English name.
Always set question to the customer's latest message rewritten as a standalone question, using the conversation
so far for context (e.g. "and in Rotterdam?" after asking about Amsterdam opening hours -> "What are the opening hours of the Rotterdam store?").
The message may have typos, slang or be in another language: understand what is meant and write question in clear,
correctly spelled English, because the store's information is in English (e.g. "hebben jullie grijze pakken?" -> "Do you have grey suits?").

Conversation so far:
{history}"""

SUMMARY_PROMPT = """You keep the memory of a conversation between a customer and a menswear store assistant.
Update the memory below with the new messages, in at most 5 short bullet points: what the customer is looking for and
the key facts already given (products, stores, countries, policies). Keep older facts that still matter.
Leave out personal details such as names, email addresses, phone numbers, addresses or order numbers of the customer.

Memory so far:
{summary}"""

# Only turns like these bring facts worth remembering; greetings, "what did I ask" or contact replies don't
WORTH_REMEMBERING = {"policy", "store", "product"}
SUMMARY_MIN_CHARS = 2500  # below this, and within the last RECENT messages, the messages themselves are memory enough

NO_ANSWER = "NO_ANSWER"

# Shared by every prompt that writes a reply to the customer
TONE = """Be warm, polite and personal, like a friendly assistant in the store.
End the reply with one short, friendly line offering more help, e.g. "Is there anything else I can help you with?".
Vary the wording and fit it to the conversation, e.g. offer help with sizing after recommending a suit."""

ANSWER_PROMPT = f"""You are the customer service assistant of an unofficial demo store built on public Suitsupply information.
Answer only from the numbered sources below and cite every fact with its number in square brackets, one number per bracket, e.g. [2] or [1][3].
Every answer needs at least one citation, even a short one, e.g. "Yes, return shipping is free [2]."
If the sources do not contain the answer, reply with exactly {NO_ANSWER} and nothing else. Never invent policies, prices or products.
Keep the answer short. Prices are in EUR, were collected for a demo and may have changed.
{TONE} (Not when you reply {NO_ANSWER}.)

Sources:
{{sources}}"""

PRODUCT_PROMPT = f"""You are the styling assistant of an unofficial demo store built on public Suitsupply information.
Recommend products from the numbered list below that fit the customer's request, at most 4.
Briefly explain each choice using only that product's details (name, color, material, price, description),
e.g. why linen or a light color suits a summer event. Cite every product with its number in square brackets, one number per bracket, e.g. [2].
If none of the listed products fits the request, for example the customer asks for something the store does not sell,
reply with exactly {NO_ANSWER} and nothing else. Never invent products, prices or details.
Keep it short. Prices are in EUR, were collected for a demo and may have changed.
{TONE} (Not when you reply {NO_ANSWER}.)

Products:
{{sources}}"""

RECALL_PROMPT = f"""You are the customer service assistant of an unofficial demo store built on public Suitsupply information.
The customer asks about this conversation itself. Answer only from the conversation so far given above,
e.g. list the questions they asked or what you recommended. Never invent anything that isn't in it.
If the conversation doesn't contain it, say so kindly: you only keep the recent part of a chat, and chats are deleted
after {os.getenv("CHAT_RETENTION_DAYS", "30")} days.
{TONE}"""

JUDGE_PROMPT = """You are a strict quality judge for the answers of a menswear store assistant.
Decide whether the draft answer below is good enough to send to the customer. It is qualified only if all of these hold:
1. It answers the customer's question; for a product request it recommends products that fit the request.
2. Every fact (policies, prices, times, products, contact details) is supported by the numbered sources. Nothing is invented.
3. Every citation [n] points to a source that supports that sentence.
4. It is clear, polite and not repetitive. It may be in another language than the sources, to match the customer.
The friendly closing line offering more help is expected and needs no citation.
If it is not qualified, list the problems briefly so the writer can fix them.

Sources:
{sources}"""

RETRY = """A quality check rejected your previous draft for these reasons: {problems}
Write a better answer that fixes them."""

ELABORATE = """The customer asks for more detail on your previous answer. Go deeper with details from the sources that
you haven't mentioned yet, and don't repeat the previous answer."""

FALLBACK_PROMPT = f"""You are the customer service assistant of an unofficial demo store built on public Suitsupply information.
You cannot answer the customer's message, either because it is outside what you can help with or because the information is not available.
Say so honestly in one sentence without guessing an answer. Then offer to help via customer service (phone, WhatsApp, email)
and suggest visiting or booking an appointment in a store, using only the numbered sources below.
Write the contact details out in full: the WhatsApp number and the email address, plus the phone number for the customer's country if they mention one.
Cite every fact with its number in square brackets, one number per bracket. Keep it short.
{TONE}

Sources:
{{sources}}"""

SMALLTALK_PROMPT = f"""You are the customer service assistant of an unofficial demo store built on public Suitsupply information.
Reply to the customer's greeting, thanks, small talk or comment about you in two or three warm sentences:
- to a greeting: welcome them and mention what you can help with: finding an outfit or a product, shipping, returns
  and payments, sizing and alterations, stores and appointments;
- to thanks: say you're glad to help;
- to a goodbye: wish them a nice day;
- to criticism or a rude remark: stay kind and calm, don't argue, and offer to help.
Do not state any facts about policies, prices or products.
{TONE}"""

STORE_FINDER = {
    "title": "Find a store",
    "url": "https://suitsupply.com/en-nl/stores",
    "text": "Store finder listing every store with its address and opening hours; each store page has a button to book an appointment.",
}


IntentName = Literal["greeting", "policy", "store", "product", "order_status", "human", "conversation", "out_of_scope"]
# The coarse group used by the eval, the dashboard and the events table
ROUTE_OF = {"policy": "policy", "store": "policy", "product": "product"}


class Intent(BaseModel):
    intent: IntentName
    section: Literal[tuple(SECTIONS)] | None = None
    color: Literal[tuple(COLORS)] | None = Field(None, description="the closest catalog colour, e.g. gray or charcoal -> grey")
    max_price: float | None = Field(None, description="maximum price in EUR")
    language: str = Field("English", description="the language the customer writes in")
    country: str | None = Field(None, description="country name in English, only for questions about stores in a country")
    question: str = Field("", description="the latest message as a standalone question")
    elaborate: bool = Field(False, description="the customer asks for more detail on the previous answer")

    @property
    def route(self) -> str:
        return ROUTE_OF.get(self.intent, "other")


class Verdict(BaseModel):
    qualified: bool
    problems: str = Field("", description="what is wrong, when not qualified")


MAX_DRAFTS = 2  # a rejected draft is rewritten once with the judge's feedback, then it's the fallback


class State(MessagesState):
    summary: str  # memory of the earlier conversation, see summarize()
    recent: list[tuple[str, str]]  # the last messages before this one, as (role, text)
    previous_sources: list[str]  # urls the previous answer cited, for "explain more"
    intent: Intent
    sources: list[dict]
    fallback: bool
    draft: str  # the answer before the judge approves it
    drafts: int  # how many were written
    verdict: Verdict


detector = llm.with_structured_output(Intent)
judge_llm = llm.with_structured_output(Verdict)


def question(state: State) -> str:
    # After intent detection, use the standalone version so follow-ups like "and in Rotterdam?" can be searched
    i = state.get("intent")
    return i.question if i and i.question else state["messages"][-1].content


RECENT = 10  # earlier messages shown next to the summary (all the api loads)
CITATION = re.compile(r"\s*\[[\d,\s]+\]")


def history(state: State) -> str:
    """The conversation before this message: the summary plus the last few messages, or "" for a new one."""
    # Old citation numbers would point at the wrong sources in the next answer
    recent = "\n".join(f"{role}: {CITATION.sub('', text)[:400]}" for role, text in (state.get("recent") or [])[-RECENT:])
    summary = state.get("summary")
    if not recent and not summary:
        return ""
    return f"Summary:\n{summary or '(none yet)'}\n\nLast messages:\n{recent or '(none)'}"


def with_history(prompt: str, state: State) -> str:
    """Puts the conversation in front of a reply prompt, so the assistant continues it instead of starting over.
    In front, not after: behind a long list of sources the model loses the instruction and greets again."""
    # The question is rewritten in English for search, so the customer's own language has to be asked for
    if (i := state.get("intent")) and i.language.lower() != "english":
        prompt = f"Reply in {i.language}, the customer's language.\n\n{prompt}"
    earlier = history(state)
    if not earlier:
        return "This is the start of a new conversation.\n\n" + prompt
    return (
        "This conversation is already in progress and the customer has been greeted. Do not start your reply with a "
        'greeting such as "Hello" or "Hi" and do not introduce yourself again: continue the conversation naturally.\n'
        "Conversation so far (for context only; facts still come from the sources):\n"
        f"{earlier}\n\n{prompt}"
    )


def intent(state: State):
    """Intent detection: what the customer wants, the standalone question and any search filters, in one call."""
    prompt = INTENT_PROMPT.format(history=history(state) or "(new conversation)")
    return {"intent": detector.invoke([SystemMessage(prompt), HumanMessage(state["messages"][-1].content)])}


def elaborating(state: State) -> list[str]:
    """For "explain more": the pages the previous answer cited, so the answer goes deeper instead of drifting."""
    return state.get("previous_sources") or [] if state["intent"].elaborate else []


def policy_search(state: State):
    # Nothing from those pages (e.g. the previous answer was about products): search as usual
    if (pages := elaborating(state)) and (found := retrieval.from_pages(pages, question(state))):
        return {"sources": found}
    # "Which stores are in X" needs every store there, not just the top search hits
    country = state["intent"].country
    stores = retrieval.stores_in(country) if country else []
    return {"sources": stores or retrieval.search(question(state))}


def product_search(state: State):
    i = state["intent"]
    pages = elaborating(state)
    found = [p for p in products() if p["url"] in pages]  # for "explain more": the products recommended before
    if not found:
        found = retrieval.search_products(question(state), i.section, i.color, i.max_price)
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
    prompt = PRODUCT_PROMPT if state["intent"].intent == "product" else ANSWER_PROMPT
    system = with_history(prompt.format(sources=numbered(state["sources"])), state)
    if state["intent"].elaborate:
        system = f"{ELABORATE}\n\n{system}"  # in front, like the conversation, so it isn't lost behind the sources
    if (v := state.get("verdict")) and not v.qualified:
        system = f"{RETRY.format(problems=v.problems)}\n\n{system}"
    reply = llm.invoke([SystemMessage(system), HumanMessage(question(state))])
    if NO_ANSWER in reply.text:
        return {"fallback": True}
    return {"draft": reply.text, "drafts": state.get("drafts", 0) + 1}


def judge(state: State):
    """Checks the draft before the customer sees it: answers the question, grounded in the sources, valid citations."""
    system = JUDGE_PROMPT.format(sources=numbered(state["sources"]))
    check = f"Customer question: {question(state)}\n\nDraft answer:\n{state['draft']}"
    verdict = judge_llm.invoke([SystemMessage(system), HumanMessage(check)])
    if verdict.qualified:
        return {"verdict": verdict, "messages": [AIMessage(state["draft"])]}
    return {"verdict": verdict, "fallback": state["drafts"] >= MAX_DRAFTS}


def after_judge(state: State) -> str:
    if state["verdict"].qualified:
        return END
    return "fallback" if state.get("fallback") else "answer"


def smalltalk(state: State):
    reply = llm.invoke([SystemMessage(with_history(SMALLTALK_PROMPT, state)), HumanMessage(state["messages"][-1].content)])
    return {"messages": [AIMessage(reply.text)], "sources": []}


def recall(state: State):
    """Questions about the chat itself, answered from the conversation history only."""
    reply = llm.invoke([SystemMessage(with_history(RECALL_PROMPT, state)), HumanMessage(state["messages"][-1].content)])
    return {"messages": [AIMessage(reply.text)], "sources": []}


def fallback(state: State):
    # Contact options and appointment info come from the crawled pages, so they stay citable
    sources = retrieval.contact_sources() + [STORE_FINDER]
    system = with_history(FALLBACK_PROMPT.format(sources=numbered(sources)), state)
    reply = llm.invoke([SystemMessage(system), HumanMessage(question(state))])
    return {"messages": [AIMessage(reply.text)], "sources": sources, "fallback": True}


# Which node handles each intent; order, account, human and off-topic requests go to the contact options
NEXT = {
    "greeting": "smalltalk",
    "policy": "policy_search",
    "store": "policy_search",
    "product": "product_search",
    "order_status": "fallback",
    "human": "fallback",
    "conversation": "recall",
    "out_of_scope": "fallback",
}

builder = StateGraph(State)
builder.add_node(intent)
builder.add_node(policy_search)
builder.add_node(product_search)
builder.add_node(answer)
builder.add_node(judge)
builder.add_node(smalltalk)
builder.add_node(recall)
builder.add_node(fallback)
builder.add_edge(START, "intent")
builder.add_conditional_edges("intent", lambda s: NEXT[s["intent"].intent], sorted(set(NEXT.values())))
builder.add_edge("policy_search", "answer")
builder.add_edge("product_search", "answer")
builder.add_conditional_edges("answer", lambda s: "fallback" if s.get("fallback") else "judge", ["fallback", "judge"])
builder.add_conditional_edges("judge", after_judge, ["answer", "fallback", END])
builder.add_edge("smalltalk", END)
builder.add_edge("recall", END)
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


def result(state: dict, trace_id: str | None) -> dict:
    reply = state["messages"][-1].content
    # Only return the sources the answer actually cites
    # Handles [2] as well as [2, 5] in case the model groups citations
    cited = {int(n) for group in re.findall(r"\[([\d,\s]+)\]", reply) for n in re.findall(r"\d+", group)}
    used = [(i, s) for i, s in enumerate(state["sources"], 1) if i in cited]
    return {
        "reply": reply,
        "route": state["intent"].route,
        "intent": state["intent"].intent,
        "fallback": state.get("fallback", False),
        "trace_id": trace_id,
        "sources": [{"n": i, "title": s["title"], "url": s["url"]} for i, s in used],
        # Cards for the products the answer recommends, in source order
        "products": [{"n": i, **s["product"]} for i, s in used if "product" in s],
    }


def inputs(message: str, memory: str, recent, previous_sources) -> dict:
    return {
        "messages": [HumanMessage(message)],
        "summary": memory,
        "recent": list(recent),
        "previous_sources": list(previous_sources),
    }


def run_agent(message: str, memory: str = "", session_id: str | None = None, recent=(), previous_sources=()) -> dict:
    trace_id, config = run_config(session_id)
    return result(graph.invoke(inputs(message, memory, recent, previous_sources), config=config), trace_id)
    return result(graph.invoke(inputs, config=config), trace_id)


def stream_agent(message: str, memory: str = "", session_id: str | None = None, recent=(), previous_sources=()):
    """Yield ("token", text) while the reply is written, then ("done", result).
    memory is the conversation summary, recent the last (role, text) messages before this one, previous_sources the
    urls the previous answer cited."""
    trace_id, config = run_config(session_id)
    state, streamed = None, False
    start = inputs(message, memory, recent, previous_sources)
    for mode, data in graph.stream(start, config=config, stream_mode=["messages", "values"]):
        if mode == "values":
            state = data
            continue
        chunk, meta = data
        # Answers aren't streamed: the judge has to approve them first. Skip the structured outputs (intent, judge)
        # and the finished message LangGraph re-emits after the tokens.
        if meta.get("langgraph_node") not in ("smalltalk", "recall", "fallback") or not isinstance(chunk, AIMessageChunk):
            continue
        if chunk.text:
            streamed = True
            yield "token", chunk.text
    if not streamed:  # an approved answer arrives in one piece
        yield "token", state["messages"][-1].content
    yield "done", result(state, trace_id)


def needs_summary(intent: str | None, messages: list[tuple[str, str]]) -> bool:
    """Every step already sees the last RECENT messages word for word, so the summary only matters when this turn
    brought useful facts and the chat is too long for those messages alone (older ones drop out, or they're long)."""
    long = len(messages) >= RECENT or sum(len(text) for _, text in messages) > SUMMARY_MIN_CHARS
    return intent in WORTH_REMEMBERING and long


def summarize(summary: str, messages: list[tuple[str, str]]) -> str:
    """The previous memory updated with the recent messages."""
    transcript = "\n".join(f"{role}: {CITATION.sub('', text)}" for role, text in messages)
    prompt = SUMMARY_PROMPT.format(summary=summary or "(empty)")
    return summary_llm.invoke([SystemMessage(prompt), HumanMessage(transcript)]).text


def delete_traces(trace_ids: list[str]) -> bool:
    """Best effort: the chats are already gone from our database, a Langfuse outage shouldn't fail the request."""
    if not tracing or not trace_ids:
        return True
    try:
        get_client().api.trace.delete_multiple(trace_ids=trace_ids)
        return True
    except Exception as e:
        print(f"Could not delete {len(trace_ids)} Langfuse traces: {e!r}")
        return False


def score(trace_id: str, value: int, comment: str | None = None):
    if tracing:
        get_client().create_score(name="user_feedback", value=value, trace_id=trace_id, data_type="BOOLEAN", comment=comment)


def flush():
    if tracing:
        get_client().flush()

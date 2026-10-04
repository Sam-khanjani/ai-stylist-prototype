import os
import re
from typing import Literal

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import BaseModel, Field

import retrieval
from catalog import COLORS, OCCASIONS, SECTIONS, card, occasions, products, section_of

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
  Custom Made or Size Passport, materials, customer service contact details or company information, including
  business questions: a company or organization that wants to work with Suitsupply (dress its staff, a business
  account, a partnership) is about its Corporate Service (write question as e.g. "What does Suitsupply's Corporate
  Service offer companies?"). A question about contracts when buying (e.g. "what is a contract in Suitsupply?") is
  about the terms and conditions, not the Corporate Service.
- "store": store locations, addresses, opening hours, which stores are in a city or country, appointments.
- "product": the customer looks for a kind of product, its price or details (e.g. "navy suit under 700", "do you have
  brown shoes?", "shoes that match my black coat"), and follow-ups that change that search ("and for a white coat?").
- "style": the customer wants styling advice: what to wear to an occasion or event, how to style or combine clothes they
  already own, putting together a look or building a style. Also their answers to the stylist's questions about it,
  and follow-ups in a styling conversation that change their mind (another piece, budget or occasion, "only a coat").
- "order_status": anything about a specific order or account: where is my order, change or cancel an order,
  my account, my payment. The assistant cannot look these up.
- "human": the customer asks for a person, a stylist or customer service, or makes a complaint.
- "conversation": questions about this chat itself: what did I ask, summarize our conversation, what did you say
  or recommend earlier, what was the second product you mentioned.
- "out_of_scope": unrelated to the store, its products or its services (weather, general knowledge, other brands, coding).
Follow-ups that ask for more on your previous answer ("explain more", "tell me more", "what do you mean?", "more details")
keep the intent of the question before and set elaborate to true.
Asking for other options ("any other suggestion?", "something else", "show me different ones") is not elaborate: keep
the intent of the question before and set alternatives to true.
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
WORTH_REMEMBERING = {"policy", "store", "product", "style"}
SUMMARY_MIN_CHARS = 2500  # below this, and within the last RECENT messages, the messages themselves are memory enough

NO_ANSWER = "NO_ANSWER"

# Shared by every prompt that writes a reply to the customer
TONE = """Be warm, polite and personal, like a friendly assistant in the store.
End the reply with one short, friendly line offering more help, e.g. "Is there anything else I can help you with?".
Vary the wording and fit it to the conversation, e.g. offer help with sizing after recommending a suit."""

# Product cards in the chat open the product on this site, next to the try-on panel
TRY_ON = """Before the closing line, add one sentence like "Click a product card to find it on this page and see how it
looks on you with Try it on.\""""

ANSWER_PROMPT = f"""You are the customer service assistant of an unofficial demo store built on public Suitsupply information.
Answer only from the numbered sources below and cite every fact with its number in square brackets, one number per bracket, e.g. [2] or [1][3].
Every answer needs at least one citation, even a short one, e.g. "Yes, return shipping is free [2]."
If the sources do not contain the answer, reply with exactly {NO_ANSWER} and nothing else. Never invent policies, prices or products.
Keep the answer short. Prices are in EUR, were collected for a demo and may have changed.
{TONE} (Not when you reply {NO_ANSWER}.)

Sources:
{{sources}}"""

PRODUCT_PROMPT = f"""You are the styling assistant of an unofficial demo store built on public Suitsupply information.
Recommend products from the numbered list below that fit the customer's request, at most 4. They are options to choose
from, each within the customer's price limit: don't add their prices up (for a full outfit the stylist helps).
Briefly explain each choice using only that product's details (name, color, material, price, description),
e.g. why linen or a light color suits a summer event. Cite every product with its number in square brackets, one number per bracket, e.g. [2].
If none of the listed products fits the request, for example the customer asks for something the store does not sell,
reply with exactly {NO_ANSWER} and nothing else. Never invent products, prices or details.
Keep it short. Prices are in EUR, were collected for a demo and may have changed.
{TRY_ON}
{TONE} (Not when you reply {NO_ANSWER}.)

Products:
{{sources}}"""

# The stylist first makes sure it knows enough, like a stylist in the store would ask before suggesting anything
BRIEF_PROMPT = f"""You are the personal stylist of an unofficial demo store built on public Suitsupply information.
Keep the customer's details up to date and decide whether you know enough to suggest one concrete outfit.
The details below are what you know from earlier in this conversation. Start from them and update them with the latest
message: change only what the customer changes or adds (e.g. a higher budget and a coat instead of a suit) and keep
every other detail as it is.
- For an occasion you need: the occasion and its dress code, the season or weather, and the customer's role when it
  matters (e.g. groom or guest).
- For styling clothes they own you need: the pieces they want to build on, the look they are after and the season.
Budget, colour preferences and choices between options (e.g. linen suit or blazer) are optional: never ask about
them, you suggest them. As soon as what you need is known, set ready to true.
If something you need is missing, set ready to false and write at most two short questions about it in questions,
the most important first, warm and personal. Never ask for something the details or the conversation already answer.
If you already asked questions twice in this conversation, or the customer doesn't know or doesn't mind, set ready to
true and assume sensible defaults.
Choose the sections to suggest from for what they want now: the pieces that complete the look, not the ones the
customer already owns or has chosen (e.g. a suit, a shirt and shoes for a wedding guest).
When the customer decides to buy a product, add it with its price to chosen and keep budget_left up to date.
Products you recommended before are not owned or chosen unless the customer says so. When the customer asks for other
options, keep the sections of the look so different products can be suggested for the same pieces.
{TONE} (Not when you only ask questions.)

Details so far:
{{details}}"""

STYLE_PROMPT = f"""You are the personal stylist of an unofficial demo store built on public Suitsupply information.
Put together one complete outfit for the customer's brief below: a combination of pieces that go together, one of each
kind (e.g. a suit, a shirt and shoes), not alternatives of the same kind. Name each piece and say in a few words why it
works (colour, formality, fabric, season, the occasion) and how the pieces work together. Build on what the customer
already owns and complete the look with products from the numbered list below, at most 4. Choose colours that work with
what they own and with each other: contrast or complement rather than repeating the same colour on every piece (e.g.
not navy trousers and a navy shirt with a navy blazer, not a navy shirt with a navy suit). Use the advice of the
occasion page sections when it helps.
Cite every product and every fact from the sources with its number in square brackets, one number per bracket, e.g. [2].
Call each product by its exact name from the list and cite it with its own number only, right after its name.
Each product says how much of the budget it leaves: that is the
most the other recommended products may cost together (a product that leaves EUR 2 can't be combined with another).
Pieces the customer owns and your own styling tips need no citation.
When the brief has a budget, the products you recommend must add up to no more than the budget left (the budget when
nothing is chosen yet): if a full outfit doesn't fit, recommend the most important pieces and say what could be added later.
When something the customer asks for can't be met (e.g. nothing fits the budget left), say so kindly and why, then offer
the closest options you have, e.g. the cheapest fitting piece, and what a higher budget would allow. If there
is nothing to offer, say so and give styling advice only. When the customer asks about products recommended before,
answer that first.
Never invent products, prices or details. Keep it short. Prices are in EUR, were collected for a demo and may have changed.
{TRY_ON}
{TONE}

Sources:
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
3. Every citation [n] points to a source that supports that sentence, and every recommended product is cited with its
   number (without it the customer gets no product card).
4. It is clear, polite and not repetitive. It may be in another language than the sources, to match the customer.
The friendly closing line offering more help and the invitation to try products on are expected and need no citation.
A citation at the end of a sentence covers every fact in it. Recommending a few of the fitting products is fine; it
doesn't have to list them all. Friendly styling remarks (e.g. "goes well with a white coat"), pieces the customer
already owns, what the customer said earlier in the conversation and products recommended earlier in it are not
invented facts.
An answer that openly says a request can't be fully met and why (e.g. nothing fits the budget left) is honest and
helpful, not a problem. When the budget left is too small for a full outfit, recommending fewer pieces is right.
You don't need to check prices against the budget or add them up: the system does that. An answer may state its
total; that is not a problem. Colour and style choices are the stylist's call: reject them only when they
clearly don't fit the request or the occasion. Reject only for real problems, not for wording or style.
If it is not qualified, list the problems briefly so the writer can fix them.

Sources:
{sources}"""

RETRY = """A quality check rejected your previous draft for these reasons: {problems}
Write a better answer that fixes them."""

FAILED = """You tried to answer the customer but your own answer wasn't good enough, for this reason: {problems}
This is about your answer, not something the customer asked for. If the reason matters to the customer (e.g. nothing
fits the budget left), explain it in one or two simple, kind sentences in their terms; otherwise just say you couldn't
put together a reliable suggestion this time. Never mention drafts, checks or sources. Suggest what could help, e.g. a
higher budget, another piece or our stylists in store."""

ELABORATE = """The customer asks for more detail on your previous answer. Go deeper with details from the sources that
you haven't mentioned yet, and don't repeat the previous answer."""

FALLBACK_PROMPT = f"""You are the customer service assistant of an unofficial demo store built on public Suitsupply information.
You cannot answer the customer's message, either because it is outside what you can help with or because the information is not available.
Be kind and personal, never cold: show you understood what they were looking for, and say honestly that you can't
answer it, without guessing an answer. Then offer to help via customer service (phone, WhatsApp, email)
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


IntentName = Literal["greeting", "policy", "store", "product", "style", "order_status", "human", "conversation", "out_of_scope"]
# The coarse group used by the eval, the dashboard and the events table
ROUTE_OF = {"policy": "policy", "store": "policy", "product": "product", "style": "style"}


class Intent(BaseModel):
    intent: IntentName
    section: Literal[tuple(SECTIONS)] | None = None
    color: Literal[tuple(COLORS)] | None = Field(None, description=(
        "the colour of the product the customer wants, as the closest catalog colour (gray or charcoal -> grey); "
        "not the colour of what it should match: shoes for a white coat -> none"
    ))
    max_price: float | None = Field(None, description=(
        "maximum price in EUR; for cheaper options, just below the lowest price recommended before"
    ))
    language: str = Field("English", description="the language the customer writes in, as its English name, e.g. English or Dutch")
    country: str | None = Field(None, description="country name in English, only for questions about stores in a country")
    occasion: Literal[tuple(OCCASIONS)] | None = Field(None, description=(
        "the closest occasion the customer dresses for, also from the conversation: wedding (also engagement), "
        "black-tie (gala, tuxedo, formal evening), business (office, interview, meeting), resort (beach, holiday), "
        "clubbing (night out, party)"
    ))
    question: str = Field("", description="the latest message as a standalone question")
    elaborate: bool = Field(False, description="the customer asks for more detail on the previous answer")
    alternatives: bool = Field(False, description="the customer asks for other options than the ones recommended before")

    @property
    def route(self) -> str:
        return ROUTE_OF.get(self.intent, "other")


class Verdict(BaseModel):
    qualified: bool
    problems: str = Field("", description="what is wrong, when not qualified")


class Brief(BaseModel):
    """What the stylist knows about the request. Saved with the conversation and updated every styling turn, so a
    change of mind changes only the details it is about."""
    occasion: str = Field("", description="the occasion or event and where, e.g. a wedding in Italy")
    role: str = Field("", description="the customer's role at it, e.g. guest or the groom's best friend")
    season: str = Field("", description="season, month or weather")
    dress_code: str = ""
    wants: str = Field("", description="what they want to buy now, e.g. a full outfit, a suit or a short coat")
    owns: str = Field("", description="pieces they own and want to wear, e.g. a blue shirt")
    chosen: str = Field("", description="products they decided to buy, with prices, e.g. Havana Dinner Jacket EUR 449")
    budget: float | None = Field(None, description="total budget in EUR for everything they buy, chosen products included")
    budget_left: float | None = Field(None, description="the budget minus the prices of the chosen products")
    colours: str = Field("", description="colour preferences")
    look: str = Field("", description="the style they are after, e.g. smart casual")
    sections: list[Literal[tuple(SECTIONS)]] = Field(
        default_factory=list, description="the sections to suggest from for what they want now, most important first, at most 4"
    )
    ready: bool = Field(False, description="enough is known to suggest a concrete outfit")
    questions: str = Field("", description="when not ready: at most two short questions about what is missing")

    def text(self, leave_out=()) -> str:
        """The known details in one line, for the stylist."""
        details = self.model_dump(exclude={"sections", "ready", "questions", *leave_out}, exclude_defaults=True)
        return "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in details.items())

    def query(self) -> str:
        """What to search for: the occasion and the look. Not what they own or chose: a navy blazer in the query finds
        navy trousers and navy shirts, and the outfit turns navy head to toe."""
        return self.text(leave_out={"owns", "chosen", "budget", "budget_left"})


MAX_DRAFTS = 2  # a rejected draft is rewritten once with the judge's feedback, then it's the fallback
ANSWER_KIND = {
    "product": "product suggestions: alternatives to choose from, each within the customer's price limit; their prices "
    "are not added up",
    "style": "an outfit: the recommended products are worn together (the budget is checked separately)",
}


class State(MessagesState):
    summary: str  # memory of the earlier conversation, see summarize()
    recent: list[tuple[str, str]]  # the last messages before this one, as (role, text)
    previous_sources: list[str]  # urls the previous answer cited, for "explain more"
    intent: Intent
    previous_brief: dict | None  # the stylist's details saved with the conversation
    brief: Brief  # style advice only
    sources: list[dict]
    fallback: bool
    draft: str  # the answer before the judge approves it
    drafts: int  # how many were written
    verdict: Verdict


detector = llm.with_structured_output(Intent)
judge_llm = llm.with_structured_output(Verdict)
briefer = llm.with_structured_output(Brief)


def question(state: State) -> str:
    # Style advice works from the stylist's brief, which gathers what the customer said over several messages
    # plus the latest message, which may ask something specific ("are these suitable?")
    if (b := state.get("brief")) and b.ready:
        return f"{b.text()}\nLatest message: {state['messages'][-1].content}"
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
    if (i := state.get("intent")) and i.language.lower() not in ("english", "en"):
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
    i = detector.invoke([SystemMessage(prompt), HumanMessage(state["messages"][-1].content)])
    # Once the stylist has saved details, "a coat instead" or a new budget is a change of mind it should handle,
    # keeping the occasion and the rest; left to the model this sometimes went to product search instead
    if i.intent == "product" and state.get("previous_brief"):
        i.intent = "style"
    return {"intent": i}


def elaborating(state: State) -> list[str]:
    """For "explain more": the pages the previous answer cited, so the answer goes deeper instead of drifting."""
    return state.get("previous_sources") or [] if state["intent"].elaborate else []


def policy_search(state: State):
    # Nothing from those pages (e.g. the previous answer was about products): search as usual
    if (pages := elaborating(state)) and (found := retrieval.from_pages(pages, question(state))):
        return {"sources": found}
    # "Which stores are in X" needs every store there, not just the top search hits. Only for store questions: for
    # "delivery to Germany" the model sometimes sets the country too, and the store list replaced the delivery page
    country = state["intent"].country if state["intent"].intent == "store" else None
    stores = retrieval.stores_in(country) if country else []
    return {"sources": stores or retrieval.search(question(state))}


def product_search(state: State):
    i = state["intent"]
    pages = elaborating(state)
    found = [p for p in products() if p["url"] in pages]  # for "explain more": the products recommended before
    if not found:
        found = retrieval.search_products(question(state), i.section, i.color, i.max_price)
    if i.alternatives:  # "any others?": not the products recommended before
        found = [p for p in found if p["url"] not in (state.get("previous_sources") or [])]
    return {"sources": [product_source(p) for p in found]}


def product_source(p: dict, note: str = "") -> dict:
    return {
        "title": p["name"],
        "url": p["url"],
        "text": f"{p['name']} - {p['color']}, {p['material']}, EUR {p['price']:.0f}. {p['description']}{note}",
        "product": card(p),
    }


def style_brief(state: State):
    """Updates the saved details with the latest message, then asks what's missing (at most two questions) or goes on
    to build the outfit from them."""
    saved = state.get("previous_brief")
    # Sections aren't a customer detail: they're chosen again from what the customer wants now (a coat, not the suit)
    details = Brief(**saved).model_dump_json(exclude={"ready", "questions", "sections"}) if saved else "(none yet)"
    prompt = with_history(BRIEF_PROMPT.format(details=details), state)
    b = briefer.invoke([SystemMessage(prompt), HumanMessage(state["messages"][-1].content)])
    b.ready = b.ready or not b.questions.strip()  # "not ready" without a question would send an empty reply
    if b.ready:
        return {"brief": b}
    return {"brief": b, "messages": [AIMessage(b.questions)], "sources": []}


def style_search(state: State):
    """The products recommended before, the occasion page's advice and the products Suitsupply features for it, then
    the best matches per section. No single product above the budget."""
    b, i, page = state["brief"], state["intent"], occasions().get(state["intent"].occasion)
    by_id = {p["id"]: p for p in products()}
    limit = b.budget_left if b.budget_left is not None else b.budget  # what's left after the chosen products
    previous = state.get("previous_sources") or []
    # So the stylist can keep or swap them when the customer changes their mind; left out when they ask for others
    earlier = [] if i.alternatives else [p for p in products() if p["url"] in previous]
    # "Other options" for the same look: the pieces of the earlier outfit if the stylist left none
    sections = b.sections or (i.alternatives and (state.get("previous_brief") or {}).get("sections")) or []
    picks = [by_id[pid] for pid in page["products"] if pid in by_id] if page else []
    picks = [p for p in picks if not limit or p["price"] <= limit]
    # At most two per piece of the outfit: a page of twelve suits would leave no room for the shirt and shoes
    picks = [p for section in sections for p in [q for q in picks if section_of(q) == section][:2]]
    k = 3 + len(previous) if i.alternatives else 3  # the earlier products are dropped below, so fetch more
    found = earlier + picks + [
        p for section in sections for p in retrieval.search_products(b.query(), section, max_price=limit, k=k)
    ]
    found = list({p["id"]: p for p in found}.values())  # a pick can also be a search match
    if i.alternatives:
        found = [p for p in found if p["url"] not in previous]
    featured = {p["id"] for p in picks}
    advice = retrieval.from_pages([page["url"]], b.query(), k=3) if page else []

    def note(p: dict) -> str:
        text = f" Featured in Suitsupply's {page['title']}." if p["id"] in featured else ""
        # The arithmetic done here: the model got "EUR 149 of EUR 151 left" wrong
        if limit:
            text += f" Leaves EUR {limit - p['price']:.0f} of the EUR {limit:.0f} budget left."
        return text

    return {"sources": advice + [product_source(p, note(p)) for p in found]}


def numbered(sources: list[dict]) -> str:
    return "\n\n".join(
        f"[{i}] {s['title']}" + (f" > {s['heading']}" if s.get("heading") else "") + f"\n{s['text']}"
        for i, s in enumerate(sources, 1)
    ) or "(none)"


def answer(state: State):
    # Product questions need recommendations, which a strict "only what the sources say" prompt refuses to give
    prompt = {"product": PRODUCT_PROMPT, "style": STYLE_PROMPT}.get(state["intent"].intent, ANSWER_PROMPT)
    system = with_history(prompt.format(sources=numbered(state["sources"])), state)
    if state["intent"].elaborate:
        system = f"{ELABORATE}\n\n{system}"  # in front, like the conversation, so it isn't lost behind the sources
    if (v := state.get("verdict")) and not v.qualified:
        system = f"{RETRY.format(problems=v.problems)}\n\n{system}"
    reply = llm.invoke([SystemMessage(system), HumanMessage(question(state))])
    if NO_ANSWER in reply.text:
        return {"fallback": True}
    # A citation to no source (e.g. a price written as [449]) is a slip, not a reason for the judge to reject the answer
    n = len(state["sources"])
    draft = CITATION.sub(lambda m: m[0] if all(1 <= int(d) <= n for d in re.findall(r"\d+", m[0])) else "", reply.text)
    return {"draft": draft, "drafts": state.get("drafts", 0) + 1}


def cited_numbers(text: str) -> set[int]:
    # Handles [2] as well as [2, 5] in case the model groups citations
    return {int(n) for group in re.findall(r"\[([\d,\s]+)\]", text) for n in re.findall(r"\d+", group)}


def over_budget(state: State) -> str:
    """The budget check, in code: language models add up prices unreliably, both when writing and when judging.
    An outfit's cited products, apart from the ones already chosen, must fit the budget left."""
    b = state.get("brief")
    limit = b and (b.budget_left if b.budget_left is not None else b.budget)
    if state["intent"].intent != "style" or not limit:
        return ""
    cited = cited_numbers(state["draft"])
    total = sum(
        s["product"]["price"] for n, s in enumerate(state["sources"], 1)
        if n in cited and "product" in s and s["product"]["name"].lower() not in b.chosen.lower()
    )
    if total <= limit:
        return ""
    return (
        f"The recommended products add up to EUR {total:.0f}, EUR {total - limit:.0f} over the EUR {limit:.0f} budget "
        f"left. Recommend fewer or cheaper products that fit within EUR {limit:.0f} together and say what could be added later."
    )


def judge(state: State):
    """Checks the draft before the customer sees it: answers the question, grounded in the sources, valid citations."""
    if problem := over_budget(state):  # no need to ask the model
        return {"verdict": Verdict(qualified=False, problems=problem), "fallback": state["drafts"] >= MAX_DRAFTS}
    system = JUDGE_PROMPT.format(sources=numbered(state["sources"]))
    check = f"Customer question: {question(state)}\n\nDraft answer:\n{state['draft']}"
    # A budget means something else for a list of options than for an outfit; the judge can't tell them apart itself
    if kind := ANSWER_KIND.get(state["intent"].intent):
        check = f"Answer type: {kind}\n\n{check}"
    # Without the conversation, details the customer gave earlier (budget, occasion, what they own) look invented
    if earlier := history(state):
        check = f"Conversation so far:\n{earlier}\n\n{check}"
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
    # After the judge rejected the drafts: tell the customer why (e.g. nothing fits the budget) instead of a bare "can't help"
    if (v := state.get("verdict")) and not v.qualified:
        system = f"{FAILED.format(problems=v.problems)}\n\n{system}"
    reply = llm.invoke([SystemMessage(system), HumanMessage(question(state))])
    return {"messages": [AIMessage(reply.text)], "sources": sources, "fallback": True}


# Which node handles each intent; order, account, human and off-topic requests go to the contact options
NEXT = {
    "greeting": "smalltalk",
    "policy": "policy_search",
    "store": "policy_search",
    "product": "product_search",
    "style": "style_brief",
    "order_status": "fallback",
    "human": "fallback",
    "conversation": "recall",
    "out_of_scope": "fallback",
}

builder = StateGraph(State)
builder.add_node(intent)
builder.add_node(policy_search)
builder.add_node(product_search)
builder.add_node(style_brief)
builder.add_node(style_search)
builder.add_node(answer)
builder.add_node(judge)
builder.add_node(smalltalk)
builder.add_node(recall)
builder.add_node(fallback)
builder.add_edge(START, "intent")
builder.add_conditional_edges("intent", lambda s: NEXT[s["intent"].intent], sorted(set(NEXT.values())))
builder.add_edge("policy_search", "answer")
builder.add_edge("product_search", "answer")
# Not enough known yet: the stylist's questions are the reply, and the customer's answers start the next turn
builder.add_conditional_edges("style_brief", lambda s: "style_search" if s["brief"].ready else END, ["style_search", END])
builder.add_edge("style_search", "answer")
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
    cited = cited_numbers(reply)
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
        # The stylist's updated details, saved with the conversation for the next turn
        "brief": b.model_dump(exclude={"ready", "questions"}) if (b := state.get("brief")) else None,
    }


def inputs(message: str, memory: str, recent, previous_sources, brief: dict | None = None) -> dict:
    return {
        "messages": [HumanMessage(message)],
        "summary": memory,
        "recent": list(recent),
        "previous_sources": list(previous_sources),
        "previous_brief": brief,
    }


def run_agent(message: str, memory: str = "", session_id: str | None = None, recent=(), previous_sources=(), brief=None) -> dict:
    trace_id, config = run_config(session_id)
    return result(graph.invoke(inputs(message, memory, recent, previous_sources, brief), config=config), trace_id)


def stream_agent(message: str, memory: str = "", session_id: str | None = None, recent=(), previous_sources=(), brief=None):
    """Yield ("token", text) while the reply is written, then ("done", result).
    memory is the conversation summary, recent the last (role, text) messages before this one, previous_sources the
    urls the previous answer cited, brief the stylist's details saved with the conversation."""
    trace_id, config = run_config(session_id)
    state, streamed = None, False
    start = inputs(message, memory, recent, previous_sources, brief)
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

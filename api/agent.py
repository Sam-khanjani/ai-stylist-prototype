import os
import re
from typing import Literal

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import BaseModel, Field

import retrieval
from catalog import COLORS, OCCASIONS, SECTIONS, card, occasions, products, section_of

CHAT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Gemini 2.x takes a temperature and a seed, so the same question takes the same path (best effort, says Google).
# Gemini 3.x ignores them and thinking_budget, with a warning on every call, so they're only sent to 2.x.
SAMPLING = {} if CHAT_MODEL.startswith("gemini-3") else {
    "temperature": float(os.getenv("GEMINI_TEMPERATURE", "0")),
    "seed": int(os.getenv("GEMINI_SEED", "42")),
    "thinking_budget": int(os.getenv("GEMINI_THINKING_BUDGET", "0")),  # no thinking: faster; -1 lets the model decide
}
llm = ChatGoogleGenerativeAI(
    model=CHAT_MODEL,
    vertexai=True,
    project=retrieval.PROJECT,
    # 2.5 is offered in single EU regions; Gemini 3.x needs the "eu" multi-region
    location=os.getenv("GEMINI_LOCATION", "europe-west4"),
    **SAMPLING,
    # At temperature 0 the model can loop, repeating itself up to its 65K-token limit for minutes; replies here are a
    # few hundred tokens (a full one takes up to ~10s).
    max_output_tokens=2048,
    # Generous on purpose: the SDK sends it to the server as a deadline, and a request the server cancels (499) isn't
    # retried, while timeouts on our side, 429s and 5xx are
    timeout=60,
    max_retries=3,  # attempts, including the first
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
- For an occasion you need: the occasion, its dress code unless the occasion makes it clear (a beach wedding is
  relaxed summer smart), the season or weather, and the customer's role when it matters (at a wedding always: groom
  or guest).
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
Cite only the products to buy now; name pieces for later without a citation.
When something the customer asks for can't be met (e.g. nothing fits the budget left), say so kindly and why, then offer
the closest options you have, e.g. the cheapest fitting piece, and what a higher budget would allow. If there
is nothing to offer, say so and give styling advice only. Answer the customer's latest message first (e.g. whether a
product comes in other colours, and the most similar products if it doesn't), then the outfit.
Match fabrics to the season: light ones (tropical wool, linen, cotton) for summer, warmer ones (flannel, heavier wool,
cashmere, velvet) for autumn and winter. Keep the formality consistent: a tuxedo shirt goes with a tuxedo or dinner
jacket, not with a regular suit.
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
   number (without it the customer gets no product card). Pieces only suggested for later, over the budget, are
   named without a citation.
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
clearly don't fit the request or the occasion, and don't invent dress-code rules the customer didn't give (an evening
wedding is not black-tie unless they say so). Reject only for real problems, not for wording or style.
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
        "the occasion the customer dresses for, also from the conversation, only when it clearly is one of these: "
        "wedding (also engagement), black-tie (gala, tuxedo, formal evening), business (office, interview, meeting), "
        "resort (beach, holiday), clubbing (night out, party). Otherwise none, e.g. for a date or a birthday"
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
    wants: str = Field("", description="what they want to buy now, in a few words, e.g. a full outfit, a suit or a short coat")
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

    def left(self) -> float | None:
        """The budget left for new pieces: the budget minus the chosen prices ("... EUR 449"), worked out here instead
        of trusting the model's budget_left, which kept the old amount after a new budget."""
        prices = re.findall(r"(?:EUR|€)\s?(\d+(?:\.\d+)?)", self.chosen)
        if self.budget is None or (self.chosen and not prices):  # nothing to work it out from
            return self.budget_left if self.budget_left is not None else self.budget
        return self.budget - sum(map(float, prices))

    def query(self) -> str:
        """What to search for: the occasion and the look. Not what they own or chose: a navy blazer in the query finds
        navy trousers and navy shirts, and the outfit turns navy head to toe."""
        return self.text(leave_out={"owns", "chosen", "budget", "budget_left"})


MAX_DRAFTS = 2  # a rejected draft is rewritten once with the judge's feedback, then it's the fallback
ANSWER_KIND = {
    "product": "product suggestions: alternatives to choose from, each within the customer's price limit; their prices "
    "are not added up",
    "style": "an outfit: the recommended products are worn together (the budget is checked separately). Style, colour "
    "and formality are the stylist's call: reject them only when they plainly contradict what the customer asked",
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


FULL_OUTFIT = ["jackets", "trousers", "shirts", "shoes"]
KEPT = ["occasion", "role", "season", "dress_code", "wants", "owns", "chosen", "budget", "colours", "look"]


def keep(b: Brief, saved: Brief):
    """Details the model left empty keep their saved value: it sometimes writes everything into one field and empties
    the rest, which lost the budget. A change of mind still replaces a detail, since the new value isn't empty.
    budget_left isn't kept: after a new budget the old amount left would be wrong (the search then uses the budget)."""
    if len(b.wants) > 150 and saved.wants:  # everything written into "wants"
        b.wants = saved.wants
    for field in KEPT:
        if getattr(b, field) in ("", None):
            setattr(b, field, getattr(saved, field))


def still_unknown(b: Brief, saved: dict | None) -> list[str]:
    """What the stylist needs before suggesting an outfit and doesn't know yet. Each is asked about once: if it was
    already unknown last turn, the stylist assumes a sensible default instead of asking again."""
    unknown = []
    if not b.occasion and not b.owns:
        unknown.append("the occasion or the clothes to build on")
    if not b.season:
        unknown.append("the season or month")
    if "wedding" in b.occasion.lower() and not b.role:
        unknown.append("whether they are the groom or a guest")
    if saved:
        before = still_unknown(Brief(**saved), None)
        unknown = [u for u in unknown if u not in before]
    return unknown


def style_brief(state: State):
    """Updates the saved details with the latest message, then asks what's missing (at most two questions) or goes on
    to build the outfit from them."""
    saved = state.get("previous_brief")
    # Sections aren't a customer detail: they're chosen again from what the customer wants now (a coat, not the suit)
    details = Brief(**saved).model_dump_json(exclude={"ready", "questions", "sections"}) if saved else "(none yet)"
    prompt = with_history(BRIEF_PROMPT.format(details=details), state)
    message = HumanMessage(state["messages"][-1].content)
    b = briefer.invoke([SystemMessage(prompt), message])
    if saved:
        keep(b, Brief(**saved))
    for field in KEPT:  # at temperature 0 a field can repeat itself ("for a wedding guest, for a wedding guest, ...")
        if isinstance(value := getattr(b, field), str) and len(value) > 150:
            value = ", ".join(dict.fromkeys(value.split(", ")))  # drop the repeats
            setattr(b, field, value if len(value) <= 150 else value[:150].rsplit(",", 1)[0])
    b.ready = b.ready or not b.questions.strip()  # "not ready" without a question would send an empty reply
    # Gemini 2.5 says ready without the season or the role at a wedding, even when told; the code asks for them, in a
    # small call that only writes the question (in the customer's language)
    if b.ready and (unknown := still_unknown(b, saved)):
        ask = f"Ask the customer about {' and '.join(unknown)}, in one or two short, warm questions. Only the questions."
        b.ready, b.questions = False, llm.invoke([SystemMessage(with_history(ask, state)), message]).text
    # A new budget the model didn't pick up: it copied the saved one, while intent detection read the new amount
    if saved and (new := state["intent"].max_price) and b.budget == Brief(**saved).budget != new:
        b.budget = new
    # Same for a piece named now: "wants" is what the customer just asked for, not the copied "a suit"
    if saved and wanted_piece(state["intent"], b) and b.wants == Brief(**saved).wants:
        b.wants = state["messages"][-1].content[:150]
    if b.ready:
        return {"brief": b}
    return {"brief": b, "messages": [AIMessage(b.questions)], "sources": []}


def wanted_piece(i: Intent, b: Brief) -> bool:
    """The message names a kind of product to find ("a coat instead"), not one the customer owns or chose ("if I take
    this jacket")."""
    return bool(i.section) and i.section.rstrip("s") not in f"{b.chosen} {b.owns}".lower()


def off_season(season: str) -> tuple[str, ...]:
    """Fabric words that don't suit the season: light ones in the cold months, heavy ones in summer."""
    season = season.lower()
    if any(m in season for m in ("oct", "nov", "dec", "jan", "feb", "autumn", "fall", "winter")):
        return ("linen", "tropical", "seersucker")
    if any(m in season for m in ("jun", "jul", "aug", "summer")):
        return ("flannel", "velvet", "corduroy", "tweed")
    return ()


def style_search(state: State):
    """The products recommended before, the occasion page's advice and the products Suitsupply features for it, then
    the best matches per section. No single product above the budget."""
    b, i, page = state["brief"], state["intent"], occasions().get(state["intent"].occasion)
    by_id = {p["id"]: p for p in products()}
    limit = b.left()  # what's left after the chosen products
    previous = state.get("previous_sources") or []
    # So the stylist can keep or swap them when the customer changes their mind; left out when they ask for others
    earlier = [] if i.alternatives else [p for p in products() if p["url"] in previous]
    # No sections chosen (a broken brief, or "other options"): the pieces of the earlier look, or a full outfit
    sections = b.sections or (state.get("previous_brief") or {}).get("sections") or FULL_OUTFIT
    # A piece the customer names now, as intent detection read it (Gemini 2.5 kept the saved "a suit"). In a follow-up
    # it's the only piece searched ("a coat instead"); in a first message it comes first in the outfit
    if wanted_piece(i, b):
        sections = [i.section] if state.get("previous_brief") else [i.section, *(s for s in sections if s != i.section)]

    # What doesn't suit the occasion is left out in code: the prompt asks for it, but Gemini 2.5 picked tropical wool
    # for December and, with those gone, a tuxedo for a guest at an evening wedding
    wrong = off_season(b.season)
    # Suitsupply's wedding guide: tuxedos are for black-tie weddings, a dark suit for the others
    no_tuxedo = "wedding" in b.occasion.lower() and i.occasion != "black-tie" and "black" not in b.dress_code.lower()

    def fits(p: dict) -> bool:
        name, material = p["name"].lower(), (p["material"] or "").lower()
        if any(f in material for f in wrong):
            return False
        return not (no_tuxedo and ("tuxedo" in name or "dinner jacket" in name))

    picks = [by_id[pid] for pid in page["products"] if pid in by_id] if page else []
    picks = [p for p in picks if (not limit or p["price"] <= limit) and fits(p)]
    # At most two per piece of the outfit: a page of twelve suits would leave no room for the shirt and shoes
    picks = [p for section in sections for p in [q for q in picks if section_of(q) == section][:2]]
    k = 3 + len(previous) if i.alternatives else 3  # the earlier products are dropped below, so fetch more
    # Extra candidates, so leaving out what doesn't fit still leaves k per section
    found = earlier + picks + [
        p for section in sections
        for p in [q for q in retrieval.search_products(b.query(), section, max_price=limit, k=k + 6) if fits(q)][:k]
    ]
    # The latest message itself, for asks about a specific product the outfit search misses ("this vest in another
    # colour?"). Only then: "I own a navy blazer, ..." would find more navy pieces
    if i.alternatives or i.section:
        asked = i.question or state["messages"][-1].content
        found += [p for p in retrieval.search_products(asked, max_price=limit, k=6) if fits(p)][:4]
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


def too_expensive(state: State) -> tuple[float | None, list[tuple[int, dict]]]:
    """The budget check, in code: language models add up prices unreliably, both when writing and when judging.
    An outfit's cited products, apart from the ones already chosen, must fit the budget left. They're added up in the
    order the answer mentions them; a second product of the same kind is an option (the same shirt in another colour),
    not bought too. Returns the budget left and the cited products that don't fit after the ones before them."""
    b = state.get("brief")
    limit = b and b.left()
    if state["intent"].intent != "style" or not limit:
        return limit, []
    draft = state["draft"]
    total, kinds, extra = 0, set(), []
    for n in sorted(cited_numbers(draft), key=lambda n: draft.find(f"[{n}]")):
        s = state["sources"][n - 1] if n <= len(state["sources"]) else {}
        if "product" not in s or s["product"]["name"].lower() in b.chosen.lower():
            continue
        kind, price = section_of(s["product"]), s["product"]["price"]
        if kind in kinds:
            continue
        if total + price <= limit:
            total, kinds = total + price, kinds | {kind}
        else:
            extra.append((n, s["product"]))
    return limit, extra


def over_budget(state: State) -> str:
    """Names exactly what doesn't fit: Gemini 2.5 ignored the general "don't cite pieces for later"."""
    limit, extra = too_expensive(state)
    if not extra:
        return ""
    names = ", ".join(f"[{n}] {p['name']} (EUR {p['price']:.0f})" for n, p in extra)
    return (
        f"With the EUR {limit:.0f} budget left, these don't fit after the pieces before them: {names}. "
        "Remove their citation numbers and mention them only as additions for later, or swap them for cheaper products."
    )


def within_budget(state: State) -> str:
    """The last draft with what doesn't fit turned into an idea for later: no card, and a note saying why. Gemini 2.5
    kept adding prices up wrong after the feedback, and the contact reply helps the customer less than this."""
    limit, extra = too_expensive(state)
    draft = state["draft"]
    for n, _ in extra:
        draft = re.sub(rf"\s*\[{n}\]", "", draft)
    names = " and ".join(f"the {p['name']} (EUR {p['price']:.0f})" for _, p in extra)
    return f"{draft}\n\nNote: {names} would go over the EUR {limit:.0f} you have left, so take it as an idea for later."


def judge(state: State):
    """Checks the draft before the customer sees it: answers the question, grounded in the sources, valid citations."""
    if problem := over_budget(state):  # no need to ask the model
        if state["drafts"] >= MAX_DRAFTS:  # last try: send it without what doesn't fit, rather than the contact reply
            return {"verdict": Verdict(qualified=True), "messages": [AIMessage(within_budget(state))]}
        return {"verdict": Verdict(qualified=False, problems=problem)}
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

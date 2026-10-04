# Service assistant graph

The LangGraph agent in `api/agent.py`. Dashed arrows are conditional edges.
Every node gets the conversation so far: a summary plus the last 10 messages.
Answers are checked by the judge before they're sent, so they arrive in one piece instead of streaming.
The summary is not a graph node: the api updates it after the reply, when it's worth it.

```mermaid
flowchart TD
    start([start]) --> intent

    intent{{"intent<br/><small>intent, standalone question, product filters,<br/>elaborate flag for 'explain more'</small>"}}
    intent -. greeting .-> smalltalk
    intent -. conversation .-> recall
    intent -. "policy, store" .-> policy_search
    intent -. product .-> product_search
    intent -. style .-> style_brief
    intent -. "order_status, human, out_of_scope" .-> fallback

    style_brief{{"style_brief<br/><small>updates the details saved with the conversation<br/>(occasion, season, role, budget, what they want, what they own...)<br/>with the latest message, then: enough known for an outfit?</small>"}}
    style_brief -. "missing info: up to 2 questions" .-> finish
    style_brief -. ready .-> style_search
    style_search["style_search<br/><small>occasion page advice, Suitsupply's picks for it,<br/>best matches per section</small>"] --> answer

    policy_search["policy_search<br/><small>hybrid search over the knowledge pages,<br/>all stores of a country,<br/>or the previous answer's pages for 'explain more'</small>"] --> answer
    product_search["product_search<br/><small>semantic search with section, colour<br/>and price filters, or the products<br/>recommended before for 'explain more'</small>"] --> answer

    answer["answer<br/><small>draft from the numbered sources, with citations</small>"]
    answer -. draft .-> judge
    answer -. "NO_ANSWER" .-> fallback

    judge{{"judge<br/><small>answers the question? grounded in the sources?<br/>citations right? clear and polite?</small>"}}
    judge -. qualified .-> finish
    judge -. "rejected (1st draft): rewrite with feedback" .-> answer
    judge -. "rejected again" .-> fallback

    smalltalk["smalltalk<br/><small>short friendly reply, no sources</small>"] --> finish
    recall["recall<br/><small>answers about the chat itself,<br/>from the conversation history only</small>"] --> finish
    fallback["fallback<br/><small>contact options and store appointments</small>"] --> finish

    finish([end])

    finish -. "after the reply is sent" .-> summarize
    summarize[/"summarize, outside the graph (api/main.py)<br/><small>cheaper gemini-2.5-flash-lite; only after a policy, store, product or style turn<br/>in a chat of 10+ messages or 2,500+ characters</small>"/]
```

| Intent | Route (eval, dashboard, events table) | Next node |
|---|---|---|
| greeting | other | smalltalk |
| policy | policy | policy_search |
| store | policy | policy_search |
| product | product | product_search |
| style | style | style_brief |
| order_status | other | fallback |
| human | other | fallback |
| conversation | other | recall |
| out_of_scope | other | fallback |

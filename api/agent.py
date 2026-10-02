import os

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph


def respond(state: MessagesState):
    # Placeholder node, the real assistant replaces this later
    return {"messages": [AIMessage(f"You said: {state['messages'][-1].content}")]}


builder = StateGraph(MessagesState)
builder.add_node("respond", respond)
builder.add_edge(START, "respond")
builder.add_edge("respond", END)
graph = builder.compile()

# Tracing is optional so the api runs locally without Langfuse keys
tracing = bool(os.getenv("LANGFUSE_SECRET_KEY"))
if tracing:
    from langfuse.langchain import CallbackHandler

    langfuse_handler = CallbackHandler()


def run_agent(message: str, session_id: str | None = None) -> str:
    config = {
        "run_name": "stylist-agent",
        "callbacks": [langfuse_handler] if tracing else [],
        "metadata": {"langfuse_session_id": session_id} if session_id else {},
    }
    result = graph.invoke({"messages": [HumanMessage(message)]}, config=config)
    return result["messages"][-1].content


def flush():
    if tracing:
        from langfuse import get_client

        get_client().flush()

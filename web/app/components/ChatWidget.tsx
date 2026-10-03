"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import type { Card } from "@/lib/card";
import ProductCard from "./ProductCard";

type Source = { n: number; title: string; url: string };

type Message = {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
  products?: (Card & { n: number })[];
  fallback?: boolean;
  traceId?: string | null;
  vote?: 0 | 1;
};

type Conversation = { id: string; title: string; updated_at: string };

// Shape stored by the api's history
type StoredMessage = {
  role: Message["role"];
  text: string;
  sources: Source[] | null;
  products: Message["products"] | null;
  fallback: boolean | null;
  trace_id: string | null;
  vote: 0 | 1 | null;
};

const fromStored = (m: StoredMessage): Message => ({
  role: m.role,
  text: m.text,
  sources: m.sources ?? undefined,
  products: m.products ?? undefined,
  fallback: m.fallback ?? undefined,
  traceId: m.trace_id,
  vote: m.vote ?? undefined,
});

const SUGGESTIONS = [
  "How can I return a shirt?",
  "Show me a navy suit under €700",
  "Which stores are in the Netherlands?",
  "Do I need an appointment?",
];

// Turn [2] into a link to source 2, once the sources are known
function linkCitations(text: string, sources: Source[] = []) {
  return text.replace(/\[(\d+)\]/g, (match, n) => {
    const source = sources.find((s) => s.n === Number(n));
    return source ? `[[${n}]](${source.url})` : match;
  });
}

// Several cited chunks can come from the same page, show each link once
function uniqueLinks(sources: Source[]) {
  return [...new Map(sources.map((s) => [s.url, s])).values()];
}

function TypingDots() {
  return (
    <div className="flex gap-1 py-2" aria-label="Assistant is typing">
      {[0, 150, 300].map((delay) => (
        <span key={delay} className="size-1.5 animate-bounce rounded-full bg-gray-600" style={{ animationDelay: `${delay}ms` }} />
      ))}
    </div>
  );
}

export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function loadConversations() {
    const res = await fetch("/api/conversations");
    const list: Conversation[] = res.ok ? await res.json() : [];
    setConversations(list);
    return list;
  }

  async function openConversation(id: string) {
    const res = await fetch(`/api/conversations/${id}`);
    if (!res.ok) return;
    const data: { messages: StoredMessage[] } = await res.json();
    setConversationId(id);
    setMessages(data.messages.map(fromStored));
    setShowHistory(false);
  }

  // First time the panel opens: continue the most recent chat of this browser
  useEffect(() => {
    if (!open || conversations !== null) return;
    loadConversations().then((list) => list[0] && openConversation(list[0].id));
  }, [open]);

  // Keep the newest text in view while the answer streams in
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    // Listen on the whole page: focus may have left the panel (e.g. after a vote button is disabled)
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const updateLast = (patch: Partial<Message>) =>
    setMessages((m) => [...m.slice(0, -1), { ...m[m.length - 1], ...patch }]);

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text }, { role: "assistant", text: "" }]);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
      });
      if (!res.ok || !res.body) throw new Error(`chat returned ${res.status}`);

      // Server-Sent Events: blocks separated by a blank line, each with an "event:" and a "data:" line
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      let reply = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          const event = block.match(/^event: (.*)$/m)?.[1];
          const data = JSON.parse(block.match(/^data: (.*)$/m)?.[1] ?? "null");
          if (event === "conversation") setConversationId(data);
          if (event === "token") updateLast({ text: (reply += data) });
          if (event === "done") {
            updateLast({
              text: data.reply,
              sources: data.sources,
              products: data.products,
              fallback: data.fallback,
              traceId: data.trace_id,
            });
            // The stream stays open a moment longer while the api updates its memory; no need to wait for that
            setBusy(false);
          }
        }
      }
    } catch {
      updateLast({ text: "Sorry, something went wrong. Please try again." });
    }
    setBusy(false);
    inputRef.current?.focus();
    loadConversations();
  }

  function vote(index: number, value: 0 | 1) {
    const message = messages[index];
    if (!message.traceId || message.vote !== undefined) return;
    setMessages((m) => m.map((msg, i) => (i === index ? { ...msg, vote: value } : msg)));
    fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trace_id: message.traceId, value }),
    });
  }

  function newChat() {
    setMessages([]);
    setConversationId(null);
    setShowHistory(false);
    inputRef.current?.focus();
  }

  async function deleteAll() {
    if (!confirm("Delete all your chats? This can't be undone.")) return;
    await fetch("/api/conversations", { method: "DELETE" });
    setConversations([]);
    newChat();
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    send(input);
  }

  return (
    <>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={open ? "Close assistant" : "Open assistant"}
        className="fixed bottom-4 left-4 z-30 flex items-center gap-2 rounded-full bg-gray-800 py-3 pr-5 pl-4 text-sm font-medium text-white shadow-lg transition-transform hover:scale-105 hover:bg-gray-900"
      >
        <span aria-hidden className="text-base leading-none">{open ? "✕" : "💬"}</span>
        {open ? "Close" : "Ask the assistant"}
      </button>

      <section
        aria-label="Assistant"
        className={`fixed inset-0 z-40 flex origin-bottom-left flex-col bg-background shadow-2xl transition-all duration-200 sm:inset-auto sm:bottom-20 sm:left-4 sm:h-[min(640px,calc(100vh-7rem))] sm:w-[400px] sm:rounded-lg sm:border sm:border-border ${
          open ? "scale-100 opacity-100" : "pointer-events-none scale-95 opacity-0"
        }`}
      >
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <h2 className="text-sm font-medium">Stylist assistant</h2>
            <p className="text-xs text-text-secondary">Returns, delivery, stores, sizing and products</p>
          </div>
          <div className="flex gap-1">
            {!!conversations?.length && (
              <button
                onClick={() => setShowHistory((h) => !h)}
                className="rounded px-2 py-1 text-xs text-text-secondary hover:bg-surface hover:text-text"
              >
                {showHistory ? "Back" : "History"}
              </button>
            )}
            {(messages.length > 0 || showHistory) && (
              <button onClick={newChat} className="rounded px-2 py-1 text-xs text-text-secondary hover:bg-surface hover:text-text">
                New chat
              </button>
            )}
            <button
              onClick={() => setOpen(false)}
              aria-label="Close assistant"
              className="rounded px-2 py-1 text-text-secondary hover:bg-surface hover:text-text"
            >
              ✕
            </button>
          </div>
        </header>

        {showHistory && (
          <div className="flex-1 overflow-y-auto px-4 py-4">
            <h3 className="text-xs text-text-secondary">Your chats from the last 24 hours</h3>
            <ul className="mt-3 divide-y divide-border">
              {conversations?.map((c) => (
                <li key={c.id}>
                  <button
                    onClick={() => openConversation(c.id)}
                    className={`w-full py-3 text-left hover:text-text-secondary ${c.id === conversationId ? "font-medium" : ""}`}
                  >
                    <span className="block truncate text-sm">{c.title}</span>
                    <span className="text-xs text-text-secondary">{new Date(c.updated_at).toLocaleString()}</span>
                  </button>
                </li>
              ))}
            </ul>
            <button onClick={deleteAll} className="mt-6 text-xs text-text-secondary underline hover:text-text">
              Delete my chats
            </button>
          </div>
        )}

        <div ref={scrollRef} className={`flex-1 space-y-6 overflow-y-auto px-4 py-4 ${showHistory ? "hidden" : ""}`}>
          {messages.length === 0 && (
            <div>
              <p className="text-sm">Hi, welcome! I'm your style and service assistant.</p>
              <p className="mt-2 text-sm">
                I can help you find the right outfit, answer questions about shipping, returns and sizing, or find a store
                near you. How can I help you today?
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                {SUGGESTIONS.map((q) => (
                  <button
                    key={q}
                    onClick={() => send(q)}
                    className="rounded-full border border-border px-3 py-1.5 text-xs transition-colors hover:border-gray-800 hover:bg-gray-800 hover:text-white"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="ml-auto w-fit max-w-[85%] rounded-lg rounded-br-sm bg-gray-800 px-3 py-2 text-sm text-white">
                {m.text}
              </div>
            ) : (
              <div key={i} className="max-w-full text-sm leading-6">
                {m.text ? (
                  <div className={`chat-markdown ${m.fallback ? "rounded-md bg-surface px-3 py-2" : ""}`}>
                    <ReactMarkdown
                      components={{
                        a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" className="underline" />,
                      }}
                    >
                      {linkCitations(m.text, m.sources)}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <TypingDots />
                )}

                {m.products && m.products.length > 0 && (
                  <ul className="-mx-4 mt-3 flex snap-x gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:thin]">
                    {m.products.map((p) => (
                      <li key={p.id} className="w-36 shrink-0 snap-start">
                        <ProductCard product={p} />
                      </li>
                    ))}
                  </ul>
                )}

                {m.sources && m.sources.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs text-text-secondary">
                    <span>Sources:</span>
                    {uniqueLinks(m.sources).map((s) => (
                      <a key={s.url} href={s.url} target="_blank" rel="noopener noreferrer" className="underline hover:text-text">
                        {s.title}
                      </a>
                    ))}
                  </div>
                )}

                {m.traceId && (
                  <div className="mt-2 flex items-center gap-1 text-text-secondary">
                    {([1, 0] as const).map((value) => (
                      <button
                        key={value}
                        onClick={() => vote(i, value)}
                        disabled={m.vote !== undefined}
                        aria-label={value ? "Helpful" : "Not helpful"}
                        className={`rounded px-1.5 py-0.5 text-xs transition-transform ${
                          m.vote === value ? "scale-110 bg-surface" : "hover:scale-110 hover:bg-surface"
                        } disabled:cursor-default ${m.vote !== undefined && m.vote !== value ? "opacity-30" : ""}`}
                      >
                        {value ? "👍" : "👎"}
                      </button>
                    ))}
                    {m.vote !== undefined && <span className="ml-1 text-xs">Thanks for the feedback</span>}
                  </div>
                )}
              </div>
            ),
          )}
        </div>

        <form onSubmit={submit} className="flex gap-2 border-t border-border p-3">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={busy ? "Answering…" : "Ask a question…"}
            className="flex-1 rounded-md border border-border px-3 py-2 text-sm outline-none focus:border-gray-600"
          />
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="rounded-md bg-gray-800 px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-40"
          >
            Send
          </button>
        </form>
        <p className="px-3 pb-2 text-[10px] leading-4 text-text-secondary">
          AI answers can be wrong. Chats are saved for 24 hours in this browser so you can continue later; delete them
          anytime under History. Please don&apos;t share personal details. Unofficial demo, not affiliated with Suitsupply.
        </p>
      </section>
    </>
  );
}

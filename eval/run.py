"""Run the golden dataset through the agent, score every answer and compare with the baseline.

    python eval/run.py                  # run all questions, compare with eval/baseline.json
    python eval/run.py --only g04,g23   # run a few questions
    python eval/run.py --judge          # also measure faithfulness, citation precision and relevance with Gemini
    python eval/run.py --save-baseline  # make this run the new baseline

Exits with code 1 when any question fails, so it can gate CI. The baseline comparison shows what changed.
Needs the same environment as the api (PG* variables for Cloud SQL, Google login for Vertex AI).
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "api"))
# Separate eval traces from real usage in Langfuse and the admin monitoring
os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "eval")

import agent  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

GOLDEN = HERE / "golden.jsonl"
BASELINE = HERE / "baseline.json"
RESULTS = HERE / "results"

# Deterministic checks: a question passes only if all that apply are perfect. answer_accuracy = share of passed questions.
CHECKS = [
    "intent_accuracy",    # intent detection chose the expected intent
    "route_accuracy",     # intent detection chose the expected route
    "fallback_accuracy",  # contact fallback shown exactly when expected
    "retrieval_recall",   # an expected page is in the retrieved top-k (hit@k)
    "citation_recall",    # the answer cites an expected page
    "citation_validity",  # the answer cites something, and every [n] points to an existing source
    "fact_recall",        # share of expected facts present in the answer
    "product_accuracy",   # product cards satisfy the requested section, color and price
    "no_repeat_greeting", # mid-conversation replies don't start with "Hello" again
    "asks_when_unclear",  # the stylist asks questions (no products yet) exactly when the request is incomplete
]
NO_CITATIONS = {"greeting", "conversation"}  # small talk and questions about the chat itself have no sources
# Reported but not pass/fail: ranking quality, and LLM-graded scores that can vary between runs
INFO = ["retrieval_mrr"]
JUDGE = ["faithfulness", "citation_precision", "answer_relevance"]

JUDGE_PROMPT = """You grade an answer of a store's customer service assistant against its numbered sources.
Split the answer into its individual factual claims. For each claim:
- supported: true if any of the sources states it (contact details and "I can't help with that" statements count as claims).
- citations: the source numbers the answer attached to this claim, e.g. [2] -> 2.
- supporting_citations: the subset of those citations whose source really states the claim.
Also decide relevant: the answer addresses the customer's question, or clearly says it cannot help and offers an alternative.

Sources:
{sources}"""


class Claim(BaseModel):
    claim: str
    supported: bool
    citations: list[int] = Field(default_factory=list)
    supporting_citations: list[int] = Field(default_factory=list)


class Verdict(BaseModel):
    claims: list[Claim]
    relevant: bool


def product_ok(cards: list[dict], want: dict) -> bool:
    if len(cards) < want.get("min_cards", 1):
        return False
    return all(
        (not want.get("section") or f"/{want['section']}/" in p["url"])
        and (not want.get("color") or want["color"] in (p["color"] or "").lower())
        and (not want.get("max_price") or p["price"] <= want["max_price"])
        and p["url"] not in want.get("exclude", [])  # e.g. "any other suggestion?" must not repeat the previous ones
        for p in cards
    )


def check(g: dict, state: dict, out: dict) -> dict:
    """Deterministic metrics. None means the metric does not apply to this question."""
    relevant = lambda url: any(pattern in url for pattern in g["sources"])  # noqa: E731
    ranks = [i for i, s in enumerate(state["sources"], 1) if relevant(s["url"])]
    numbers = [int(n) for group in re.findall(r"\[([\d,\s]+)\]", out["reply"]) for n in re.findall(r"\d+", group)]
    return {
        "intent_accuracy": out.get("intent") == g["intent"] if g.get("intent") else None,
        "route_accuracy": out["route"] == g["route"] if g.get("route") else None,
        "fallback_accuracy": out["fallback"] == g["fallback"],
        "retrieval_recall": bool(ranks) if g["sources"] else None,
        "retrieval_mrr": 1 / ranks[0] if ranks else 0.0 if g["sources"] else None,
        "citation_recall": any(relevant(s["url"]) for s in out["sources"]) if g["sources"] else None,
        "citation_validity": None
        if g.get("intent") in NO_CITATIONS or g.get("asks")
        else bool(numbers) and all(1 <= n <= len(state["sources"]) for n in numbers),
        "fact_recall": sum(bool(re.search(f, out["reply"], re.I)) for f in g["facts"]) / len(g["facts"]) if g["facts"] else None,
        "product_accuracy": product_ok(out["products"], g["products"]) if g.get("products") else None,
        "no_repeat_greeting": not re.match(r"\W*(hello|hi|hey)\b", out["reply"], re.I) if g.get("history") else None,
        "asks_when_unclear": (not out["products"] and "?" in out["reply"]) == g["asks"] if "asks" in g else None,
    }


def judge(g: dict, state: dict, out: dict) -> dict:
    # Fixed, stronger model so judge scores stay comparable when the app's model changes
    grader = ChatGoogleGenerativeAI(
        model=os.getenv("JUDGE_MODEL", "gemini-3.6-flash"), vertexai=True, project=agent.retrieval.PROJECT, location="eu"
    ).with_structured_output(Verdict)
    verdict = grader.invoke([
        SystemMessage(JUDGE_PROMPT.format(sources=agent.numbered(state["sources"]))),
        HumanMessage(f"Question: {g['question']}\n\nAnswer: {out['reply']}"),
    ])
    claims = verdict.claims
    cited = sum(len(c.citations) for c in claims)
    return {
        # share of claims backed by the sources (RAGAS-style faithfulness)
        "faithfulness": sum(c.supported for c in claims) / len(claims) if claims else None,
        # share of attached citations whose source really supports the claim
        "citation_precision": sum(len(set(c.supporting_citations) & set(c.citations)) for c in claims) / cited if cited else None,
        "answer_relevance": verdict.relevant,
        "unsupported_claims": [c.claim for c in claims if not c.supported],
    }


def run_one(g: dict, run_name: str, use_judge: bool) -> dict:
    start = time.time()
    trace_id, config = agent.run_config(session_id=run_name)  # one Langfuse session per eval run
    state = {}
    try:
        # Multi-turn cases bring the earlier messages and the pages the previous answer cited
        history = [tuple(m) for m in g.get("history", [])]
        first = agent.inputs(g["question"], g.get("summary", ""), history, g.get("previous_sources", []), g.get("brief"))
        state = agent.graph.invoke(first, config=config)
        out = agent.result(state, trace_id)
        metrics = check(g, state, out) | (judge(g, state, out) if use_judge else {})
        error = None
    except Exception as e:  # a crash is a failed answer, not a failed run
        out, metrics, error = {"reply": "", "route": None}, {}, repr(e)

    passed = error is None and all(metrics[k] in (None, True, 1, 1.0) for k in CHECKS)
    if agent.tracing:
        from langfuse import get_client

        for k in CHECKS + INFO + JUDGE:
            if metrics.get(k) is not None:
                get_client().create_score(name=f"eval_{k}", value=float(metrics[k]), trace_id=trace_id)
        get_client().create_score(name="eval_answer_accuracy", value=float(passed), trace_id=trace_id)

    return {
        "id": g["id"],
        "category": g["category"],
        "question": g["question"],
        "reply": out["reply"],
        "route": out.get("route"),
        "latency": round(time.time() - start, 2),
        "metrics": metrics,
        "passed": passed,
        "error": error,
        # Why the app's own judge rejected the answer, so a fallback can be understood without opening the trace
        "rejected_because": v.problems if (v := state.get("verdict")) and not v.qualified else None,
        "last_draft": state.get("draft") if v and not v.qualified else None,
        "trace_id": trace_id,
    }


def summarize(results: list[dict]) -> dict:
    def mean(values):
        values = [float(v) for v in values if v is not None]
        return round(sum(values) / len(values), 3) if values else None

    by_category = defaultdict(list)
    for r in results:
        by_category[r["category"]].append(r["passed"])
    latencies = sorted(r["latency"] for r in results)
    return {
        "questions": len(results),
        "answer_accuracy": mean(r["passed"] for r in results),
        "errors": sum(bool(r["error"]) for r in results),
        "metrics": {k: mean(r["metrics"].get(k) for r in results) for k in CHECKS + INFO + JUDGE},
        "categories": {c: mean(v) for c, v in sorted(by_category.items())},
        "latency_avg": round(statistics.mean(latencies), 2),
        "latency_p95": latencies[int(0.95 * (len(latencies) - 1))],
    }


def failed_checks(r: dict) -> str:
    if r["error"]:
        return f"error: {r['error'][:80]}"
    return ", ".join(f"{k}={r['metrics'][k]}" for k in CHECKS if r["metrics"].get(k) not in (None, True, 1, 1.0))


def report(summary: dict, results: list[dict], baseline: dict | None) -> int:
    base = baseline["summary"] if baseline else {}

    def delta(new, old):
        if new is None or old is None:
            return ""
        d = new - old
        return f"  ({'+' if d >= 0 else ''}{d:.3f})" if abs(d) > 1e-9 else "  (=)"

    print(f"\nanswer_accuracy {summary['answer_accuracy']}{delta(summary['answer_accuracy'], base.get('answer_accuracy'))}"
          f"   ({sum(r['passed'] for r in results)}/{summary['questions']} questions passed every check)")
    print(f"latency avg {summary['latency_avg']}s, p95 {summary['latency_p95']}s"
          f"{delta(summary['latency_avg'], base.get('latency_avg'))}")
    if summary["errors"]:
        print(f"errors {summary['errors']}: crashed before an answer (quota, network...), so they count as failed but say nothing about quality")

    for title, keys in [("Pass/fail checks", CHECKS), ("Ranking", INFO), ("LLM judge (--judge)", JUDGE)]:
        rows = [(k, summary["metrics"][k]) for k in keys if summary["metrics"][k] is not None]
        if rows:
            print(f"\n{title}")
            for k, v in rows:
                print(f"  {k:<19} {v:<6}{delta(v, base.get('metrics', {}).get(k))}")

    print("\nCategory            answer_accuracy")
    for k, v in summary["categories"].items():
        print(f"  {k:<19} {v:<6}{delta(v, base.get('categories', {}).get(k))}")

    failures = [r for r in results if not r["passed"]]
    if failures:
        print("\nFailures")
        for r in failures:
            print(f"  {r['id']} {r['question'][:50]:<50} {failed_checks(r)}")
            if r["rejected_because"]:
                print(f"      app judge rejected it: {r['rejected_because'][:300]}")

    unsupported = [(r["id"], c) for r in results for c in r["metrics"].get("unsupported_claims", [])]
    if unsupported:
        print("\nUnsupported claims (judge)")
        for qid, claim in unsupported:
            print(f"  {qid} {claim[:100]}")

    if baseline:
        regressions, fixed = compare(results, baseline)
        print(f"\nCompared with baseline {baseline['run']}: {len(regressions)} regression(s), {len(fixed)} fixed")
        for r in regressions:
            label = "ERROR     " if r["error"] else "REGRESSION"
            print(f"  {label} {r['id']} {r['question'][:50]}  {failed_checks(r)}")
        for r in fixed:
            print(f"  fixed      {r['id']} {r['question'][:50]}")
    else:
        print("\nNo baseline yet. Run with --save-baseline to create one.")

    passed = all(r["passed"] for r in results)
    print(f"\nGate: {'PASSED' if passed else 'FAILED'} (every question must pass)")
    return 0 if passed else 1


def compare(results: list[dict], baseline: dict) -> tuple[list[dict], list[dict]]:
    """Regressions passed in the baseline and fail now; fixed is the opposite."""
    was = {r["id"]: r["passed"] for r in baseline["results"]}
    regressions = [r for r in results if was.get(r["id"]) and not r["passed"]]
    fixed = [r for r in results if was.get(r["id"]) is False and r["passed"]]
    return regressions, fixed


def save_to_db(run_name: str, summary: dict, results: list[dict], baseline: dict | None, saved_as_baseline: bool):
    """Full runs go to the database so the admin dashboard can show the trend and the gate history."""
    import history
    import psycopg

    failures = [{"id": r["id"], "question": r["question"], "checks": failed_checks(r)} for r in results if not r["passed"]]
    regressions, fixed = compare(results, baseline) if baseline else ([], [])
    brief = lambda rs: [{"id": r["id"], "question": r["question"]} for r in rs]  # noqa: E731
    history.init()
    with psycopg.connect() as conn:
        conn.execute(
            "INSERT INTO eval_runs (run, summary, failures, gate_passed, baseline, regressions, fixed, saved_as_baseline)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run_name,
                json.dumps(summary),
                json.dumps(failures),
                not failures,  # the gate passes only when every question passes
                baseline["run"] if baseline else None,
                json.dumps(brief(regressions)),
                json.dumps(brief(fixed)),
                saved_as_baseline,
            ),
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma separated question ids")
    ap.add_argument("--judge", action="store_true", help="add faithfulness, citation precision and relevance graded by Gemini")
    ap.add_argument("--save-baseline", action="store_true")
    args = ap.parse_args()

    golden = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    if args.only:
        golden = [g for g in golden if g["id"] in args.only.split(",")]

    run_name = "eval-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    for g in golden:
        r = run_one(g, run_name, args.judge)
        results.append(r)
        print(f"{'PASS' if r['passed'] else 'FAIL'} {r['id']} {r['latency']:>5}s  {g['question'][:60]}")
    agent.flush()

    summary = summarize(results)
    run = {"run": run_name, "judge": args.judge, "summary": summary, "results": results}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{run_name}.json").write_text(json.dumps(run, indent=1, ensure_ascii=False))

    # Only compare full runs; a subset would show every skipped question as missing
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() and not args.only else None
    code = report(summary, results, baseline)
    if not args.only:
        save_to_db(run_name, summary, results, baseline, args.save_baseline)

    if args.save_baseline:
        BASELINE.write_text(json.dumps(run, indent=1, ensure_ascii=False))
        print(f"\nSaved as baseline: {BASELINE.relative_to(HERE.parent)}")
    sys.exit(code)


if __name__ == "__main__":
    main()

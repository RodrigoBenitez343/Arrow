"""Context growth over a run, and the compute a field loop actually spends.

    .venv\\Scripts\\python.exe benchmarks\\bench_form_context.py --turns 40
    .venv\\Scripts\\python.exe benchmarks\\bench_form_context.py --budget 6000
    .venv\\Scripts\\python.exe benchmarks\\bench_form_context.py --fields 8
    .venv\\Scripts\\python.exe benchmarks\\bench_form_context.py --fixture f.json

Two modes.

TURNS (default) — no model needed: this measures the PROMPT the model would
read, which is what a cloud call is billed on and what the local server
re-evaluates.  Each turn is a query + a tool result + an answer, and the
script prints the history size and cumulative total for two policies:

  unbounded   what the store did before (turn-count cap only)
  budgeted    head kept, newest turns kept verbatim, middle collapsed to one
              marker (``add_turn(..., max_chars=...)``)

The point is the shape: unbounded grows linearly forever, budgeted flattens
once it hits the budget.

FIELDS (``--fields`` / ``--fixture``) — the REAL per-field retrieval loop of
the form node (``_ff_retrieve`` + CRAG scoring) over a document, printing each
field's wall ms, the Laya forwards it paid, and the cumulative curve.  The
forwards come from ``AI/engine_metrics.py``, which the Laya client records for
every request, so the cumulative column is the compute actually spent.
Prerequisites: the Laya GGUF + binary for real scoring (without them the loop
still runs and prints the planning/retrieval cost).

A fixture is ``{"document": "...", "fields": ["Email address*", ...]}``; a
plain JSON list is read as the field labels.
"""

import argparse
import json
import sys
import time

import bench_common as bc

bc.bootstrap()

from player.multi_sequence.worflow_interpreter_modules.executor_modules \
    .port_store import PortScopedStore                    # noqa: E402

INSTRUCTIONS = ("You are filling a job application. Answer each field from "
                "the source material and never invent a value.")

#: A compact resume, the same shape the CRAG suite uses: contact block + two
#: body sections, so a field's question has to pick a section, not a word.
DEFAULT_DOCUMENT = (
    "# Rodrigo Benitez\nEmail: benitezrodrigo169@gmail.com\n"
    "Phone: +54 11 5498-0148\nLocation: Buenos Aires, Argentina\n\n"
    "## Experience\nDevOps Engineer at Synopsys Inc., Buenos Aires, from "
    "February 2025 to today, remote.\nBuilt internal Python automations and "
    "reporting scripts for a language services company over five years.\n\n"
    "## Education\nSoftware Engineering, self taught, learned by shipping "
    "production systems rather than by coursework.\n"
)

#: The field labels a real application asks for, in page order.
DEFAULT_FIELDS = [
    "First name*", "Email address*", "Phone number*",
    "Location (city)*", "Current company", "Years of experience*",
    "Highest education", "Why do you want this role?",
]


def _turn(index: int) -> dict:
    """One realistic turn: a short query, a fat tool result, a short answer."""
    return {
        "role": "assistant",
        "query": "field %d: what should be written here?" % index,
        "tool": "context_read",
        "result": ("Source excerpt %d: " % index) + ("lorem ipsum dolor " * 40),
        "answer": "value-%d" % index,
    }


def _history_chars(store: PortScopedStore, chain="bench", node="ctx") -> int:
    turns = store.get_turns(chain, node_id=node, limit=10 ** 6)
    return sum(len(str(t.get("content") or "")) + len(str(t.get("result")
               or "")) + len(str(t.get("query") or "")) for t in turns)


def _run(turns: int, budget: int):
    store = PortScopedStore()
    store.add_turn("bench", "ctx", {"content": INSTRUCTIONS}, max_chars=budget)
    rows = []
    for index in range(1, turns + 1):
        store.add_turn("bench", "ctx", _turn(index), max_chars=budget)
        kept = store.get_turns("bench", node_id="ctx", limit=10 ** 6)
        rows.append([index, len(kept), _history_chars(store),
                     sum(1 for t in kept if t.get("role") == "note")])
    return store, rows


def _fixture(path: str):
    """``(document, fields)`` from a fixture file (list = fields only)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return DEFAULT_DOCUMENT, [str(x) for x in data]
    fields = data.get("fields") or DEFAULT_FIELDS
    return str(data.get("document") or DEFAULT_DOCUMENT), [str(x) for x in fields]


def _field_loop(document: str, labels, hold: bool = True,
                no_embed: bool = False) -> int:
    """Drive the real per-field retrieval loop; print its compute curve."""
    from AI import engine_metrics as metrics
    import AI.laya_client as laya_client
    import AI.embedding_server as embedding_server
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        .form_filler_modules._test_support import _Harness

    h = _Harness()
    h.pool = document
    # No rewrite completion: this benchmark measures retrieval + scoring, and a
    # rewrite would need a generation server on top of it.
    h._ff_llm_call = lambda prompt, system, cfg, stop_flag: ""
    if no_embed:
        h._ff_embed_call = lambda cfg: (lambda texts: [])
    cfg = h._ff_cfg({"instruction": "bench", "crag_evaluator": "auto",
                     "crag_cycles": 1, "crag_external": False,
                     "crag_rewrite": False, "engine": "llamacpp",
                     "model": ""})

    held = []
    if hold:
        if laya_client.hold():
            held.append(laya_client)
        if embedding_server.hold():
            held.append(embedding_server)
    metrics.reset()
    rows, cumulative_ms = [], 0.0
    try:
        print("laya: %s | embeddings: %s | hold: %s"
              % (laya_client.status(), "off (lexical)" if no_embed else "on",
                 bool(held)))
        print()
        for index, label in enumerate(labels, 1):
            started = time.perf_counter()
            pack = h._ff_retrieve(label, cfg, document, [])
            elapsed = (time.perf_counter() - started) * 1000.0
            cumulative_ms += elapsed
            rows.append([index, label[:26], round(elapsed, 1),
                         metrics.count("forward"), round(cumulative_ms, 1),
                         len(pack or "")])
            print("field %2d: %8.1f ms | forwards %3d | cumulative %8.1f ms"
                  % (index, elapsed, metrics.count("forward"), cumulative_ms))
    finally:
        for module in held:
            module.unhold()

    print()
    bc.table(("field", "label", "ms", "forwards", "cumulative ms",
              "pack chars"), rows)
    print()
    print("fields: %d | wall %.1f ms | forwards paid %d | per field %.1f ms"
          % (len(rows), cumulative_ms, metrics.count("forward"),
             cumulative_ms / max(1, len(rows))))
    first, second = bc.halves([r[2] for r in rows])
    print("per-field ms: first half %.1f vs second half %.1f"
          % (first, second))
    print("context trims: %d | metrics jsonl: %s"
          % (metrics.count("context_trim"), bc.jsonl_path()))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, default=40)
    parser.add_argument("--budget", type=int, default=6000,
                        help="char budget for the budgeted policy")
    parser.add_argument("--fields", type=int, default=0,
                        help="run the real field loop over N built-in labels")
    parser.add_argument("--fixture", default="",
                        help="JSON fixture: {document, fields} or a list")
    parser.add_argument("--no-embed", action="store_true",
                        help="skip the embedding server (lexical ranking)")
    parser.add_argument("--no-hold", action="store_true",
                        help="do not hold the engines between fields")
    args = parser.parse_args()

    if args.fixture:
        document, labels = _fixture(args.fixture)
        return _field_loop(document, labels, hold=not args.no_hold,
                           no_embed=args.no_embed)
    if args.fields:
        count = max(1, args.fields)
        labels = (DEFAULT_FIELDS * (count // len(DEFAULT_FIELDS) + 1))[:count]
        return _field_loop(DEFAULT_DOCUMENT, labels, hold=not args.no_hold,
                           no_embed=args.no_embed)

    turns = max(2, args.turns)
    _unbounded, free_rows = _run(turns, 0)
    _budgeted, bounded_rows = _run(turns, args.budget)

    print("turns per run: %d | budget: %d chars | unbounded = max_chars 0"
          % (turns, args.budget))
    print()
    bc.table(("turn", "turns kept", "chars (no budget)", "chars (budgeted)",
              "markers"),
             [[f[0], f[1], f[2], b[2], b[3]]
              for f, b in zip(free_rows, bounded_rows)])
    print()
    print("context growth: unbounded %.0f -> %.0f chars | budgeted %.0f -> "
          "%.0f chars"
          % (free_rows[0][2], free_rows[-1][2],
             bounded_rows[0][2], bounded_rows[-1][2]))
    print("final turn count: %d unbounded vs %d budgeted (markers included)"
          % (free_rows[-1][1], bounded_rows[-1][1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

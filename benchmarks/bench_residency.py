"""Cold-load cost of a form pass: burst policy vs one pass-long hold.

    .venv\\Scripts\\python.exe benchmarks\\bench_residency.py --fields 12
    .venv\\Scripts\\python.exe benchmarks\\bench_residency.py --mode burst

Per field the node ranks with the LOCAL embedding server and then scores with
Laya — exactly the two engines the burst policy stops between fields.  This
script runs that cycle N times and prints, per field, the embed ms, the Laya
ms, how many engine LOADS were paid so far, and the resident RSS of each
engine, so the difference between "load, use, unload" and "hold for the pass"
is a visible curve instead of an opinion.

Modes
  hold   hold both engines for the whole loop (what the form node does now)
  burst  force-stop both engines before every field (the old behaviour)

Prerequisites: the Laya GGUF (AI/models/laya-GGUF) and the embedding GGUF
(LLAMA_CPP.embedding_model_path).  Missing Laya is reported and skipped;
missing embeddings is fatal, because then nothing is measured that the
residency policy controls.
"""

import argparse
import sys
import time

import bench_common as bc

bc.bootstrap()

import AI.embedding_server as embedding_server          # noqa: E402
import AI.engine_residency as residency                 # noqa: E402
import AI.laya_client as laya_client                    # noqa: E402
from AI import engine_metrics as metrics                # noqa: E402

SAMPLES = [
    "Rodrigo Benitez, DevOps engineer, Buenos Aires Argentina",
    "benitezrodrigo169@gmail.com",
    "Professional Experience: Synopsys Inc. February 2025 to today",
]


def _embed_once() -> float:
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        .form_filler_modules.common import _FFEmbedClient
    client = _FFEmbedClient("llamacpp", "")
    started = time.perf_counter()
    vectors = client.embeddings("", list(SAMPLES))
    elapsed = (time.perf_counter() - started) * 1000.0
    if not vectors:
        bc.require(False, "the local embedding server returned no vectors - "
                          "check LLAMA_CPP.embedding_model_path")
    return elapsed


def _score_once() -> float:
    if laya_client.status() != "ready" and not laya_client.ensure_running():
        return 0.0
    started = time.perf_counter()
    laya_client.noul(SAMPLES[0], "This passage contains the answer to: full name")
    return (time.perf_counter() - started) * 1000.0


def _force_burst() -> None:
    laya_client.release(wait=1.0, force=True)
    embedding_server.release(wait=1.0, force=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fields", type=int, default=12,
                        help="how many field cycles to run (default 12)")
    parser.add_argument("--mode", choices=("hold", "burst"), default="hold")
    parser.add_argument("--budget-mb", type=int, default=None,
                        help="LOOPER_RESIDENT_BUDGET_MB for this run")
    args = parser.parse_args()

    if args.budget_mb is not None:
        import os
        os.environ["LOOPER_RESIDENT_BUDGET_MB"] = str(args.budget_mb)

    print("residency budget: %s MB free required, available %.0f MB"
          % (residency.budget_mb(), residency.available_mb()))
    held = []
    if args.mode == "hold":
        if laya_client.hold():
            held.append("laya")
        if embedding_server.hold():
            held.append("embed")
        print("held for the pass: %s" % (", ".join(held) or "(none - "
                                                 "budget refused)"))
    metrics.reset()

    rows = []
    started = time.perf_counter()
    for index in range(1, max(1, args.fields) + 1):
        if args.mode == "burst":
            _force_burst()
        embed_ms = round(_embed_once(), 1)
        laya_ms = round(_score_once(), 1)
        rows.append([index, embed_ms, laya_ms,
                     metrics.count("engine_load"),
                     round(sum(metrics.series("engine_load")), 1),
                     metrics.rss_mb("llama-server"), metrics.rss_mb("laya")])
        print("field %2d: embed %7.1f ms | laya %7.1f ms | loads so far %d"
              % (index, embed_ms, laya_ms, metrics.count("engine_load")))

    wall = time.perf_counter() - started
    for name in held:
        (laya_client if name == "laya" else embedding_server).unhold()

    print()
    bc.table(("field", "embed ms", "laya ms", "loads", "cum load ms",
              "llama-server MB", "laya MB"), rows)
    print()
    print("mode: %s | %d field(s) | wall %.1f s" % (args.mode, len(rows), wall))
    print("engine loads paid: %d (one per field is the burst policy)"
          % metrics.count("engine_load"))
    print("load time paid: %.1f ms total, %.1f ms per field"
          % (sum(metrics.series("engine_load")),
             sum(metrics.series("engine_load")) / max(1, len(rows))))
    print("mean embed ms %.1f | first half %.1f vs second half %.1f"
          % (bc.mean([r[1] for r in rows]),
             *bc.halves([r[1] for r in rows])))
    print("metrics jsonl: %s" % metrics.jsonl_path())
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""laya_bench.py — Phase-0 gates for the embedded Laya engine (Gate A / Gate B).

Run this while the app (or `AI/laya_client.ensure_running()`) can start the
Laya daemon.  Samples come from real LoOper artifacts so the gate reflects the
app's own text, not benchmark prose.

Sample file: JSONL, one object per line.

    Gate A (brain/tool picking, orchestrator picker):
        {"kind": "pick", "premise": "<goal + progress>",
         "options": ["<worker A>", "<worker B>", ...], "gold": 0}
    Gate B (binary decisions, input decision evaluator):
        {"kind": "decision", "premise": "<resolved value>",
         "criterion": "<criterion statement>", "gold": true}

    python AI/laya_bench.py --samples samples.jsonl
    python AI/laya_bench.py --samples samples.jsonl --min-acc 0.8

Exit codes: 0 = all gates pass (Laya options may ship enabled), 1 = a gate
failed (set LOOPER_LAYA=off to force every consumer onto its LLM fallback),
2 = engine unreachable / bad input.

Mining samples from real runs: take (goal, trace, candidate list, chosen
worker) rows out of LoOper logs for Gate A; take (resolved value, criterion
used, human judgment) rows for Gate B from the chains you actually run.
"""

import argparse
import json
import sys
import time


def _load_samples(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception as e:
                print(f"line {ln}: bad JSON ({e}) — skipped")
                continue
            if row.get("kind") not in ("pick", "decision"):
                print(f"line {ln}: unknown kind {row.get('kind')!r} — skipped")
                continue
            rows.append(row)
    return rows


def _run_pick(rows):
    """Gate A: choice top-1 against gold over the app's own candidate lists."""
    from AI import laya_client
    hits, n, lat = 0, 0, []
    for row in rows:
        premise = str(row.get("premise") or "")
        options = [str(o) for o in (row.get("options") or [])]
        gold = row.get("gold")
        if not options or not isinstance(gold, int):
            continue
        t0 = time.perf_counter()
        idx = laya_client.rerank(premise, options)
        lat.append(time.perf_counter() - t0)
        if idx is None:
            print("pick: Laya engine unavailable mid-run — aborting gate")
            return None
        n += 1
        hits += int(idx == gold)
    return {"n": n, "acc": (hits / n) if n else 0.0,
            "lat_ms": (1000.0 * sum(lat) / len(lat)) if lat else 0.0}


def _run_decision(rows):
    """Gate B: noul decisions against gold booleans."""
    from AI import laya_client
    hits, n, lat = 0, 0, []
    for row in rows:
        premise = str(row.get("premise") or "")
        criterion = str(row.get("criterion") or "")
        gold = row.get("gold")
        if not criterion or not isinstance(gold, bool):
            continue
        t0 = time.perf_counter()
        decided = laya_client.decision(premise, criterion)
        lat.append(time.perf_counter() - t0)
        if decided is None:
            print("decision: Laya engine unavailable mid-run — aborting gate")
            return None
        n += 1
        hits += int(bool(decided) == gold)
    return {"n": n, "acc": (hits / n) if n else 0.0,
            "lat_ms": (1000.0 * sum(lat) / len(lat)) if lat else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", required=True, help="JSONL with pick/decision rows")
    ap.add_argument("--min-acc", type=float, default=0.7,
                    help="gate threshold for both accuracy checks")
    ap.add_argument("--no-launch", action="store_true",
                    help="do not attempt the lazy daemon launch")
    args = ap.parse_args()

    from AI import laya_client

    if not args.no_launch:
        laya_client.ensure_running()
    if not laya_client.available():
        print("Laya engine not reachable (status: %s) — the app starts it "
              "from AI/bin/laya.exe; run the app once or check the bundle."
              % laya_client.status())
        return 2

    rows = _load_samples(args.samples)
    picks = [r for r in rows if r["kind"] == "pick"]
    decisions = [r for r in rows if r["kind"] == "decision"]
    print(f"loaded {len(rows)} samples: {len(picks)} pick, {len(decisions)} decision")

    ok = True
    if picks:
        res = _run_pick(picks)
        if res is None:
            return 2
        passed = res["n"] > 0 and res["acc"] >= args.min_acc
        ok &= passed
        print(f"Gate A (pick): n={res['n']} acc={res['acc']:.3f} "
              f"lat={res['lat_ms']:.0f}ms — {'PASS' if passed else 'FAIL'}")
    if decisions:
        res = _run_decision(decisions)
        if res is None:
            return 2
        passed = res["n"] > 0 and res["acc"] >= args.min_acc
        ok &= passed
        print(f"Gate B (decision): n={res['n']} acc={res['acc']:.3f} "
              f"lat={res['lat_ms']:.0f}ms — {'PASS' if passed else 'FAIL'}")

    if ok:
        print("all gates pass — Laya options may ship enabled")
        return 0
    print("gate failure — set LOOPER_LAYA=off to keep every consumer on the LLM fallback")
    return 1


if __name__ == "__main__":
    sys.exit(main())

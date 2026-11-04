"""Threshold calibration for this app's evaluator backend.

CRAG's two thresholds are the whole decision surface of the corrective
action, and upstream leaves them as CLI values (`--upper_threshold 10`,
`--lower_threshold -10` are placeholders).  This script sweeps them against a
labelled set and prints the pair that best separates "the retrieval answered
the question" from "it did not" - the numbers to put in the node's
"Correct at score" / "Wrong below score" fields.

Input: a jsonl of
    {"question": "...", "ctxs": [{"text": "...", "label": "1"|"0"}, ...]}
where a label of 1 means the passage holds the answer (the same rows
``offline/dataset.py --dataset form`` builds).

Objective: the corrective action must fire exactly when the retrieved set has
no answer at all.  ``hit`` = at least one passage labelled 1; the predicted
class is ``action != incorrect``.  Accuracy over the whole set is maximised.
"""

import argparse
import json
import os
from itertools import product

from ..evaluator import INCORRECT, classify, make_evaluator
from .dataset import load_file


def load_rows(path):
    rows = load_file(path)
    out = []
    for row in rows:
        question = str(row.get("question") or "").strip()
        ctxs = row.get("ctxs") or []
        passages = [str(c.get("text") or "").strip() for c in ctxs]
        labels = [str(c.get("label") or "0").strip() for c in ctxs]
        if not question or not passages:
            continue
        out.append((question, passages, labels))
    return out


def sweep(scored, upper_values, lower_values):
    """``scored``: [(hit, chunk_scores)] -> [(accuracy, upper, lower)]."""
    best = []
    for upper, lower in product(upper_values, lower_values):
        if lower > upper:
            continue
        correct = 0
        for hit, scores in scored:
            action = classify(scores, upper, lower)
            predicted_hit = action != INCORRECT
            correct += 1 if predicted_hit == hit else 0
        accuracy = correct / max(1, len(scored))
        best.append((accuracy, upper, lower))
    best.sort(reverse=True)
    return best


def main():
    parser = argparse.ArgumentParser(description="Calibrate CRAG thresholds.")
    parser.add_argument("--input_file", required=True,
                        help="labelled jsonl (see the module docstring)")
    parser.add_argument("--evaluator", default="auto",
                        choices=["auto", "laya", "embed", "llm"])
    parser.add_argument("--out_dir", default="",
                        help="write the best pair to out_dir/crag_thresholds.json")
    parser.add_argument("--top", type=int, default=10,
                        help="how many candidate pairs to print")
    args = parser.parse_args()

    rows = load_rows(args.input_file)
    if not rows:
        raise SystemExit("no labelled rows in %s" % args.input_file)

    embed = None
    llm = None
    if args.evaluator in ("auto", "embed"):
        try:
            from AI.embedding_server import embed_texts
            embed = lambda texts: embed_texts(list(texts)) or []  # noqa: E731
        except Exception:
            embed = None
    if args.evaluator == "llm":
        print("llm scoring needs a live engine; run it from a form node "
              "instead (crag_evaluator=llm)")

    evaluator = make_evaluator(args.evaluator, embed=embed, llm=llm)
    scored = []
    for question, passages, labels in rows:
        scores = evaluator.score_many(question, passages)
        clean = [(-1.0 if s is None else float(s)) for s in scores]
        scored.append((any(label == "1" for label in labels), clean))

    best = sweep(scored, [round(0.30 + 0.05 * i, 2) for i in range(14)],
                 [round(0.00 + 0.05 * i, 2) for i in range(20)])
    print("evaluator=%s rows=%d forwards=%d" % (
        evaluator.name, len(scored), evaluator.forwards))
    for accuracy, upper, lower in best[:max(1, args.top)]:
        print("accuracy %.3f  upper %.2f  lower %.2f"
              % (accuracy, upper, lower))

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        accuracy, upper, lower = best[0]
        path = os.path.join(args.out_dir, "crag_thresholds.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"evaluator": evaluator.name, "accuracy": accuracy,
                       "upper_threshold": upper, "lower_threshold": lower},
                      f, indent=2)
        print("wrote", path)


if __name__ == "__main__":
    main()

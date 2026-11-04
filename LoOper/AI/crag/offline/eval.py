"""CRAG evaluation: port of `utils/crag/scripts/eval.py`.

Reads the generator's output file (one answer per line, in the input order),
pairs it with the eval jsonl and reports the metric mean - ``match`` for
PopQA/PubQA style answers, ``accuracy`` for option-conditioned ones.
"""

import argparse
import json

from .metrics import match


def load_eval_file(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def score(preds, rows, metric="match"):
    results = []
    for pred, row in zip(preds, rows):
        answer = str(pred or "").strip()
        if metric == "accuracy":
            results.append(1 if answer == str(row.get("output", "")[0]) else 0)
            continue
        if "SUPPORTS" in answer:
            answer = "true"
        elif "REFUTES" in answer:
            answer = "false"
        answers = row.get("answers") or row.get("answer") or []
        if isinstance(answers, str):
            answers = [answers]
        results.append(match(answer, answers))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_file", required=True,
                        help="the eval json(l|s) built by offline/dataset.py")
    parser.add_argument("--eval_file", required=True,
                        help="one generated answer per line")
    parser.add_argument("--metric", default="match",
                        choices=["match", "accuracy"])
    args = parser.parse_args()

    rows = load_eval_file(args.input_file)
    with open(args.eval_file, "r", encoding="utf-8") as f:
        preds = [line.strip() for line in f.readlines()]
    results = score(preds, rows, args.metric)
    mean = (sum(results) / len(results)) if results else 0.0
    print(json.dumps({"metric": args.metric, "rows": len(results),
                      "mean": round(mean, 4)}))
    print("Final result: {0}".format(mean))


if __name__ == "__main__":
    main()

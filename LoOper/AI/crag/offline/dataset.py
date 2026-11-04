"""Evaluator dataset builder: port of `utils/crag/scripts/data_process.py`.

Upstream turns the Self-RAG `eval_data` files into the four artefacts the rest
of the pipeline reads:

    <dataset>/sources          one question per line
    <dataset>/retrieved_psgs    the retrieved passages, ' [sep] '-joined
    <dataset>/test_<x>.txt      'question [SEP] passage' (+\t label) per line,
                                the evaluator's own training/inference input
    <dataset>/choices           arc_challenge's option list (that task only)

``--postprocess`` also folds the prepared correct/incorrect/ambiguous files
back into the jsonl (Self-CRAG's assembled / distributed layouts).

The port keeps all of that, and adds the form-field variant this app needs:
``--dataset form`` reads a jsonl of ``{"question", "ctxs": [{"text"}],
"label": "1"|"0"}`` rows - the shape `AI/crag/offline/calibrate.py` and the
trainer expect - and writes the same three artefacts under ``form/``.
"""

import argparse
import json
import os

# Upstream's dataset -> input-file map, relative to `utils/crag`.
INPUT_FILES = {
    "popqa": "../retrieval_lm/eval_data/popqa_longtail_w_gs.jsonl",
    "pubqa": "../retrieval_lm/eval_data/health_claims_processed.jsonl",
    "bio": "../retrieval_lm/eval_data/factscore_unlabeled_alpaca_13b_retrieval.jsonl",
    "arc_challenge": "../retrieval_lm/eval_data/arc_challenge_processed.jsonl",
}


def load_file(path):
    """json, or jsonl (one object per line) - upstream `utils.load_file`."""
    if str(path).endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write(path, lines):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _psgs(item, n_docs=10):
    return [c["title"] + " // " + c["text"].strip().replace("\n", " ")
            for c in item["ctxs"][:n_docs]]


def build_dataset(dataset, input_file, out_dir, n_docs=10):
    """Write sources / retrieved_psgs / test_<dataset>.txt (+ choices)."""
    data = load_file(input_file)
    questions, passages, contents = [], [], []
    choices_all = []

    for item in data:
        question = item["question"]
        questions.append(question)
        if dataset == "popqa":
            # popqa passages carry no title, and the label IS the title match.
            psgs = [c["text"].strip().replace("\n", " ").replace("\t", " ")
                    for c in item["ctxs"][:n_docs]]
            labels = ["1" if c.get("title") == item.get("s_wiki_title") else "0"
                      for c in item["ctxs"][:n_docs]]
            contents += ["%s [SEP] %s\t%s" % (question, p, l)
                         for p, l in zip(psgs, labels)]
        else:
            psgs = _psgs(item, n_docs)
            contents += ["%s [SEP] %s" % (question, p) for p in psgs]
        passages.append(" [sep] ".join(psgs))
        if dataset == "arc_challenge":
            choices = ["%s: %s" % (l, t) for l, t in zip(
                item["choices"]["label"], item["choices"]["text"])]
            choices_all.append("; ".join(choices))

    _write(os.path.join(out_dir, dataset, "sources"), questions)
    _write(os.path.join(out_dir, dataset, "retrieved_psgs"), passages)
    _write(os.path.join(out_dir, dataset, "test_%s.txt" % dataset), contents)
    if dataset == "arc_challenge":
        _write(os.path.join(out_dir, dataset, "choices"), choices_all)


def build_form_dataset(input_file, out_dir, n_docs=10):
    """The form-field variant: labelled (question, passage) pairs, as-is."""
    data = load_file(input_file)
    questions, passages, contents = [], [], []
    for item in data:
        question = item["question"]
        questions.append(question)
        psgs = [str(c.get("text") or "").strip().replace("\n", " ")
                for c in (item.get("ctxs") or [])[:n_docs]]
        labels = [str(c.get("label") or "0") for c in
                  (item.get("ctxs") or [])[:n_docs]]
        passages.append(" [sep] ".join(psgs))
        contents += ["%s [SEP] %s\t%s" % (question, p, l)
                     for p, l in zip(psgs, labels)]
    _write(os.path.join(out_dir, "form", "sources"), questions)
    _write(os.path.join(out_dir, "form", "retrieved_psgs"), passages)
    _write(os.path.join(out_dir, "form", "test_form.txt"), contents)


def postprocess(dataset, out_dir, mode="distributed"):
    """Fold the prepared knowledge files back into the jsonl (Self-CRAG)."""
    def _read(name):
        with open(os.path.join(out_dir, dataset, "ref", name),
                  "r", encoding="utf-8") as f:
            return [line.strip() for line in f.readlines()]

    contexts = [c[1:] for c in _read("correct")]
    web_contexts = [c[1:] for c in _read("incorrect")]
    comb_contexts = [c for c in _read("ambiguous")]
    input_file = os.path.join(out_dir, dataset, "test_%s.txt" % dataset)
    if not os.path.exists(input_file):
        raise SystemExit("run the dataset build first: %s missing" % input_file)

    with open(input_file, "r", encoding="utf-8") as f:
        pre_data = [json.loads(line) for line in f if line.strip()]
    for i, (item, context, web_context, comb_context) in enumerate(
            zip(pre_data, contexts, web_contexts, comb_contexts)):
        title = (item.get("ctxs") or [{}])[0].get("title", "").strip()
        if mode == "distributed":
            item["ctxs"] = [{"id": i, "title": title, "text": context,
                             "score": 1}]
            for _web in web_context.split("; "):
                item["ctxs"].append({"id": i, "title": title,
                                     "text": web_context, "score": 0.5})
            item["ctxs"].append({"id": i, "title": title,
                                 "text": comb_context, "score": 0.75})
        else:
            item["ctxs"] = [
                {"id": i, "title": title, "text": context, "score": 1},
                {"id": i, "title": title, "text": web_context, "score": 0.5},
                {"id": i, "title": title, "text": comb_context, "score": 0.75},
            ]
    out_path = os.path.join(out_dir, dataset, "output",
                            "%s_selfcrag.json" % dataset)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pre_data, f)


def main():
    parser = argparse.ArgumentParser(
        description="Build CRAG sources / retrieved_psgs / test files.")
    parser.add_argument("--dataset",
                        choices=list(INPUT_FILES) + ["form"], required=True)
    parser.add_argument("--input_file", default="",
                        help="jsonl of eval data (default: upstream's path)")
    parser.add_argument("--out_dir", default="../data",
                        help="root the <dataset>/ directories are written to")
    parser.add_argument("--ndocs", type=int, default=10)
    parser.add_argument("--mode", default="distributed",
                        choices=["assembled", "distributed"])
    parser.add_argument("--postprocess", action="store_true")
    args = parser.parse_args()

    input_file = args.input_file
    if not input_file:
        if args.dataset == "form":
            raise SystemExit("--dataset form needs --input_file")
        input_file = INPUT_FILES[args.dataset]
    if args.dataset == "form":
        build_form_dataset(input_file, args.out_dir, args.ndocs)
    else:
        build_dataset(args.dataset, input_file, args.out_dir, args.ndocs)
    if args.postprocess:
        postprocess(args.dataset, args.out_dir, args.mode)


if __name__ == "__main__":
    main()

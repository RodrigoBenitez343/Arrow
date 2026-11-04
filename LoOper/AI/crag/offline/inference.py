"""CRAG inference: port of `utils/crag/scripts/CRAG_Inference.py`.

Upstream scores every retrieved passage with the fine-tuned T5 evaluator,
buckets each query with ``process_flag``, swaps in the matching prepared
knowledge file (correct / ambiguous / incorrect) and generates with vLLM.

The port keeps the data preparation, the evaluator call, the flag bucketing
(``AI.crag.evaluator.process_flag``) and the knowledge swap, and offers two
generators: a local ``transformers`` model (``--generator_path``) or the
app's own llama-server (``--api_url``, ``POST /llamacpp/generate``).  The
prompt shape and the control-token cleanup come from ``AI.crag.generate``,
so an offline run and a live form run read the same contract.
"""

import argparse
import json

import requests

from ..evaluator import process_flag
from ..generate import format_prompt_for_task, postprocess_answer

TASK_INST = {
    "wow": "Given a chat history separated by new lines, generates an "
           "informative, knowledgeable and engaging response. ",
    "pubqa": "Is the following statement correct or not? Say true if it's "
             "correct; otherwise say false.",
    "eli5": "Provide a paragraph-length response using simple words to "
            "answer the following question.",
    "obqa": "Given four answer candidates, A, B, C and D, choose the best "
            "answer choice.",
    "arc_easy": "Given four answer candidates, A, B, C and D, choose the best "
                "answer choice.",
    "arc_challenge": "Given four answer candidates, A, B, C and D, choose the "
                     "best answer choice.",
    "trex": "Given the input format 'Subject Entity [SEP] Relationship Type,' "
            "predict the target entity.",
    "asqa": "Answer the following question. The question may be ambiguous and "
            "have multiple correct answers, and in that case, you have to "
            "provide a long-form answer including all correct answers.",
}


def data_preprocess(file, n_docs):
    """``(queries, passages)`` - every query's ' [sep] '-joined passages."""
    queries, passages = [], []
    tmp_psgs = []
    with open(file, "r", encoding="utf-8") as f:
        for line in f.readlines():
            c = line.strip()
            if not c or " [SEP] " not in c:
                continue
            q, p = c.split(" [SEP] ", 1)
            if not queries:
                queries.append(q)
                tmp_psgs = [p]
            elif q != queries[-1]:
                passages.append(" [sep] ".join(tmp_psgs[:n_docs]))
                queries.append(q)
                tmp_psgs = [p]
            else:
                tmp_psgs.append(p)
    if queries:
        passages.append(" [sep] ".join(tmp_psgs[:n_docs]))
    return queries, passages


def get_evaluator_data(file):
    """The evaluator's own input lines (``content [\\t label]``)."""
    content = []
    with open(file, "r", encoding="utf-8") as f:
        for line in f.readlines():
            content.append(line.strip())
    return content, None


def evaluator_scores(tokenizer, model, file, device, n_docs=10):
    """Upstream ``inference()``: one logit per (query, passage) row."""
    import torch

    model.eval()
    content, _label = get_evaluator_data(file)
    scores = []
    for n, c in enumerate(content):
        if c.strip().endswith("[SEP]"):
            scores.append(-1.0)
            continue
        if n % n_docs > n_docs - 1:
            continue
        test = tokenizer(c, return_tensors="pt", padding="max_length",
                         max_length=512, truncation=True)
        with torch.no_grad():
            outputs = model(test["input_ids"].to(device),
                            attention_mask=test["attention_mask"].to(device))
        scores.append(float(outputs["logits"].cpu()))
    return scores


def _read_lines(path):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().lstrip("#") for line in f.readlines()]


def generate_local(model_path, prompts, max_tokens=100):
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_path)
    model = AutoModelForCausalLM.from_pretrained(model_path)
    out = []
    for prompt in prompts:
        inputs = tokenizer(prompt, return_tensors="pt")
        ids = model.generate(**inputs, max_new_tokens=max_tokens,
                             do_sample=False)
        out.append(tokenizer.decode(ids[0][inputs["input_ids"].shape[1]:],
                                    skip_special_tokens=True))
    return out


def generate_api(api_url, prompts, model="", max_tokens=100):
    """The app's own engine (llama-server through the local gateway)."""
    out = []
    for prompt in prompts:
        response = requests.post(
            api_url.rstrip("/") + "/llamacpp/generate",
            json={"model": model, "prompt": prompt, "system": "",
                  "temperature": 0.0, "max_tokens": max_tokens,
                  "stream": False, "use_llamacpp": True, "max_rounds": 1},
            timeout=600)
        payload = response.json() if response.status_code == 200 else {}
        out.append(payload.get("response") or payload.get("text") or "")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_file", required=True,
                        help="test_<dataset>.txt built by offline/dataset.py")
    parser.add_argument("--output_file", required=True)
    parser.add_argument("--task", default="")
    parser.add_argument("--method", default="crag",
                        choices=["rag", "crag", "no_retrieval"])
    parser.add_argument("--evaluator_path", default="")
    parser.add_argument("--generator_path", default="")
    parser.add_argument("--api_url", default="",
                        help="the app's llama-server, e.g. http://127.0.0.1:8000")
    parser.add_argument("--internal_knowledge_path", default="")
    parser.add_argument("--external_knowledge_path", default="")
    parser.add_argument("--combined_knowledge_path", default="")
    parser.add_argument("--ndocs", type=int, default=10)
    parser.add_argument("--upper_threshold", type=float, default=0.0)
    parser.add_argument("--lower_threshold", type=float, default=-1.0)
    parser.add_argument("--max_tokens", type=int, default=100)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    queries, passages = data_preprocess(args.input_file, args.ndocs)

    if args.method == "crag":
        import torch
        from transformers import T5ForSequenceClassification, T5Tokenizer

        tokenizer = T5Tokenizer.from_pretrained(args.evaluator_path)
        model = T5ForSequenceClassification.from_pretrained(
            args.evaluator_path, num_labels=1)
        device = torch.device(args.device)
        model.to(device)
        scores = evaluator_scores(tokenizer, model, args.input_file, device,
                                  n_docs=args.ndocs)
        flags = process_flag(scores, args.ndocs, args.upper_threshold,
                             args.lower_threshold)
        internal = (_read_lines(args.internal_knowledge_path)
                    if args.internal_knowledge_path else [])
        external = (_read_lines(args.external_knowledge_path)
                    if args.external_knowledge_path else [])
        combined = (_read_lines(args.combined_knowledge_path)
                    if args.combined_knowledge_path else [])
        passages = []
        for flag, i, e, c in zip(flags, internal, external, combined):
            passages.append(e if flag == 0 else c if flag == 1 else i)
    elif args.method == "no_retrieval":
        passages = []

    prompts = []
    for query, psg in zip(queries, passages or [None] * len(queries)):
        prompts.append(format_prompt_for_task(
            args.task, query, psg, TASK_INST.get(args.task)))

    if args.api_url:
        replies = generate_api(args.api_url, prompts, max_tokens=args.max_tokens)
    else:
        replies = generate_local(args.generator_path, prompts,
                                 args.max_tokens)
    preds = [postprocess_answer(r) for r in replies]

    with open(args.output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(preds))
    print(json.dumps({"queries": len(queries), "method": args.method,
                      "output": args.output_file}, indent=2))


if __name__ == "__main__":
    main()

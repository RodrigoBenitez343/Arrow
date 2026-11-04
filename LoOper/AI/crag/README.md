# CRAG — Corrective Retrieval Augmented Generation (port)

Full port of `D:\Arrow\utils\crag` into the app. The Form Filling node uses
it instead of the probe / ComoRAG consolidation loop it used to run per field.

Source: Yan, Gu, Zhu, Ling — *Corrective Retrieval Augmented Generation*,
arXiv:2401.15884.

## What the port keeps

The pipeline is the paper's and upstream's, stage for stage:

```
retrieve ──► evaluate ──► route ───────────────► decompose ──► recompose ──► generate
              (scores)     | correct               (strips)      (top-n)      (cleanup)
                           | ambiguous ─┐
                           | incorrect ─┤──► external knowledge ──► decompose/recompose
                                        ┘
```

| upstream | port | notes |
|---|---|---|
| `CRAG_Inference.inference()` | `evaluator.py` | one score per (query, passage) |
| `CRAG_Inference.process_flag()` | `evaluator.py:process_flag` | verbatim, including the `n_docs` grouping |
| `CRAG_Inference.main()` routing (`2/1/0`) | `pipeline.py:CRAGPipeline.run` | correct ⇒ internal, ambiguous ⇒ `Knowledge1 [sep] Knowledge2`, incorrect ⇒ external only |
| `internal_knowledge_preparation.extract_strips_from_psg` | `decompose.py` | all three modes: `selection`, `excerption`, `fixed_num` |
| `utils.select_relevants` | `decompose.py:select_relevants` | `<4`-word strips score `-1.0` for free, sort desc, `top_n`, `'; '` join |
| `utils.extract_keywords` | `rewrite.py` | the keyword rewrite that feeds the corrective step |
| `external_knowledge_preparation.visit_pages` | `external.py` | the *stage*; the stores are the app's own |
| `CRAG_Inference.format_prompt` | `generate.py:build_prompt` / `format_prompt_for_task` | reference-based prompt |
| `postprocess_answer_option_conditioned`, `utils.postprocess` | `generate.py:postprocess_answer` | control tokens, `</s>`, `<|endoftext|>` |
| `data_process.py` | `offline/dataset.py` | plus a `--dataset form` variant |
| `train_evaluator.py` | `offline/train_evaluator.py` | T5-large one-logit classifier |
| `CRAG_Inference.py` (offline run) | `offline/inference.py` | `rag` / `crag` / `no_retrieval` |
| `eval.py`, `metrics.py` | `offline/eval.py`, `offline/metrics.py` | `match`, `accuracy`, EM, F1 |
| — | `offline/calibrate.py` | sweeps the two thresholds against a labelled set |

## The two deliberate substitutions

1. **The evaluator is Laya, not a fine-tuned T5.**
   Upstream scores `query [SEP] passage` with a T5-large sequence classifier
   and thresholds the raw logit. That model's weights are an external
   download, and torch is not part of the app runtime. Laya (`AI/laya_client`,
   shipped in `AI/bin` + `AI/models/laya-GGUF`) answers one typed `noul`
   question per passage — `"This passage contains the answer to: <query>"` —
   and returns a **calibrated probability** in `[0, 1]`. Same shape (one number
   per pair, one forward, no generation), different scale, so the thresholds
   live in probability space: `crag_upper_threshold` 0.60 (correct) and
   `crag_lower_threshold` 0.40 (ambiguous). The T5 path still exists for
   dataset-level reproduction in `offline/`.

2. **The external store is the app's context pool, not the open web.**
   Upstream rewrites the query, searches Google (Serper), scrapes the result
   pages and rescoring the paragraphs. This app has no web search, so the
   corrective stage reads what the user already wired: the pool's learned /
   verified answers and turn history (`PoolStreams`), and a second retrieval
   over the attached documents with the rewritten query (`ChunkStore`). If
   nothing there clears the lower threshold the field is handed to the user —
   which is the point of the corrective action, not a fallback.

## Budget

One Laya forward is ~2 s on CPU, so two caps bound the work per field:
`crag_top_k` passages scored and `crag_max_strips` strips scored (short strips
cost nothing). The scoring runs as one contiguous burst and `release()` is
called before the extraction completion — Laya shares one memory pool with
llama-server.

## Where the app wires it

`LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/form_filler_modules/crag.py`

* corpus: attached documents + the pool body, chunked 250/50, with the first
  chunk of every document force-included (the header block holds name /
  email / phone and embeds poorly against short labels);
* query: the field's question (`_ff_question_head`), plus the required format
  on a repair run;
* pack: `evidence_block()` — verbatim source strips, labelled with the
  verdict, capped by `crag_char_budget`.

Node settings: `crag_enabled`, `crag_evaluator`, `crag_upper_threshold`,
`crag_lower_threshold`, `crag_decompose_mode`, `crag_top_k`,
`crag_max_strips`, `crag_top_n_internal`, `crag_top_n_external`,
`crag_external`, `crag_rewrite`, `crag_char_budget`.

## Offline use

```
python -m AI.crag.offline.dataset --dataset popqa        # sources/psgs/test
python -m AI.crag.offline.train_evaluator --train_file ... --save_path ...
python -m AI.crag.offline.inference --input_file ... --method crag \
    --evaluator_path ... --internal_knowledge_path ... \
    --external_knowledge_path ... --combined_knowledge_path ...
python -m AI.crag.offline.eval --input_file ... --eval_file ... --metric match
python -m AI.crag.offline.calibrate --input_file labelled.jsonl --out_dir out
```

`offline/` needs torch + transformers; nothing under `AI/crag/*.py` does.

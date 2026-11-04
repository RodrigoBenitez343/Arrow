"""CRAG generation contract: the prompt shape, the cleanup, the evidence pack.

Port of `CRAG_Inference.py:format_prompt` / `:postprocess_answer_option_
conditioned` and `utils.py:postprocess`.  Upstream generates with vLLM and a
Self-RAG control-token vocabulary; here the generation itself is the node's
own extractor (it must return ONE value for ONE field), and what is ported is
everything around it:

* the "Refer to the following documents ... Instruction: <task>" framing that
  makes the model answer FROM the knowledge block;
* the control-token scrub (``[Retrieval]``, ``<paragraph>``,
  ``[Utility:1..5]``, ``[Fully supported]`` ...) so a self-RAG fine-tune's
  markers never reach a form field;
* the knowledge block itself, labelled with the verdict that produced it so
  the extractor knows whether it is looking at evaluated-correct material.
"""

import re
from typing import Optional

# Upstream's control-token vocabulary (`CRAG_Inference.control_tokens`).
CONTROL_TOKENS = (
    "[Fully supported]", "[Partially supported]", "[No support / Contradictory]",
    "[No Retrieval]", "[Retrieval]", "[Irrelevant]", "[Relevant]",
    "<paragraph>", "</paragraph>", "[Utility:1]", "[Utility:2]", "[Utility:3]",
    "[Utility:4]", "[Utility:5]",
)

_TOKEN_RE = re.compile("|".join(re.escape(t) for t in CONTROL_TOKENS))
_END_MARKERS = ("</s>", "<|endoftext|>")

# Upstream's document framing, one sentence per line 60 of CRAG_Inference.py.
_PROMPT_HEAD = ("Refer to the following documents, follow the instruction "
                "and answer the question.")
_TASK_HEAD = "Instruction: Answer the question: %s"


def build_prompt(query: str, knowledge: str) -> str:
    """Upstream's reference-based prompt for one query + knowledge block."""
    return "%s\n\nDocuments: %s\n\n%s" % (
        _PROMPT_HEAD, str(knowledge or ""), _TASK_HEAD % str(query or ""))


def format_prompt_for_task(task: str, question: str, paragraph: str = None,
                           instruction: str = None) -> str:
    """`format_prompt` for the offline scripts, with a task instruction.

    Upstream branches per dataset AND per generator (llama vs selfrag
    templates).  The readable llama-style branch is kept for every task: it
    states the instruction, then the documents, then the input - which is
    what the app's own engines follow best.
    """
    psg = "" if paragraph is None else str(paragraph)
    if instruction:
        if task == "pubqa":
            return ("Read the documents and answer the question: Is the "
                    "following statement correct or not? \n\nDocuments: %s"
                    "\n\nStatement: %s\n\nOnly say true if the statement is "
                    "true; otherwise say false." % (psg, question))
        return ("%s\n\n## Input:\n\n%s\n\nDocuments: %s"
                % (instruction, question, psg))
    return build_prompt(question, psg)


def postprocess_answer(text: str, keep_newlines: bool = False) -> str:
    """Scrub control tokens / end markers (upstream ``postprocess``)."""
    out = str(text or "")
    out = _TOKEN_RE.sub("", out)
    for marker in _END_MARKERS:
        out = out.replace(marker, "")
    if not keep_newlines:
        out = out.replace("\r\n", " ").replace("\n", " ")
    out = out.strip()
    if out.startswith(" "):
        out = out[1:]
    return out


def evidence_block(action: str, material: str, top_score: float = 0.0,
                   extra_heading: Optional[str] = None) -> str:
    """The knowledge block the extractor reads.

    Labelled VERBATIM: CRAG hands back source sentences, not a paraphrase, so
    the exact-value rule ("copy a URL / email / phone character-for-character")
    is enforceable.  ``action`` names the verdict that produced the block, and
    the block is empty when the verdict produced no knowledge.
    """
    body = str(material or "").strip()
    if not body:
        return ""
    head = extra_heading or (
        "[Retrieved knowledge - CRAG verdict: %s (top score %.2f). These are "
        "VERBATIM source passages: copy every exact value character-for-"
        "character.]" % (str(action or "unknown"), float(top_score or 0.0))
    )
    return "%s\n%s" % (head, body)


__all__ = [
    "CONTROL_TOKENS",
    "build_prompt",
    "evidence_block",
    "format_prompt_for_task",
    "postprocess_answer",
]

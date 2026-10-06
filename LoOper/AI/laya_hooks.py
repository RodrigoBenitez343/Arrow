"""laya_hooks.py — Laya-first verdicts for the ComoRAG and form-filler pipelines.

The embedded Laya engine (`AI/laya_client.py`) answers by default the two
verdicts these pipelines used to ask a small LLM for:

* **fact creation** — ``relevance_verdict``: does each composed finding answer
  the probe it came from?  (the engine's per-cycle curation verdict)
* **probing assessment / consumption** — ``sufficiency_verdict``: is the goal
  answerable from the accumulated facts + verbatim evidence yet?  Emits the
  engine's own verdict line: ``ANSWER: <candidate>`` (stop the sweep) or
  ``MISSING:`` (keep probing).

Both are typed ``noul`` questions — a calibrated P(the statement holds) from
one encoder pass, no model prose is parsed (the fragile part of the LLM
path).  Every helper returns ``None`` when the engine is unavailable (not
shipped, still loading, ``LOOPER_LAYA=off``), and the ``*_or`` wrappers then
call the caller's LLM evaluator, so ``LOOPER_LAYA=off`` restores the
LLM-only behavior everywhere.
"""

import logging
import re
from typing import Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Calibrated probability threshold for a noul statement holding.
_NOUL_THRESHOLD = 0.5

# Hypothesis templates (statements about the premise); tuned for Laya's
# typed-question encoder.
_RELEVANCE_STATEMENT = "This text is relevant to: {topic}"
_SUFFICIENCY_STATEMENT = "This text fully answers: {task}"
# The STATE is the composed claim; the instruction names the premise.
_GROUNDING_STATEMENT = "This text is supported by the source excerpts: {evidence}"
# The STATE is the value ALREADY on a form field; the instruction names the
# field's own question AND the trusted source.  The source (the node's own
# document, which the user can swap/edit) is the ROOT TRUTH the verdict is
# judged against: a pre-filled field counts as correct only when that source
# supports it.
_ACCURACY_STATEMENT = (
    "This text is the correct answer to the form field: {field} "
    "according to: {evidence}"
)
# The STATE is the model's approximate answer; the instruction names the form
# question and the option under test.  Each option is scored independently so
# the pick carries a CONFIDENCE (argmax + margin), not just a single choice.
_CHOICE_STATEMENT = (
    "This text answers the question '{question}' with the option: {option}"
)
# The winning option must clear this margin over the runner-up, or the pick is
# refused and the caller asks the user instead of guessing.
_CHOICE_MARGIN = 0.15
# One noul forward pass per option: cap the list so a 250-entry country select
# cannot cost minutes.  Beyond the cap the caller falls back to its own rail.
_MAX_CHOICE_OPTIONS = 12


def _noul(state: str, instructions: str) -> Optional[float]:
    from AI.laya_client import noul
    return noul(state, instructions)


def relevance_verdict(probe: str,
                      findings: Sequence[str]) -> Optional[List[bool]]:
    """Per-finding fact-curation verdict: does the finding answer the probe?

    Mirrors the engine's ``relevance_judge`` contract: a list of bools aligned
    with *findings*, or ``None`` when the engine is unavailable (the caller
    then keeps its LLM evaluator / the lexical result — fail open).
    """
    claims = [str(f) for f in (findings or [])]
    if not claims:
        return None
    statement = _RELEVANCE_STATEMENT.format(topic=str(probe or "")[:400])
    verdicts: List[bool] = []
    for claim in claims:
        p = _noul(claim, statement)
        if p is None:
            return None
        verdicts.append(bool(p > _NOUL_THRESHOLD))
    logger.info(
        "Laya relevance verdict: %d/%d finding(s) on topic",
        sum(verdicts), len(verdicts),
    )
    return verdicts


def _candidate_from(facts_text: str, evidence_text: str) -> str:
    """A quotable candidate for an ANSWER verdict.

    The composed facts are the pipeline's own grounded claims — take the
    longest finding line (the LAST line of each fact block; the first line is
    the probe header).  Falls back to the longest evidence line.  Capped at
    the engine's 220-char payload limit.
    """
    best = ""
    for block in re.split(r"\n-{3,}\n", str(facts_text or "")):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if lines and len(lines[-1]) > len(best):
            best = lines[-1]
    if not best:
        lines = [ln.strip() for ln in str(evidence_text or "").splitlines()
                 if ln.strip()]
        best = max(lines, key=len) if lines else ""
    return best[:200]


def sufficiency_verdict(task: str, facts_text: str,
                        evidence_text: str) -> Optional[str]:
    """Probing assessment: is the goal answerable from the material yet?

    Returns the engine's verdict line — ``ANSWER: <candidate>`` when the
    material answers the task, ``MISSING:`` (no payload: a binary verdict
    cannot name the missing detail) when it does not — or ``None`` when the
    engine is unavailable / there is nothing to assess (the caller keeps
    probing or uses its LLM judge).
    """
    task_s = str(task or "").strip()[:400]
    material = ("%s\n\n%s" % (str(facts_text or "").strip(),
                              str(evidence_text or "").strip())).strip()
    if not task_s or not material:
        return None
    p = _noul(material, _SUFFICIENCY_STATEMENT.format(task=task_s))
    if p is None:
        return None
    if p > _NOUL_THRESHOLD:
        candidate = _candidate_from(facts_text, evidence_text)
        if candidate:
            logger.info(
                "Laya probing assessment: goal answerable — stopping the "
                "sweep (candidate: %s)", candidate[:120],
            )
            return "ANSWER: " + candidate
        return None  # answerable but nothing quotable: keep probing (safe)
    logger.info("Laya probing assessment: not answerable yet — keep probing")
    return "MISSING:"


def grounding_verdict(claim: str, evidence: str) -> Optional[bool]:
    """Is *claim* SUPPORTED by *evidence* (inferable), not merely verbatim?

    Token matching answers "does this word occur in the text?", which rejects a
    perfectly correct INFERENCE - a count computed from dates, a faithful
    paraphrase - and so discarded good facts as "not in the source ground
    truth".  A calibrated entailment verdict answers what the pipeline actually
    means: "does the source support this claim?".  ``None`` when the engine is
    unavailable or there is nothing to judge (the caller keeps its own rule).
    """
    claim_s = str(claim or "").strip()
    ev_s = str(evidence or "").strip()
    if not claim_s or not ev_s:
        return None
    p = _noul(claim_s, _GROUNDING_STATEMENT.format(evidence=ev_s[:1200]))
    if p is None:
        return None
    logger.info("Laya grounding verdict: p=%.3f", p)
    return bool(p > _NOUL_THRESHOLD)


def grounding_or(claim: str, evidence: str) -> Optional[bool]:
    """Laya grounding verdict, else ``None`` (caller keeps its own rule)."""
    try:
        return grounding_verdict(claim, evidence)
    except Exception as exc:  # noqa: BLE001 - never break a pipeline
        logger.warning("Laya grounding verdict failed: %s", exc)
        return None


def value_accuracy_verdict(field: str, value: str,
                           evidence: str) -> Optional[bool]:
    """Does *value* (already on a form field) match the TRUSTED SOURCE?

    A pre-filled field is not automatically correct: the page can hold a stale
    answer, and a read can resolve a SIBLING control (bringing back a value that
    belongs to another question).  ``evidence`` is the node's OWN source - the
    document set on the node dialog, which the user can swap/edit - i.e. the
    root truth.  ``True`` -> the source supports the value (skip the field),
    ``False`` -> wrong / unrelated (refill it), ``None`` when the engine OR the
    source is unavailable (the caller then keeps its existing skip rule).
    """
    v = str(value or "").strip()
    f = str(field or "").strip()
    ev = str(evidence or "").strip()
    if not v or not f or not ev:
        return None
    p = _noul(v[:220], _ACCURACY_STATEMENT.format(field=f[:200], evidence=ev[:1200]))
    if p is None:
        return None
    logger.info("Laya accuracy verdict for %r: p=%.3f", f[:50], p)
    return bool(p > _NOUL_THRESHOLD)


def value_accuracy_or(field: str, value: str, evidence: str,
                      fallback: Optional[Callable[[], object]] = None):
    """Laya accuracy verdict, else *fallback()*, else None.  Never raises."""
    try:
        verdict = value_accuracy_verdict(field, value, evidence)
    except Exception as exc:  # noqa: BLE001 - never break a pipeline
        logger.warning("Laya accuracy verdict failed: %s", exc)
        verdict = None
    if verdict is not None:
        return verdict
    return fallback() if callable(fallback) else None


def choose_option(question: str, answer: str,
                  options: Sequence[str]) -> Tuple[Optional[int], bool]:
    """Option pick for *answer*, plus whether the engine could ANSWER at all.

    Returns ``(picked_index, engine_answered)``:

    * ``(i, True)``  - the engine judged option *i* the clear winner;
    * ``(None, True)`` - the engine ANSWERED but no option won clearly (a
      REFUSAL).  The caller must ASK the user rather than fall through to a
      blind pick - a re-ask guessed the MOST SENIOR option for a field the
      sources did not support (observed: a cloud-experience question the
      evidence answered "no" was filled with "implementé y desplegué
      soluciones completas en la nube");
    * ``(None, False)`` - the engine could not be consulted (down / kill
      switch), so the caller keeps its own non-semantic rail.

    The model's approximate answer is the STATE; each option is scored with a
    noul question ("does this text answer <question> with <option>?") and the
    ARGMAX wins ONLY when it clears ``_NOUL_THRESHOLD`` AND beats the runner-up
    by ``_CHOICE_MARGIN``.  Never raises.
    """
    try:
        opts = [str(o) for o in (options or []) if str(o).strip()]
        ans = str(answer or "").strip()
        if not ans or not opts:
            return None, False
        q = str(question or "")[:200]
        scores: List[Optional[float]] = [
            _noul(ans, _CHOICE_STATEMENT.format(question=q, option=o))
            for o in opts[:_MAX_CHOICE_OPTIONS]
        ]
        if any(s is None for s in scores):
            return None, False
        best = max(range(len(scores)), key=lambda i: scores[i])
        runner = max(
            (s for i, s in enumerate(scores) if i != best),
            default=0.0,
        )
        if scores[best] <= _NOUL_THRESHOLD or scores[best] - runner < _CHOICE_MARGIN:
            logger.info(
                "Laya choice verdict: no clear pick (best %.2f vs %.2f) — "
                "leaving it to the caller", scores[best], runner,
            )
            return None, True
        logger.info(
            "Laya choice verdict: picked %r (p=%.2f, runner-up %.2f)",
            opts[best][:60], scores[best], runner,
        )
        return best, True
    except Exception as exc:  # noqa: BLE001 - never break a pipeline
        logger.warning("Laya choice verdict failed: %s", exc)
        return None, False


def choose_option_by_source(question: str, source: str,
                            options: Sequence[str]) -> Tuple[Optional[int], bool]:
    """The option the TRUSTED SOURCE best supports (argmax, no margin).

    The model's ANSWER can be a lossy paraphrase of the source ("Buenos Aires"
    for "Buenos Aires, Buenos Aires Province, Argentina"), so scoring THAT
    cannot separate two close options.  Scoring the SOURCE text instead asks
    "which option does the source itself describe?" and returns the HIGHEST
    scoring option above the threshold - the most accurate one - which is how a
    human picks the city over the province when the source spells out both.

    Returns ``(index, engine_answered)``: ``(i, True)`` a pick; ``(None, True)``
    nothing cleared the threshold; ``(None, False)`` the engine was unavailable.
    Never raises.
    """
    try:
        opts = [str(o) for o in (options or []) if str(o).strip()]
        src = str(source or "").strip()
        if not src or not opts:
            return None, False
        q = str(question or "")[:200]
        scores: List[Optional[float]] = [
            _noul(src[:220], _CHOICE_STATEMENT.format(question=q, option=o))
            for o in opts[:_MAX_CHOICE_OPTIONS]
        ]
        if any(s is None for s in scores):
            return None, False
        best = max(range(len(scores)), key=lambda i: scores[i])
        if scores[best] <= _NOUL_THRESHOLD:
            logger.info(
                "Laya source pick: no option cleared the threshold (best %.2f, %r)",
                scores[best], opts[best][:60],
            )
            return None, True
        logger.info(
            "Laya source pick: %r (p=%.2f) for %r",
            opts[best][:60], scores[best], q[:40],
        )
        return best, True
    except Exception as exc:  # noqa: BLE001 - never break a pipeline
        logger.warning("Laya source pick failed: %s", exc)
        return None, False


def relevance_or(probe: str, findings: Sequence[str],
                 fallback: Optional[Callable[[], object]] = None):
    """Laya relevance verdict, else *fallback()* (the LLM evaluator), else None."""
    try:
        verdict = relevance_verdict(probe, findings)
    except Exception as exc:  # noqa: BLE001 - never break a pipeline
        logger.warning("Laya relevance verdict failed: %s", exc)
        verdict = None
    if verdict is not None:
        return verdict
    return fallback() if callable(fallback) else None


def sufficiency_or(task: str, facts_text: str, evidence_text: str,
                   fallback: Optional[Callable[[], object]] = None):
    """Laya sufficiency verdict, else *fallback()* (the LLM judge), else None."""
    try:
        verdict = sufficiency_verdict(task, facts_text, evidence_text)
    except Exception as exc:  # noqa: BLE001 - never break a pipeline
        logger.warning("Laya sufficiency verdict failed: %s", exc)
        verdict = None
    if verdict is not None:
        return verdict
    return fallback() if callable(fallback) else None

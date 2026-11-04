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
import time
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


# The STATE is one candidate line of a document; the instruction names the
# QUESTION.  This is the general "does this line answer that question" score,
# and it is what lets a caller pick WHICH part of a document a field is
# answered from without holding a vocabulary of field names: the judgement is
# semantic, so it belongs to the engine.
ANSWER_THRESHOLD = _NOUL_THRESHOLD


def answer_scores(question: str, texts: Sequence[str]) -> List[float]:
    """P(each text fully answers *question*), aligned with *texts*.

    Used to ORDER candidate lines (best answer first) and to decide whether a
    section answers the question at all.  Retries then raises
    :class:`LayaUnavailable`: a missing engine is a fault, never a reason to
    decide a meaning from a word list.
    """
    rows = [str(t or "") for t in (texts or [])]
    if not rows:
        return []
    statement = _SUFFICIENCY_STATEMENT.format(task=str(question or "")[:400])
    scores: List[float] = []
    for text in rows:
        scores.append(_noul_required(text[:220], statement, "answer score"))
    logger.info("Laya answer scores: %d line(s) for %r",
                len(scores), str(question or "")[:50])
    return scores


# A composed fact entry is "[cycle N] probe: <question>" followed by the
# composer's finding ("Key Finding: <statement>", "Support: <quote>").
_FACT_FINDING_RE = re.compile(
    r"^\s*(?:\*\*)?(?:key\s+)?finding\s*:\s*(?:\*\*)?\s*(.+)$", re.IGNORECASE
)


def _candidate_from(facts_text: str, evidence_text: str) -> str:
    """A quotable candidate for an ANSWER verdict.

    The composed facts are the pipeline's own grounded claims, so the
    candidate is a FINDING line - never the longest line of the pool: that
    picked an ``evidence:``-tagged raw chunk and the verdict shipped
    "evidence: 1154980148) · benitezrodrigo169@gmail.com ·
    linkedin.com/in/rodrigo-..." as the answer (a truncated URL that then
    fails the engine's grounding check, so the cut is refused as well).
    Evidence is only a fallback when no composed finding exists.  Capped at
    the engine's 220-char payload limit.
    """
    best = ""
    for block in re.split(r"\n-{3,}\n", str(facts_text or "")):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines or lines[0].lower().startswith("evidence:"):
            # A raw chunk: its tail is wrapped source text, not a value.
            continue
        finding = ""
        for line in lines:
            m = _FACT_FINDING_RE.match(line)
            if m:
                finding = m.group(1).strip()
        if not finding:
            tail = [ln for ln in lines
                    if not ln.lower().lstrip("*#>- \t").startswith(
                        ("probe", "[cycle", "support"))]
            finding = tail[-1] if tail else ""
        if len(finding) > len(best):
            best = finding
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


# A request is either an instruction to change code or a question about it.
# Judged by the engine, not by keyword lists: "can you build me an app?" is an
# instruction, "how does the parser work?" is a question.  The statement is
# kept SHORT: measured 12/12 on build-vs-question samples, while longer
# phrasings of the same idea scored 9/12.
_CODING_INTENT_STATEMENT = 'This text requests code to be built or changed.'


def coding_intent(text: str) -> Optional[bool]:
    """True when *text* is a coding request, False when it is only a question.

    ``None`` when the engine is unavailable, so the caller keeps its own
    heuristic.  One encoder pass, no model prose parsed.
    """
    body = str(text or '').strip()
    if not body:
        return None
    p = _noul(body[:900], _CODING_INTENT_STATEMENT)
    if p is None:
        return None
    logger.info('Laya coding-intent verdict: p=%.3f (%s)', p, body[:60])
    return bool(p > _NOUL_THRESHOLD)


def coding_intent_or(text: str, fallback: Callable[[], Optional[bool]]
                     ) -> Optional[bool]:
    """Laya intent verdict, else *fallback()*.  Never raises."""
    try:
        verdict = coding_intent(text)
    except Exception as exc:  # noqa: BLE001 - never break the caller
        logger.warning('Laya coding-intent verdict failed: %s', exc)
        verdict = None
    if verdict is not None:
        return verdict
    try:
        return fallback()
    except Exception:
        return None


# How much work a single generated file asks for.  The coding pipeline uses
# this BEFORE spending a long generation: a file graded 'complex' is written in
# parts instead of being asked for whole twice and failing both times.
_COMPLEXITY_CRITERIA = {
    'simple': 'one small function or class, about 50 lines or fewer',
    'medium': 'a module with several functions or classes, 50 to 120 lines',
    'complex': 'a large module that handles a GUI window, threads, or long '
               'file and data processing, usually more than 120 lines',
}
_COMPLEXITY_LIMIT = 220     # the engine's per-question payload cap


def task_complexity(scope: str) -> Optional[str]:
    """Grade a file task as 'simple' / 'medium' / 'complex', or None.

    One encoder pass over the file's one-line purpose.  ``None`` when the
    engine is unavailable (the caller keeps its retry-then-split default).
    """
    body = str(scope or '').strip()
    if not body:
        return None
    try:
        from AI.laya_client import choice
        picked = choice(body[:_COMPLEXITY_LIMIT], 'This coding task is:',
                        _COMPLEXITY_CRITERIA)
    except Exception as exc:  # noqa: BLE001 - never break the pipeline
        logger.warning('Laya complexity verdict failed: %s', exc)
        return None
    if picked in _COMPLEXITY_CRITERIA:
        logger.info('Laya complexity verdict: %s (%s)', picked, body[:60])
        return picked
    return None


# The STATE is a field's own question; the instruction names the stored one.
# Used to keep ONE record per question: a reworded copy is linked to the
# record it duplicates instead of becoming a second answer.
_SAME_QUESTION_STATEMENT = (
    "This text asks for the same information as the question: {other}"
)
# Deliberately HIGH.  Merging "experience with aws" into "experience with
# azure" is unrecoverable (one answer fills two different questions); keeping
# two records for one question is merely untidy.
#
# MEASURED on the bundled English encoder (noul, statement "This text asks for
# the same information as the question: <other>"):
#   two wordings of one question          0.84 - 0.93   -> SAME
#   different subject (aws / azure)       0.46
#   different attribute (years / level)   0.45
#   different attribute (python / java)   0.08
#   different question (salary / city)    0.001
#   SAME but cross-language (es/pt/en)    0.62 - 0.93  <- some below the gate
#   DIFFERENT and cross-language          0.00 - 0.21  ...but one exception:
#   "your full name?" vs "your phone number?"                 0.73  <- no gate
# So >= GATE is same, <= REJECT is different, and the band between them is NOT
# decided by the engine.  For a cross-language duplicate the band is where a
# translation lands, so the caller may lift it for ONE pair at a time via
# ``agree`` - and only when the stored record already holds the SAME ANSWER
# (a wrong link then costs nothing: both labels are served that one value,
# where a wrong link across two answers fills a field with the other's value).
# Without ``agree`` the caller's fallback (the node's model, which translates)
# decides, or both records are kept.
_SAME_QUESTION_GATE = 0.75
_SAME_QUESTION_REJECT = 0.50
# One encoder pass per candidate; a decision needs a bounded candidate set.
_MAX_SAME_QUESTION_CANDIDATES = 4


def same_question_scores(label: str,
                         candidates: Sequence[str]):
    """``[(candidate, p)]``, or ``None`` when the engine is unavailable.

    One encoder pass per candidate so a caller can apply its own policy (see
    :func:`same_question_verdict`); ``[]`` when there is nothing to compare.
    """
    body = str(label or "").strip()[:220]
    cands = [str(c).strip() for c in (candidates or []) if str(c).strip()]
    cands = cands[:_MAX_SAME_QUESTION_CANDIDATES]
    if not body or not cands:
        return []
    out = []
    for cand in cands:
        p = _noul(body, _SAME_QUESTION_STATEMENT.format(other=cand[:220]))
        if p is None:
            return None
        out.append((cand, p))
    return out


def same_question_verdict(label: str, candidates: Sequence[str],
                          agree: Optional[Callable[[str], bool]] = None
                          ) -> Optional[str]:
    """Which candidate asks the SAME question as *label*.

    Returns the matching candidate text, ``""`` when the engine answered "none
    of them", or ``None`` when the engine did NOT decide (down / kill switch,
    or a probability inside the ambiguous band - see the gate comment).
    The caller MUST keep those apart: an engine that answered "none" is a
    decision, an undecided engine is not.

    ``agree(candidate)`` lifts the band for that ONE candidate: it must return
    True only when the stored record already holds the same ANSWER as the new
    one (that is what makes an unsure link free instead of harmful).
    """
    scores = same_question_scores(label, candidates)
    if not scores:
        return None
    best, best_p = max(scores, key=lambda row: row[1])
    if best_p >= _SAME_QUESTION_GATE:
        logger.info("Laya same-question verdict: %r == %r (p=%.2f)",
                    str(label or "")[:60], best[:60], best_p)
        return best
    if best_p > _SAME_QUESTION_REJECT:
        try:
            agreed = bool(agree(best)) if callable(agree) else False
        except Exception:
            agreed = False
        if agreed:
            logger.info(
                "Laya same-question verdict: %r ~ %r (p=%.2f, undecided) but "
                "the record holds the SAME answer - linking (one record)",
                str(label or "")[:60], best[:60], best_p)
            return best
        logger.info(
            "Laya same-question verdict: undecided (best %.2f, %r) - "
            "leaving it to the caller", best_p, best[:60])
        return None
    logger.info("Laya same-question verdict: no match (best %.2f, %r)",
                best_p, best[:60])
    return ""


def same_question_or(label: str, candidates: Sequence[str],
                     fallback: Optional[Callable[[], object]] = None,
                     agree: Optional[Callable[[str], bool]] = None):
    """Laya same-question verdict, else *fallback()*, else None.  Never raises."""
    try:
        verdict = same_question_verdict(label, candidates, agree)
    except Exception as exc:  # noqa: BLE001 - never break a pipeline
        logger.warning("Laya same-question verdict failed: %s", exc)
        verdict = None
    if verdict is not None:
        return verdict
    try:
        return fallback() if callable(fallback) else None
    except Exception:
        return None


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


# The NEGATIVE side of a question, asked of the OPTION itself.  Only used
# when the material says nothing about the question, because the rule is
# "no evidence -> the negative answer".  MEASURED on the bundled encoder:
#   No 0.94 | Yes 0.71 | Sí 0.27 | Si 0.35 | Acepto 0.25 | N/A 0.23
# so the negative is picked by MEANING - "yes is positive, no is negative" is
# kept as intent, never as a word list (a synonym list is a language).
_NEGATIVE_OPTION_STATEMENT = "This text says it is not the case."


def _noul_required(state: str, instruction: str, what: str) -> float:
    """A noul score that the run cannot do without: retry, then raise.

    The daemon is burst-loaded (load -> use -> unload), so a ``None`` usually
    means "reload me": each attempt releases it so the next call relaunches it.
    After ``_ABSENCE_ATTEMPTS`` the caller gets :class:`LayaUnavailable` - a
    missing model is a FAULT, never a reason to decide a meaning another way.
    """
    for attempt in range(_ABSENCE_ATTEMPTS):
        p = _noul(state, instruction)
        if p is not None:
            return p
        try:
            from AI import laya_client
            laya_client.release(wait=0.5)
        except Exception:  # noqa: BLE001 - the retry still has to happen
            pass
        if attempt + 1 < _ABSENCE_ATTEMPTS:
            time.sleep(min(1.0 * (attempt + 1), 3.0))
    raise LayaUnavailable(
        "Laya did not answer the %s after %d attempts (%r). The engine ships "
        "with the app: check AI/bin/laya.exe, the GGUF under AI/models/laya-GGUF, "
        "LOOPER_LAYA / LOOPER_LAYA_EXE, and that LOOPER_LAYA is not set to 0/off."
        % (what, _ABSENCE_ATTEMPTS, str(state)[:80])
    )


def decide_choice(question: str, evidence: str,
                  options: Sequence[str],
                  material_states=None) -> Tuple[Optional[int], str]:
    """Which option THIS material supports - the decision, not a model echo.

    A choice field is the one place where the extractor's own sentence must
    never decide: asked with twelve resume chunks that never mention the
    company, the model answered "Sí" to "¿Ha trabajado anteriormente en
    PedidosYa?", the answer happened to BE an option, and the rail took it.

    ``material_states`` is the ONE mechanical fact that decides whether an
    affirmative answer is even possible: did the composer state anything about
    this question?  (``True``/``False``; ``None`` = the caller does not know.)
    Measured on the real 3110-char pack, the encoder alone is NOT enough -
    scoring a long, on-topic-looking resume against the option pair lifts both
    options above the threshold (Sí scored as "supported"), which is exactly
    how a wrong affirmative survives.  So:

      1. the material STATES something (a composed, non-absence finding) ->
         score the options against it and take the one it states (``"source"``);
      2. the material states NOTHING -> no affirmative is possible; take the
         option that reads as the NEGATIVE (``"negative"``) - "no evidence ->
         the negative answer", identified by meaning, never by a word list;
      3. neither -> ``(None, "nothing")`` and the field is left to the user.
    ``(None, "toolarge")`` for more options than one pass can score.
    """
    opts = [str(o).strip() for o in (options or []) if str(o).strip()]
    if not opts:
        return None, "no options"
    if len(opts) > _MAX_CHOICE_OPTIONS:
        return None, "toolarge"
    q = str(question or "")[:200]
    src = str(evidence or "").strip()
    if src and material_states is not False:
        support = [
            _noul_required(src[:1200],
                           _CHOICE_STATEMENT.format(question=q, option=o),
                           "option verdict")
            for o in opts
        ]
        best = max(range(len(support)), key=lambda i: support[i])
        runner = max((s for i, s in enumerate(support) if i != best), default=0.0)
        if support[best] > _NOUL_THRESHOLD:
            logger.info(
                "Laya material decides %r (p=%.2f, runner-up %.2f) for %r",
                opts[best][:60], support[best], runner, q[:40],
            )
            return best, "source"
    negative = [
        _noul_required(o, _NEGATIVE_OPTION_STATEMENT, "negative-option verdict")
        for o in opts
    ]
    best = max(range(len(negative)), key=lambda i: negative[i])
    if negative[best] > _NOUL_THRESHOLD:
        logger.info(
            "Laya: no material for %r -> the negative option %r (p=%.2f)",
            q[:40], opts[best][:60], negative[best],
        )
        return best, "negative"
    logger.info("Laya: no option is supported or negative for %r", q[:40])
    return None, "nothing"


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


# The STATE is a composed finding; the instruction names the verdict.  An
# ABSENCE is a meaning, not a vocabulary: "do not contain", "isn't listed",
# "we couldn't find it" all say the same thing, and a word list only ever
# knows the phrasings someone remembered to add (live: "do not contain" walked
# past a list that held does not / did not / is not, entered the fact pool, and
# cut a sweep at cycle 1 on a finding that said there was nothing).
#
# MEASURED on the bundled English encoder, this wording beat five others:
#   absence findings        0.54 - 0.90   -> ABSENT
#   real values            0.04 - 0.13   -> an answer
#   "no mention of a website"            0.29   <- the one phrasing it misses;
# the statement is kept SHORT for exactly that reason ("reports that the
# requested information is missing ... in the material" read the same cases
# 0.25 - 0.29 and diluted the real answers to 0.19).
_ABSENCE_STATEMENT = "This text states that something is missing."


def absence_verdict(text: str) -> Optional[bool]:
    """Does *text* only report that the information is ABSENT?

    One calibrated pass, no prose parsed.  ``None`` when the engine did not
    answer - a PRIMITIVE for diagnostics and tuning; production code calls
    :func:`absence_required`, which retries and then fails loudly instead of
    leaving the decision to a caller's word list.
    """
    body = str(text or "").strip()
    if not body:
        return None
    p = _noul(body[:600], _ABSENCE_STATEMENT)
    if p is None:
        return None
    logger.info("Laya absence verdict: p=%.3f (%s)", p, body[:60])
    return bool(p > _NOUL_THRESHOLD)


class LayaUnavailable(RuntimeError):
    """Laya could not be reached for a verdict that REQUIRES it.

    The engine SHIPS with the app and is always present, so a missing model,
    a disabled kill switch or a wedged daemon is a FAULT to surface - never a
    reason to decide a meaning with a word list or an LLM in its place.
    """


# A verdict that requires the engine retries this many times (each attempt
# relaunches the daemon) before it fails the node.
_ABSENCE_ATTEMPTS = 3


def absence_required(text: str) -> bool:
    """Is *text* an absence?  REQUIRED verdict: retries, then raises.

    Laya is not optional here: the daemon is burst-loaded (load -> use ->
    unload), so a ``None`` usually means "reload me".  Each attempt therefore
    releases the daemon (the next call relaunches it) and waits before trying
    again.  After ``_ABSENCE_ATTEMPTS`` the caller gets
    :class:`LayaUnavailable` with the reason, so a broken install stops the
    run loudly instead of silently degrading to a phrase list.
    """
    body = str(text or "").strip()
    if not body:
        return False
    for attempt in range(_ABSENCE_ATTEMPTS):
        p = _noul(body[:600], _ABSENCE_STATEMENT)
        if p is not None:
            logger.info("Laya absence verdict: p=%.3f (%s)", p, body[:60])
            return bool(p > _NOUL_THRESHOLD)
        try:
            from AI import laya_client
            # Drop the wedged/absent daemon so the next attempt relaunches it.
            laya_client.release(wait=0.5)
        except Exception:  # noqa: BLE001 - the retry still has to happen
            pass
        if attempt + 1 < _ABSENCE_ATTEMPTS:
            time.sleep(min(1.0 * (attempt + 1), 3.0))
    raise LayaUnavailable(
        "Laya did not answer the absence verdict after %d attempts (%r). "
        "The engine ships with the app: check AI/bin/laya.exe, the GGUF under "
        "AI/models/laya-GGUF, LOOPER_LAYA / LOOPER_LAYA_EXE, and that "
        "LOOPER_LAYA is not set to 0/off."
        % (_ABSENCE_ATTEMPTS, body[:80])
    )

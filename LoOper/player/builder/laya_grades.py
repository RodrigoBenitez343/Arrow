"""laya_grades.py — Laya verdicts for the builder's micro-decisions.

Every grade is a single encoder pass (noul/choice) and every grade is
FAIL-OPEN: None means "Laya unavailable" and callers degrade to LLM
judgment instead of blocking the build.
"""

import logging

from player.builder.node_templates import NODE_TYPES
from player.builder.trace import emit as _emit

logger = logging.getLogger(__name__)

#: Probability floor: a grade at or above it counts as a pass.
PASS_FLOOR = 0.5

#: Ask gate: Laya must be this sure the user owes information.
ASK_THRESHOLD = 0.8

#: Done gate: Laya must be this sure the chain is complete.
DONE_THRESHOLD = 0.5


def _client():
    """The embedded Laya client, or None when unavailable (never raises)."""
    try:
        from AI import laya_client as _lc
        if _lc.available() or _lc.ensure_running():
            return _lc
        return None
    except Exception:
        return None


def grade_recording(question, recording_meta):
    """noul: does the recording cover the step the builder asked for?

    Returns True (pass) / False (fail) / None (Laya unavailable).
    """
    try:
        _lc = _client()
        if _lc is None:
            return None
        premise = (
            f"Step the builder asked for: {str(question or '')[:300]}\n"
            f"Recording summary: {str(recording_meta or '')[:400]}"
        )
        p = _lc.noul(premise, "The recording covers the step it was asked for.")
        if p is None:
            return None
        return bool(p > PASS_FLOOR)
    except Exception:
        logger.warning("grade_recording failed", exc_info=True)
        return None


def grade_description(description, summary):
    """noul: does the description name what this chain/node does?

    Returns True / False / None (unavailable).
    """
    try:
        _lc = _client()
        if _lc is None:
            return None
        premise = (
            f"Description: {str(description or '')[:300]}\n"
            f"What the chain actually does: {str(summary or '')[:400]}"
        )
        p = _lc.noul(premise, "The description names what the chain does.")
        if p is None:
            return None
        return bool(p > PASS_FLOOR)
    except Exception:
        logger.warning("grade_description failed", exc_info=True)
        return None


def pick_node_type(goal, canvas='', transcript=None, trace=None):
    """choice: which node type should be created next?

    The criteria carry each type's PURPOSE + first usage hint from the
    runbook (``runbook.criteria_for``), so the pick is grounded in what the
    node is FOR, not just its name.  Returns a node type string, or None
    (unavailable / no pick).
    """
    try:
        _lc = _client()
        if _lc is None:
            _emit(trace, 'node type: Laya unavailable')
            return None
        from player.builder.runbook import criteria_for
        criteria = {t: criteria_for(t) for t in NODE_TYPES}
        picked = _lc.choice(
            _premise(goal, canvas, transcript),
            "Which node type should be created next?", criteria)
        _emit(trace, 'node type choice: offered %s → %s'
              % (', '.join(criteria), picked or 'none'))
        return picked if picked in criteria else None
    except Exception:
        logger.warning("pick_node_type failed", exc_info=True)
        return None


def grade_chain_match(goal, step, name, signal, trace=None):
    """noul: is this chain the goal's next step?  The runtime's framing.

    Uses the EXACT premise/hypothesis shape the orchestrator's direct-chain
    gate was calibrated on ("The next step toward the goal is to {signal}.")
    — a generic "does this chain perform this task" read is not a shape the
    tiny model holds: measured live 2026-10-09, that framing scored a
    calculator chain 0.984 for "Ask the user for the search query to scrape"
    (shared instruction phrases) while the runtime framing read the same
    pair 0.124 and every google/duckduckgo step 0.00-0.17 (→ teach).

    ``goal`` may be '' (the premise then carries the step alone).  Returns
    the probability (float), or None (Laya unavailable / read failed) — the
    caller (``composer._match_chain``) applies floor + margin + object
    rules; None never blocks the build.
    """
    try:
        _lc = _client()
        if _lc is None:
            _emit(trace, 'chain match %r: Laya unavailable → text fallback'
                  % str(step)[:60])
            return None
        goal_text = str(goal or '').strip()
        premise = f"Goal: {goal_text[:300]}\n" if goal_text else ''
        premise += f"Next step: {str(step or '')[:200]}"
        p = _lc.noul(
            premise,
            f"The next step toward the goal is to {str(signal or '')[:240]}.")
        _emit(trace, 'chain match %r vs %r: p=%s'
              % (str(step or '')[:60], str(name or '')[:40],
                 'n/a' if p is None else '%.2f' % p))
        return p
    except Exception:
        logger.warning("grade_chain_match failed", exc_info=True)
        return None


# --------------------------------------------------------------- planning
#
# The builder's routing uses the SAME two Laya shapes the orchestrator does:
# ``noul`` for yes/no gates and ``choice`` for picking among candidates.  Laya
# selects; it never invents candidates — the action menu is supplied.

def _premise(goal, canvas, transcript, thought=None):
    """Goal + canvas + steps taken (+ the reasoning LLM's plan) — the state
    Laya reads for a decision."""
    steps = '\n'.join(transcript) if transcript else '(none yet)'
    premise = (
        f"Goal: {str(goal or '')[:400]}\n"
        f"Canvas:\n{str(canvas or '(empty)')[:800]}\n"
        f"Steps so far:\n{steps[:800]}"
    )
    if thought:
        premise += f"\nReasoning: {str(thought)[:300]}"
    return premise


def wants_to_ask(goal, canvas, transcript, trace=None):
    """noul: must the user be asked before the build can continue?"""
    try:
        _lc = _client()
        if _lc is None:
            _emit(trace, 'ask-probe: Laya unavailable → no ask')
            return False
        p = _lc.noul(
            _premise(goal, canvas, transcript),
            "The user must be asked for input before the build can continue.")
        if p is None:
            _emit(trace, 'ask-probe: read failed → no ask')
            return False
        _emit(trace, 'ask-probe: p=%.2f (ask if > %.2f) → %s'
              % (p, ASK_THRESHOLD, 'ASK' if p > ASK_THRESHOLD else 'no'))
        return bool(p > ASK_THRESHOLD)
    except Exception:
        logger.warning("wants_to_ask failed", exc_info=True)
        return False


def is_done(goal, canvas, transcript, trace=None):
    """noul: is the system chain complete and nothing left to build?"""
    try:
        if not transcript:
            return False           # nothing built yet — never 'done'
        _lc = _client()
        if _lc is None:
            _emit(trace, 'done-probe: Laya unavailable → keep building')
            return False
        p = _lc.noul(
            _premise(goal, canvas, transcript),
            "The system chain is complete and nothing remains to build.")
        if p is None:
            _emit(trace, 'done-probe: read failed → keep building')
            return False
        _emit(trace, 'done-probe: p=%.2f (done if > %.2f) → %s'
              % (p, DONE_THRESHOLD,
                 'DONE' if p > DONE_THRESHOLD else 'keep building'))
        return bool(p > DONE_THRESHOLD)
    except Exception:
        logger.warning("is_done failed", exc_info=True)
        return False


def pick_action(goal, canvas, transcript, actions, trace=None, thought=None):
    """choice: which action moves the build forward?

    ``actions`` is an iterable of ``(name, description)``.  ``thought`` is the
    reasoning LLM's plan for the next step, folded into the premise.  Returns
    the picked action name, or None when Laya is unavailable / picked nothing.
    """
    try:
        _lc = _client()
        if _lc is None:
            _emit(trace, 'action choice: Laya unavailable')
            return None
        menu = {str(name): str(desc) for name, desc in actions}
        if not menu:
            return None
        picked = _lc.choice(
            _premise(goal, canvas, transcript, thought),
            "Which action moves the build forward?", menu)
        _emit(trace, 'action choice: offered %s → %s'
              % (', '.join(menu), picked or 'none'))
        return picked if picked in menu else None
    except Exception:
        logger.warning("pick_action failed", exc_info=True)
        return None

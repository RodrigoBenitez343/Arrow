"""CRAG query rewrite for the corrective branches.

Port of `utils/crag/scripts/utils.py:extract_keywords`.  Upstream keeps one
few-shot prompt per dataset (popqa / pubqa / arc_challenge) and asks GPT-3.5
for "at most three keywords separated by comma"; the rewritten query is what
the external-knowledge stage then searches with.  The dataset prompts have no
form-field equivalent, so this is ONE few-shot built from the same shape
(question -> comma-separated keyword query) and the model call is injected:
the node passes its own engine, the offline scripts pass theirs.
"""

import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_MAX_KEYWORDS = 3

# The three upstream shots keep the model on "entity, attribute, detail";
# they are dataset-agnostic (people, places, claims) so they transfer to a
# form field unchanged.
_REWRITE_PROMPT = (
    "Extract at most three keywords separated by comma from the following "
    "question as queries for a search, including the main intent and the "
    "entity the question is about.\n\n"
    "question: What is Henry Feilden's occupation?\n"
    "query: Henry Feilden, occupation\n\n"
    "question: In what city was Billy Carlson born?\n"
    "query: city, Billy Carlson, born\n\n"
    "question: What sport does Kiribati men's national basketball team "
    "play?\nquery: sport, Kiribati men's national basketball team play\n\n"
    "question: {question}\nquery:"
)

_REWRITE_SYSTEM = (
    "You rewrite a question into a short search query made of comma-"
    "separated keywords. Output the keywords only."
)


# A small model often answers with a PREAMBLE instead of the query - live,
# the 3B replied 'Here are the rewritten search queries with at most three
# keywords separated by commas:' and that sentence became the query the
# corrective store was searched with.  These markers drop such a line instead
# of using it.
_BOILERPLATE = (
    "here are", "here is", "here's", "separated by comma",
    "keywords:", "sure,", "certainly", "as requested", "the answer is",
    "following question", "extracted keywords",
)


def _clean_line(line: str) -> str:
    """One reply line without its list marker / 'query:' prefix / quotes."""
    s = str(line or "").strip().strip('"').strip("'").strip()
    s = re.sub(r"^[-*\u2022\d.\s]{1,4}(?=\S)", "", s).strip()
    if s.lower().startswith("query:"):
        s = s[6:].strip()
    return s.strip('"').strip("'").strip()


def _first_line(text: str) -> str:
    """The first line that is an ANSWER, not the model's preamble."""
    for line in str(text or "").splitlines():
        cleaned = _clean_line(line)
        if not cleaned:
            continue
        low = cleaned.lower()
        if any(marker in low for marker in _BOILERPLATE):
            continue
        return cleaned
    return ""


def rewrite_query(question: str, llm: Optional[Callable[[str, str], str]],
                  max_keywords: int = _MAX_KEYWORDS) -> str:
    """The keyword query for *question*, or ``""`` when it cannot be made.

    An empty result is not an error: the caller keeps the original question,
    which is what CRAG does when the rewrite call fails.
    """
    body = str(question or "").strip()[:600]
    if not body or not callable(llm):
        return ""
    try:
        reply = llm(_REWRITE_PROMPT.format(question=body), _REWRITE_SYSTEM)
    except Exception as exc:  # noqa: BLE001 - a failed rewrite is not fatal
        logger.debug("CRAG rewrite failed: %s", exc)
        return ""
    line = _first_line(reply)
    if not line:
        return ""
    parts = [p.strip() for p in line.split(",") if p.strip()]
    if not parts:
        return line[:200]
    return ", ".join(parts[:max(1, int(max_keywords))])[:200]


__all__ = ["rewrite_query"]

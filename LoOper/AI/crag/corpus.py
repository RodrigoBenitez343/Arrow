"""Document -> sections -> segments -> retrieval blocks (the shared chunker).

ONE chunker, two consumers, and that is the whole reason this module exists:
the Context node indexes BLOCKS with it (``AI/context_index.py``) and the form
filler scores SEGMENTS with it, so a stored vector and the text it describes can
never disagree about where a value lives.

Two units, and the split matters.  Measured with the real resume and the real
evaluator (Laya's typed-question encoder): the SAME sentence scores 0.82 for its
own field standing alone and 0.14 once it is padded into a 250-char window,
because the probability decays as unrelated text follows the value inside one
premise.  So the unit the evaluator scores is the SEGMENT (one line, or one
``·`` part of a line), while the unit an embedding ranks is the BLOCK - a short
label like 'City' needs surrounding context to find its region.

Blocks never cross a section.  A block that mixed the candidate's contact header
with a job-history row embedded as ONE vector that matched both and proved
neither: the retrieval stage then ranked an employer's city above the
candidate's own, and the pack's ``[section]`` tag named a section its text only
partly came from.
"""

import hashlib
import re

# A segment is one line / one ' · ' part of a line, capped so a long bullet
# still splits at sentence ends (resume-scale measurement, not a guess).
SEGMENT_MAX_CHARS = 200
# Segments are grouped into RETRIEVAL BLOCKS of about this size.
BLOCK_CHARS = 250

_SEGMENT_SPLIT = re.compile(r"\s+[\u00b7|]\s+")
_MD_LINK = re.compile(r"\[([^\]\n]{1,200})\]\([^)\n]{0,600}\)")
_HEADING = re.compile(r"^(#{1,6})\s*(.+)$")


def plain_source(text):
    """Markdown -> the text a reader sees (a link keeps its visible label).

    `[benitezrodrigo169@gmail.com](mailto:...)` is scored and copied as
    ``benitezrodrigo169@gmail.com``: the URL half is markup, not the value, and
    it is what pushed the address into the middle of a long premise.
    """
    t = _MD_LINK.sub(r"\1", str(text or ""))
    return t.replace("**", "").replace("__", "")


def segments(text):
    """``[(section, segment)]`` - short, single-value units, section-tagged.

    Sections come from the markdown headings: they are what tells an employer's
    history row apart from the candidate's own contact block.
    """
    out = []
    section = ""
    for raw in str(text or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        head = _HEADING.match(line)
        if head:
            section = re.sub(r"\s+", " ", head.group(2)).strip()[:80]
            line = section
        line = plain_source(line).strip(" \t-*#")
        if not line:
            continue
        parts = [p.strip() for p in _SEGMENT_SPLIT.split(line) if p.strip()]
        for part in parts or [line]:
            part = re.sub(r"\s+", " ", part).strip()
            if not part:
                continue
            pieces = ([part] if len(part) <= SEGMENT_MAX_CHARS else
                      [s.strip() for s in re.split(r"(?<=[.!?])\s+", part)
                       if s.strip()])
            for piece in pieces:
                out.append((section, piece[:SEGMENT_MAX_CHARS]))
    return out


def blocks(segs, size=BLOCK_CHARS):
    """Group segments into blocks of about ``size`` chars, ONE SECTION each.

    A new section always starts a new block, so a block's vector describes one
    part of the document and a block's ``[section]`` tag is never a lie.  A
    section longer than ``size`` simply becomes several blocks, still in order.
    """
    out, current, used, cur_section = [], [], 0, None
    for section, segment in segs or []:
        if current and (section != cur_section
                        or used + len(segment) + 2 > size):
            out.append(current)
            current, used = [], 0
        current.append((section, segment))
        cur_section = section
        used += len(segment) + 2
    if current:
        out.append(current)
    return out


def identity_section(segs):
    """The section the document opens with - the block that holds WHO it is about.

    Name / phone / email / city live there, and they embed poorly against a short
    field label, so the retrieval stage reserves slots for it (see
    ``form_filler_modules.crag``) instead of letting a rank win on length.
    """
    for section, _segment in segs or []:
        return section
    return ""


def sha256_text(text):
    """Content hash of a text, whitespace-normalised.

    Normalising first is what makes the hash survive the trip through a Context
    node's pool: the node SERVES ``"## Documents\\n" + text`` and the consumer
    SPLITS the header back off, and the two joins never agree byte for byte.
    """
    body = re.sub(r"\s+", " ", str(text or "")).strip()
    return hashlib.sha256(body.encode("utf-8", "ignore")).hexdigest()


def _is_marker(text):
    """True for the pool's own ``## Documents`` heading - not document content."""
    return re.sub(r"\s+", " ", str(text or "")).strip().lower() == "documents"


def shape(text, origin="documents"):
    """``(entries, block_meta)`` for one source text.

    ``entries`` is the flat segment list the evaluator scores, each carrying its
    block, its block's text (the unit the embedding ranks), whether it belongs to
    the document's identity section, and the ``sha`` the persistent index keys on.
    ``block_meta`` is what an index builder stores per block.
    """
    segs = [(s, t) for s, t in segments(text) if not _is_marker(t)]
    first = identity_section(segs)
    sha = sha256_text(text)
    entries, meta = [], []
    for ordinal, block in enumerate(blocks(segs)):
        body = " ".join(seg for _section, seg in block)
        section = block[0][0] if block else ""
        head = bool(first) and section == first
        meta.append({"ordinal": ordinal, "section": section, "text": body,
                    "head": head,
                    "segments": [[s, t] for s, t in block]})
        for section, segment in block:
            entries.append({"text": segment, "section": section,
                            "block": ordinal, "block_text": body,
                            "head": head, "source": origin, "sha": sha})
    return entries, meta

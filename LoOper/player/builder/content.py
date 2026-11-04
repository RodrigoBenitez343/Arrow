"""content.py — putting real material into a node, in the node's own place.

The panel's content editor takes what the user hands it (a markdown file, a
PDF, a docx, a folder of notes, a pasted document, a recording file) and
writes it into the **field that node actually reads at run time**:

* ``context``  → ``documents`` (file paths the pool reads) or ``skills``
  (literal notes / skill files) — the two streams ``context_ops`` serves;
* ``llm``      → ``system_message`` (reference material) or ``prompt``;
* ``input``    → ``default_value`` / ``user_prompt``;
* ``code``     → ``code``; ``conditional`` → ``llm_prompt`` / ``code``;
* ``handle``   → ``goal_description``; ``form_filler`` → ``instruction``;
* ``sequence`` / ``web_sequence`` / ``chain_import`` → their recording path.

Every unsupported type, unreadable file, empty extraction or oversized body
raises (``errors.py``).  There is no "skip it silently" path: material that
cannot be read is material the run will not have.
"""

import json
import os
from dataclasses import dataclass

from .errors import (ContentError, ContentTooLargeError, NodeTargetError,
                     UnsupportedContentError)
from .node_templates import node_type_of, short_type

#: Extensions the CONTEXT POOL reads at run time (``context_ops._ctx_list_text``).
POOL_EXTS = ('.txt', '.md', '.csv', '.json', '.log')
#: Extensions this module can extract text from (superset used for text slots).
DOC_EXTS = POOL_EXTS + ('.docx', '.pdf')
#: Plain-text extensions accepted for text slots (read as UTF-8).
TEXT_EXTS = DOC_EXTS + ('.markdown', '.yaml', '.yml', '.ini', '.cfg', '.py',
                        '.html', '.htm', '.xml', '.tsv')
#: Recording / chain files (JSON).
RECORDING_EXTS = ('.json',)

#: A text field is a chain property: keep bodies inside a size a chain file
#: can hold and an LLM node can actually be given.
MAX_TEXT_CHARS = 200_000
#: The pool streams are path/note lists; a single note is bounded too.
MAX_NOTE_CHARS = 200_000


@dataclass(frozen=True)
class Slot:
    """One destination field of a node."""

    name: str            # the node property
    label: str           # what the panel shows
    mode: str            # 'files' | 'text' | 'path'
    accepts: tuple       # ('file',) ('text',) ('file', 'text')
    hint: str


@dataclass
class Parsed:
    """A source parsed into material, before it is written anywhere."""

    kind: str            # 'file' | 'text'
    text: str
    path: str = ''
    label: str = ''
    chars: int = 0

    def __post_init__(self):
        self.chars = len(self.text or '')


@dataclass
class ContentResult:
    """What one write actually did (the panel's success line)."""

    node_id: str
    node_type: str
    slot: str
    slot_label: str
    source: str
    kind: str
    chars: int
    detail: str

    def line(self):
        return ('%s [%s] %s — %s.%s ← %s — %s'
                % (self.node_id, self.node_type, self.slot_label,
                   self.node_type, self.slot, self.source, self.detail))


# ------------------------------------------------------------------ slots

_SLOTS = {
    'context': (
        Slot('documents', 'Documents (file paths the pool reads)', 'files',
             ('file',), 'source material: resumes, specs, reference docs'),
        Slot('skills', 'Skills (notes / skill files)', 'files',
             ('file', 'text'), 'how-to knowledge, instructions, conventions'),
    ),
    'llm': (
        Slot('system_message', 'System message (instruction + reference)', 'text',
             ('file', 'text'), 'the standing instructions for this step'),
        Slot('prompt', 'Prompt template', 'text', ('file', 'text'),
             'what the step is asked to do (a wired prompt replaces it)'),
    ),
    'input': (
        Slot('default_value', 'Default value', 'text', ('file', 'text'),
             'value used when nothing else resolves'),
        Slot('user_prompt', 'Question shown to the user', 'text',
             ('file', 'text'), 'the wording of the question'),
    ),
    'output': (
        Slot('tts_text', 'Spoken text', 'text', ('file', 'text'),
             'text spoken instead of the raw result'),
        Slot('label', 'Label', 'text', ('file', 'text'), 'what the result is called'),
    ),
    'code': (
        Slot('code', 'Python source', 'text', ('file', 'text'),
             'the script the code node runs'),
    ),
    'conditional': (
        Slot('llm_prompt', 'LLM criterion', 'text', ('file', 'text'),
             'the condition an LLM validates'),
        Slot('code', 'Python condition', 'text', ('file', 'text'),
             'the condition a code evaluator runs'),
        Slot('description', 'Description', 'text', ('file', 'text'),
             'what this check decides'),
    ),
    'handle': (
        Slot('goal_description', 'On-screen goal', 'text', ('file', 'text'),
             'what the handle must find and act on'),
        Slot('target_description', 'Drag target', 'text', ('file', 'text'),
             'where a drag ends'),
    ),
    'form_filler': (
        Slot('instruction', 'Filling instruction', 'text', ('file', 'text'),
             'what the filler must accomplish'),
    ),
    'chain_import': (
        Slot('chain_file_path', 'Imported chain file', 'path', ('file',),
             'the chain JSON this node runs'),
    ),
    'sequence': (
        Slot('sequence_file', 'Recorded sequence file', 'path', ('file',),
             'the desktop recording this node replays'),
    ),
    'web_sequence': (
        Slot('session_file', 'Recorded web session', 'path', ('file',),
             'the browser recording this node replays'),
    ),
    'action': (Slot('label', 'Label', 'text', ('file', 'text'), 'what the action is'),),
    'mcp': (Slot('label', 'Label', 'text', ('file', 'text'), 'what the tool call is'),),
    'container': (Slot('label', 'Label', 'text', ('file', 'text'), 'what this groups'),),
}


def slots_for(node):
    """The destination slots of a live node (raises for unknown types)."""
    ntype = short_type(node_type_of(node))
    slots = _SLOTS.get(ntype)
    if not slots:
        raise NodeTargetError(
            'a %r node has no content destination' % (ntype or 'unknown'))
    return slots


def default_slot(node, parsed):
    """The slot a parsed source belongs in by default.

    Files go to the file stream, pasted text to the note stream, recordings
    to the recording property, everything else to the node's first slot.
    """
    slots = slots_for(node)
    ntype = short_type(node_type_of(node))
    if ntype == 'context':
        wanted = 'documents' if parsed.kind == 'file' else 'skills'
        for slot in slots:
            if slot.name == wanted:
                return slot
    for slot in slots:
        if slot.mode == 'path' and parsed.kind == 'file':
            return slot
    return slots[0]


def slot_by_name(node, name):
    for slot in slots_for(node):
        if slot.name == name:
            return slot
    raise NodeTargetError(
        'a %s node has no %r destination (available: %s)'
        % (short_type(node_type_of(node)), name,
           ', '.join(s.name for s in slots_for(node))))


# ------------------------------------------------------------------ parse

def _ext(path):
    return os.path.splitext(str(path))[1].lower()


def _read_document(path):
    """Extract a document's text with the SAME reader the pool/runtime uses."""
    from player.multi_sequence.llm_executor_resources import rag_utils as rag
    ext = _ext(path)
    if ext == '.pdf':
        text = rag._extract_pdf_text(path)
    elif ext == '.docx':
        text = rag._extract_docx_text(path)
    else:
        text = rag._read_text_file(path)
    if not text or not str(text).strip():
        raise ContentError(
            'the reader produced no text for %r (%s). The file is empty, '
            'scanned-only or damaged — the run would get nothing from it.'
            % (path, ext or 'no extension'))
    return str(text)


def _read_plain_text(path):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            text = handle.read()
    except UnicodeDecodeError as exc:
        raise ContentError(
            '%r is not valid UTF-8 text (%s); convert it to UTF-8 or read it '
            'as a document' % (path, exc)) from exc
    except OSError as exc:
        raise ContentError('could not read %r: %s' % (path, exc)) from exc
    if not text.strip():
        raise ContentError('%r is empty' % path)
    return text


def parse_file(path, for_pool=False):
    """One file -> ``Parsed``.  ``for_pool`` restricts to pool-readable types."""
    path = os.path.abspath(str(path or '').strip())
    if not path:
        raise ContentError('no file path given')
    if os.path.isdir(path):
        raise ContentError('%r is a folder; use the folder action' % path)
    if not os.path.isfile(path):
        raise ContentError('no such file: %r' % path)
    ext = _ext(path)
    allowed = POOL_EXTS + ('.docx', '.pdf') if for_pool else TEXT_EXTS
    if ext not in allowed:
        raise UnsupportedContentError(
            '%r has extension %r, which has no reader here. Supported: %s'
            % (path, ext or '(none)', ', '.join(sorted(allowed))))
    text = _read_document(path)
    if len(text) > MAX_TEXT_CHARS:
        raise ContentTooLargeError(
            '%r extracts to %d characters (limit %d). Split it, or keep the '
            'file as a context document so the pool reads it at run time.'
            % (path, len(text), MAX_TEXT_CHARS))
    return Parsed(kind='file', text=text, path=path,
                  label=os.path.basename(path))


def parse_folder(folder, for_pool=False):
    """Every supported file in a folder (non-recursive), parsed in order."""
    folder = os.path.abspath(str(folder or '').strip())
    if not os.path.isdir(folder):
        raise ContentError('no such folder: %r' % folder)
    allowed = POOL_EXTS if for_pool else TEXT_EXTS
    names = sorted(name for name in os.listdir(folder)
                   if os.path.isfile(os.path.join(folder, name))
                   and _ext(name) in allowed)
    if not names:
        raise UnsupportedContentError(
            '%r contains no supported file (%s)'
            % (folder, ', '.join(sorted(allowed))))
    return [parse_file(os.path.join(folder, name), for_pool=for_pool)
            for name in names]


def parse_text(text):
    """Pasted material -> ``Parsed`` (a literal note)."""
    body = str(text or '')
    if not body.strip():
        raise ContentError('the pasted text is empty')
    if len(body) > MAX_NOTE_CHARS:
        raise ContentTooLargeError(
            'the pasted text is %d characters (limit %d)'
            % (len(body), MAX_NOTE_CHARS))
    return Parsed(kind='text', text=body, label='pasted text')


# ------------------------------------------------------------------ write

def _as_list(raw):
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    text = str(raw).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContentError(
            'the node holds a malformed list in this stream: %s' % exc) from exc
    if not isinstance(data, list):
        raise ContentError('the node holds %r, not a list' % type(data).__name__)
    return data


def _apply_now(node, slot, parsed):
    """Write one parsed source into one slot (GUI thread)."""
    ntype = short_type(node_type_of(node))
    node_id = str(getattr(node, 'id', '') or '?')

    if slot.mode == 'path':
        if parsed.kind != 'file':
            raise ContentError(
                '%s takes a file path; pasted text cannot be its source'
                % slot.label)
        if _ext(parsed.path) not in RECORDING_EXTS:
            raise UnsupportedContentError(
                '%s takes a JSON file, got %r' % (slot.label, _ext(parsed.path)))
        if slot.name == 'chain_file_path':
            setter = getattr(node, 'set_chain_import_data', None)
            if not callable(setter):
                raise ContentError(
                    'this chain_import node has no set_chain_import_data(); '
                    'the file path cannot be applied')
            setter(parsed.path, 'full', '', 1, 0, True)
        else:
            node.set_property(slot.name, parsed.path)
        return ContentResult(
            node_id=node_id, node_type=ntype, slot=slot.name,
            slot_label=slot.label, source=parsed.label, kind=parsed.kind,
            chars=parsed.chars, detail='path set to %s' % parsed.path)

    if slot.mode == 'text':
        body = parsed.text
        node.set_property(slot.name, body)
        return ContentResult(
            node_id=node_id, node_type=ntype, slot=slot.name,
            slot_label=slot.label, source=parsed.label, kind=parsed.kind,
            chars=len(body), detail='%d characters written' % len(body))

    if slot.mode == 'files':
        entries = _as_list(node.get_property(slot.name))
        entry = parsed.path if parsed.kind == 'file' else parsed.text
        if entry in entries:
            return ContentResult(
                node_id=node_id, node_type=ntype, slot=slot.name,
                slot_label=slot.label, source=parsed.label, kind=parsed.kind,
                chars=parsed.chars, detail='already in the list (unchanged)')
        entries.append(entry)
        node.set_property(slot.name, json.dumps(entries, ensure_ascii=False))
        return ContentResult(
            node_id=node_id, node_type=ntype, slot=slot.name,
            slot_label=slot.label, source=parsed.label, kind=parsed.kind,
            chars=parsed.chars,
            detail='added (list now holds %d entr%s)'
                   % (len(entries), 'y' if len(entries) == 1 else 'ies'))

    raise ContentError('unknown slot mode %r' % slot.mode)


def _resolve_node(ctx, node_id):
    manager = ctx.get('graph_manager')
    if callable(manager):
        manager = manager()
    if manager is None:
        raise NodeTargetError('no graph manager is attached to the canvas')
    node = manager.get_node_by_id(str(node_id or ''))
    if node is None:
        raise NodeTargetError('no node %r is on the canvas' % node_id)
    return node


def ingest(ctx, node_id, sources, slot_name=None):
    """Write one or more parsed sources into ``node_id`` (GUI marshalled).

    ``sources`` is a list of ``Parsed``.  Returns ``[ContentResult]``.
    """
    run = ctx.get('run_on_gui')
    if not callable(run):
        raise ContentError('the builder has no GUI bridge, so content cannot be '
                           'written into a node')

    def _write():
        node = _resolve_node(ctx, node_id)
        slot = (slot_by_name(node, slot_name) if slot_name
                else default_slot(node, sources[0]))
        for parsed in sources:
            if slot.mode == 'files' and parsed.kind == 'file' \
                    and _ext(parsed.path) not in POOL_EXTS + ('.docx', '.pdf'):
                raise UnsupportedContentError(
                    '%r cannot live in %s: the pool reads %s'
                    % (parsed.path, slot.label, ', '.join(POOL_EXTS + ('.docx', '.pdf'))))
        return [_apply_now(node, slot, parsed) for parsed in sources]

    return run(_write)


def ingest_file(ctx, node_id, path, slot_name=None, for_pool=None):
    """A file: parse outside the GUI thread, then write on it."""
    if for_pool is None:
        for_pool = _slot_is_pool(ctx, node_id, slot_name)
    parsed = parse_file(path, for_pool=for_pool)
    return ingest(ctx, node_id, [parsed], slot_name)


def ingest_folder(ctx, node_id, folder, slot_name=None):
    """Every supported file in a folder."""
    for_pool = _slot_is_pool(ctx, node_id, slot_name)
    parsed = parse_folder(folder, for_pool=for_pool)
    return ingest(ctx, node_id, parsed, slot_name)


def ingest_text(ctx, node_id, text, slot_name=None):
    """Pasted material (a literal note / a field body)."""
    return ingest(ctx, node_id, [parse_text(text)], slot_name)


def _slot_is_pool(ctx, node_id, slot_name):
    """True when the destination is a context stream (pool-read types only)."""
    run = ctx.get('run_on_gui')
    if not callable(run):
        raise ContentError('the builder has no GUI bridge, so content cannot be '
                           'written into a node')

    def _check():
        node = _resolve_node(ctx, node_id)
        if short_type(node_type_of(node)) != 'context':
            return False
        if slot_name:
            return slot_by_name(node, slot_name).mode == 'files'
        return True

    return run(_check)

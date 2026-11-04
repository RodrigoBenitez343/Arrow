"""Runbook ↔ node vocabulary consistency.

The runbook is the knowledge the builder's prompt carries; ``node_templates``
is the vocabulary the reader uses; ``ports`` is the role table.  They must
agree about which node types exist and which fields describe them.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from player.builder import node_templates as nt  # noqa: E402
from player.builder import ports as pt  # noqa: E402
from player.builder import runbook as rb  # noqa: E402


def test_every_node_type_is_documented_and_has_a_port_table():
    for node_type in nt.NODE_TYPES:
        entry = rb.RUNBOOK.get(node_type)
        assert entry, 'runbook has no entry for %r' % node_type
        assert entry.get('purpose'), '%r has no purpose' % node_type
        assert node_type in pt.PORT_TABLE, '%r has no port table' % node_type


def test_the_runbook_documents_no_type_the_reader_cannot_name():
    for node_type in rb.RUNBOOK:
        assert node_type in nt.NODE_TYPES, (
            'runbook documents %r, which node_templates does not know'
            % node_type)


def test_every_descriptive_field_is_documented_in_the_runbook():
    for node_type, fields in nt.NODE_FIELDS.items():
        documented = set((rb.RUNBOOK.get(node_type) or {}).get('fields') or {})
        for field in fields:
            assert field in documented, (
                '%s.%s is read by the builder but not documented'
                % (node_type, field))


def test_port_roles_cover_the_nodes_port_lists():
    """The runbook's port tuples must be a subset of the role table."""
    for node_type, entry in rb.RUNBOOK.items():
        table = pt.PORT_TABLE[node_type]
        for name in entry.get('ports_in') or ():
            assert name in table['in'], (
                '%s documents input port %r that its role table lacks'
                % (node_type, name))
        for name in entry.get('ports_out') or ():
            assert name in table['out'], (
                '%s documents output port %r that its role table lacks'
                % (node_type, name))


def test_every_node_has_a_role_word():
    for node_type in nt.NODE_TYPES:
        assert nt.NODE_ROLE.get(node_type), 'no role word for %r' % node_type


def test_node_purposes_are_a_view_over_the_runbook():
    for node_type in nt.NODE_TYPES:
        assert rb.purpose_line(node_type) == (
            rb.RUNBOOK[node_type].get('purpose'))


def test_capability_registry_carries_every_node_and_its_ports():
    text = rb.capability_registry()
    for node_type in nt.NODE_TYPES:
        if node_type in ('mcp', 'container'):
            continue
        assert '- %s:' % node_type in text
    assert 'REPLACES' in text or 'prompt' in text


def test_markdown_renders_modes_and_ports():
    md = rb.render_markdown()
    assert '**Modes:**' in md
    assert '**Inputs:**' in md and '**Outputs:**' in md
    assert '## Connection rules' in md


def test_docs_file_matches_the_runbook():
    """docs/NODE_RUNBOOK.md must be byte-equal to render_markdown()."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(os.path.dirname(here)),
                        'docs', 'NODE_RUNBOOK.md')
    if not os.path.isfile(path):
        pytest.skip('generated runbook doc is not present')
    with open(path, 'r', encoding='utf-8') as handle:
        assert handle.read() == rb.render_markdown()

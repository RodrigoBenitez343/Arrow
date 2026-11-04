"""player.builder — the chain analyst docked beside the canvas.

It **reads** (``read_graph``, ``read_node``) and answers questions about the
chain from the resolved canvas, and it may **change what the user selected**:
``edit_node`` writes settings on a selected node, ``wire_nodes`` connects or
disconnects the wire between the two selected nodes.  It cannot create or
delete a node, record or run anything.

Describing the chain and the visual walkthrough are the panel's buttons:
``flow.py`` holds the chain's own metadata (summary, section boxes, per-step
sentences) which the panel stores in the chain file and draws on the canvas.
The conversation neither writes it nor starts it.

Modules
-------
``snapshot``   the live canvas read once, with every port resolved to a role
``ports``      the role of every port on every node, and what an edge through
               it means (execution vs data, prompt vs context vs named IO)
``describe``   the deterministic topology digest: start, execution walk,
               branches, loops, data dependencies, phases, defects
``flow``       the chain's permanent self-description (summary / sections /
               steps) and the deterministic walkthrough script
``tools``      what the builder may read, and the two changes it may make
``assistant``  the model turn: prose over the evidence, at most three tool calls
``content``    writing user-supplied material into the field a node reads
``reasoning``  the gateway call (streaming; every failure raises)
``settings``   engine / model of the builder's own call
``runbook``    what each node is for (the knowledge the prompt carries)
``node_templates``  node vocabulary: types, roles, descriptive fields
``trace``      one ``[BUILDER]`` log line, mirrored to the panel

There is no silent degradation: every failure raises ``errors.BuilderError``.
"""

__all__ = [
    'assistant',
    'content',
    'describe',
    'errors',
    'flow',
    'node_templates',
    'ports',
    'reasoning',
    'runbook',
    'settings',
    'snapshot',
    'tools',
    'trace',
]

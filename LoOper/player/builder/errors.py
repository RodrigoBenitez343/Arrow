"""errors.py — the builder's hard failures.

The builder has NO silent degradation: every failure that stops an operation
is one of these exceptions, and the surface that catches it must show the
user what failed and why.  There is no "return an empty string and carry on"
path in this package.
"""


class BuilderError(Exception):
    """Base class for every builder failure."""

    #: Short label used by the panel when it renders the failure.
    kind = 'builder error'


class BuilderConfigError(BuilderError):
    """The builder cannot run: no gateway, no model, unusable settings."""

    kind = 'builder configuration'


class BuilderModelError(BuilderError):
    """The model call failed (HTTP status, transport, empty generation)."""

    kind = 'builder model'


class BuilderProtocolError(BuilderError):
    """The model did not answer with the reply/actions envelope."""

    kind = 'builder protocol'


class BuilderCanvasError(BuilderError):
    """The live canvas could not be read."""

    kind = 'builder canvas'


class UnknownPortError(BuilderError):
    """A port the node carries has no role in the port table."""

    kind = 'builder port'


class ToolArgumentError(BuilderError):
    """A tool call arrived with missing or invalid arguments."""

    kind = 'builder tool'


class UnknownToolError(BuilderError):
    """The model asked for a tool that does not exist."""

    kind = 'builder tool'


class FlowMetadataError(BuilderError):
    """The flow metadata a chain carries is malformed (never repaired)."""

    kind = 'builder flow'


class ContentError(BuilderError):
    """A content source could not be read into a node."""

    kind = 'builder content'


class UnsupportedContentError(ContentError):
    """The source type has no reader (and no reader will be guessed)."""

    kind = 'builder content'


class ContentTooLargeError(ContentError):
    """The extracted content does not fit the destination field."""

    kind = 'builder content'


class NodeTargetError(ContentError):
    """No node, or the wrong node, is selected for a content write."""

    kind = 'builder content'

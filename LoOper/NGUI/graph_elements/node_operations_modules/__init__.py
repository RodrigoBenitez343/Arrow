from .base import BaseNodeOperations
from .sequence import SequenceOperationsMixin
from .web_sequence import WebSequenceOperationsMixin
from .action import ActionOperationsMixin
from .conditional import ConditionalOperationsMixin
from .llm import LLMOperationsMixin
from .chain_import import ChainImportOperationsMixin
from .form_filler import FormFillerOperationsMixin
from .code import CodeOperationsMixin
from .container import ContainerOperationsMixin
from .context import ContextOperationsMixin
from .input import InputOperationsMixin
from .handle import HandleOperationsMixin
from .mcp import MCPOperationsMixin
from .output import OutputOperationsMixin
from .node_management import NodeManagementMixin

class NodeOperations(
    BaseNodeOperations,
    SequenceOperationsMixin,
    WebSequenceOperationsMixin,
    ActionOperationsMixin,
    ConditionalOperationsMixin,
    LLMOperationsMixin,
    ChainImportOperationsMixin,
    FormFillerOperationsMixin,
    CodeOperationsMixin,
    ContainerOperationsMixin,
    ContextOperationsMixin,
    InputOperationsMixin,
    HandleOperationsMixin,
    MCPOperationsMixin,
    OutputOperationsMixin,
    NodeManagementMixin
):
    """Handles all node operations including adding, deleting, and editing."""
    pass
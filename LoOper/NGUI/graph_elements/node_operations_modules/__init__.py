from .base import BaseNodeOperations
from .sequence import SequenceOperationsMixin
from .action import ActionOperationsMixin
from .conditional import ConditionalOperationsMixin
from .llm import LLMOperationsMixin
from .tts import TTSOperationsMixin
from .chain_import import ChainImportOperationsMixin
from .form_filler import FormFillerOperationsMixin
from .code import CodeOperationsMixin
from .node_management import NodeManagementMixin

class NodeOperations(
    BaseNodeOperations,
    SequenceOperationsMixin,
    ActionOperationsMixin,
    ConditionalOperationsMixin,
    LLMOperationsMixin,
    TTSOperationsMixin,
    ChainImportOperationsMixin,
    FormFillerOperationsMixin,
    CodeOperationsMixin,
    NodeManagementMixin
):
    """Handles all node operations including adding, deleting, and editing."""
    pass
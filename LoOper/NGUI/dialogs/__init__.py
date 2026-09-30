# dialogs/__init__.py

# Import all dialog classes to maintain backward compatibility
from .base_dialog import ModernDialog
from .fallback_dialogs import ActionFallbackConfigDialog
from .sequence_dialogs import SequencePropertiesDialog
from .web_sequence_dialogs import WebSequencePropertiesDialog
from .conditional_dialogs import AdvancedConditionalDialog
from .trigger_dialogs import TriggerConfigDialog, OCRTriggerConfigDialog, WaitConditionDialog, ConditionalLoopDialog
from .llm_dialogs import LLMPropertiesDialog
from .tts_dialogs import TTSPropertiesDialog
from .ai_settings_dialog import AISettingsDialog
from .chain_expansion_dialog import ChainExpansionDialog
from .form_filler_dialog import FormFillerDialog
from .code_node_dialog import CodeNodeDialog
from .container_dialog import ContainerNodeDialog
from .context_dialog import ContextDialog
from .agent_export_dialog import AgentExportDialog
from .utils import get_default_api_url

__all__ = [
    'ModernDialog',
    'ActionFallbackConfigDialog',
    'SequencePropertiesDialog', 
    'WebSequencePropertiesDialog',
    'AdvancedConditionalDialog',
    'TriggerConfigDialog',
    'OCRTriggerConfigDialog',
    'WaitConditionDialog',
    'ConditionalLoopDialog',
    'LLMPropertiesDialog',
    'TTSPropertiesDialog',
    'AISettingsDialog',
    'ChainExpansionDialog',
    'FormFillerDialog',
    'CodeNodeDialog',
    'ContainerNodeDialog',
    'ContextDialog',
    'AgentExportDialog',
    'get_default_api_url'
]
# dialogs/__init__.py

# Import all dialog classes to maintain backward compatibility
from .fallback_dialogs import ActionFallbackConfigDialog
from .sequence_dialogs import SequencePropertiesDialog
from .conditional_dialogs import AdvancedConditionalDialog
from .trigger_dialogs import TriggerConfigDialog, OCRTriggerConfigDialog, WaitConditionDialog, ConditionalLoopDialog
from .llm_dialogs import LLMPropertiesDialog
from .tts_dialogs import TTSPropertiesDialog
from .ai_settings_dialog import AISettingsDialog
from .utils import get_default_api_url

__all__ = [
    'ActionFallbackConfigDialog',
    'SequencePropertiesDialog', 
    'AdvancedConditionalDialog',
    'TriggerConfigDialog',
    'OCRTriggerConfigDialog',
    'WaitConditionDialog',
    'ConditionalLoopDialog',
    'LLMPropertiesDialog',
    'TTSPropertiesDialog',
    'AISettingsDialog',
    'get_default_api_url'
]
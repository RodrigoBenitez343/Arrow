# graphui/dialogs.py

# Import all dialog classes from the modular structure
from .dialogs import (
    ActionFallbackConfigDialog,
    SequencePropertiesDialog,
    WebSequencePropertiesDialog,
    AdvancedConditionalDialog,
    TriggerConfigDialog,
    OCRTriggerConfigDialog,
    WaitConditionDialog,
    ConditionalLoopDialog,
    LLMPropertiesDialog,
    TTSPropertiesDialog,
    AgentExportDialog,
    get_default_api_url
)

# Maintain backward compatibility by exposing all classes at module level
__all__ = [
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
    'get_default_api_url'
]

# All dialog classes are now imported from the modular structure
# This file serves as the main entry point for backward compatibility
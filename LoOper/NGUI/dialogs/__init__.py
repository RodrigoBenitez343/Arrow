# dialogs/__init__.py
"""Dialog classes, resolved on FIRST USE.

Importing this package used to import EVERY dialog, and one of them
(``code_node_dialog`` -> ``NGUI.nodes_resources``) pulls in NodeGraphQt.  That
made any small helper inside the package unusable from the agent side: the
standalone agent build excludes NodeGraphQt, so touching
``NGUI.dialogs.engine_options`` would drag the whole graph editor in and fail.

The classes stay reachable exactly as before (``from NGUI.dialogs import
LLMPropertiesDialog``), but they are imported when they are asked for
(PEP 562 module ``__getattr__``).  A caller that needs only a helper —
``engine_options`` is the common one — now loads only that module.
"""

import importlib

#: public name -> the module inside this package that defines it
_LAZY = {
    'ModernDialog': '.base_dialog',
    'ActionFallbackConfigDialog': '.fallback_dialogs',
    'SequencePropertiesDialog': '.sequence_dialogs',
    'WebSequencePropertiesDialog': '.web_sequence_dialogs',
    'AdvancedConditionalDialog': '.conditional_dialogs',
    'TriggerConfigDialog': '.trigger_dialogs',
    'OCRTriggerConfigDialog': '.trigger_dialogs',
    'WaitConditionDialog': '.trigger_dialogs',
    'ConditionalLoopDialog': '.trigger_dialogs',
    'LLMPropertiesDialog': '.llm_dialogs',
    'TTSPropertiesDialog': '.tts_dialogs',
    'AISettingsDialog': '.ai_settings_dialog',
    'ChainExpansionDialog': '.chain_expansion_dialog',
    'FormFillerDialog': '.form_filler_dialog',
    'CodeNodeDialog': '.code_node_dialog',
    'ContainerNodeDialog': '.container_dialog',
    'ContextDialog': '.context_dialog',
    'AgentExportDialog': '.agent_export_dialog',
    'get_default_api_url': '.utils',
}

__all__ = sorted(_LAZY)


def __getattr__(name):
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(
            'module %r has no attribute %r' % (__name__, name))
    module = importlib.import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value        # resolved once, then a normal attribute
    return value


def __dir__():
    return sorted(__all__)

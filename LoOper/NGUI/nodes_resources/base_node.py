import os
from NodeGraphQt import BaseNode

# Fallback: ensure a property registration method exists even if inline widget API differs
if not hasattr(BaseNode, 'create_property'):
    def _create_property_fallback(self, name, value):
        try:
            # Many NodeGraphQt versions allow creating properties via set_property
            self.set_property(name, value)
        except Exception:
            # Silently ignore to avoid breaking node creation
            pass
    BaseNode.create_property = _create_property_fallback

def get_default_api_url():
    """Get default API URL using same logic as consult.py"""
    port = os.getenv("API_PORT", "8000")
    if not port or not str(port).strip():
        port = "8000"
    return f"http://localhost:{port}"

def _add_multi_input(node, name, color, display_name, data_type=None, **kwargs):
    p = None
    try:
        p = node.add_input(name, color=color, display_name=True, multi_connection=True, **kwargs)
    except Exception:
        pass
    if not p:
        try:
            p = node.add_input(name, color=color, display_name=display_name, multi_input=True, **kwargs)
        except Exception:
            pass
    if not p:
        p = node.add_input(name, color=color, display_name=display_name, **kwargs)
        try:
            if hasattr(p, 'set_multi_connection'):
                p.set_multi_connection(True)
            else:
                port_item = getattr(p, '_port', None) or getattr(p, 'port', None)
                if port_item and hasattr(port_item, 'set_multi_connection'):
                    port_item.set_multi_connection(True)
                else:
                    setattr(p, '_multi_connection', True)
        except Exception:
            pass
            
    if p and data_type:
        try:
            # Do NOT overwrite model.type_ as it is used by NodeGraphQt for 'in'/'out' distinction
            # and overwriting it causes KeyError in PortConnectedCmd.redo
            # p.model.type_ = data_type
            pass
        except Exception:
            pass
            
    return p

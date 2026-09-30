"""Verify the standalone entry no longer pulls NGUI.dialogs / NodeGraphQt."""
import sys

sys.path.insert(0, r"d:\LoOperV2\LoOper")

import builder.agent_standalone_entry as entry  # noqa: E402

ngui_mods = sorted(m for m in sys.modules if m.startswith("NGUI"))
print("NGUI modules loaded:", ngui_mods)
assert not any(m.startswith("NGUI.dialogs") for m in ngui_mods), "dialogs must NOT be imported"
assert "NodeGraphQt" not in sys.modules, "NodeGraphQt must NOT be imported"
assert "NGUI.widgets.agent_overlay" in sys.modules
assert "NGUI.widgets.agent_dot" in sys.modules
assert "NGUI.widgets.agent_web_server" in sys.modules
print("IMPORT CHAIN OK")

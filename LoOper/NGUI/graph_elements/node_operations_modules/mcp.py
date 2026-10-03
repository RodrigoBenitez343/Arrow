import json
import logging
import os
import subprocess
import time

from PyQt5.QtWidgets import (
    QLineEdit, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QFileDialog, QPushButton, QHBoxLayout, QComboBox, QTextEdit,
    QMessageBox, QCheckBox, QSpinBox, QDoubleSpinBox,
    QScrollArea, QVBoxLayout, QWidget, QFrame, QGroupBox,
)
from PyQt5.QtCore import Qt, QTimer

from .utils import get_logger
from ...constants import WELL_BG, TEXT_COLOR

logger = get_logger(__name__)


def _find_mcp_entry(folder):
    """Find the MCP server entry point in a cloned repo folder.

    Returns dict with ``cmd`` (list) or None.
    """
    pkg_path = os.path.join(folder, "package.json")
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            main = pkg.get("main", "")
            if main:
                return {"cmd": ["node", main]}
            bin_field = pkg.get("bin", {})
            if isinstance(bin_field, str):
                return {"cmd": ["node", bin_field]}
            elif isinstance(bin_field, dict) and bin_field:
                first = next(iter(bin_field.values()))
                return {"cmd": ["node", first]}
        except Exception:
            pass

    for name in ["cli.js", "index.js", "main.js", "server.js"]:
        cand = os.path.join(folder, name)
        if os.path.isfile(cand):
            return {"cmd": ["node", name]}

    return {"cmd": ["npx", "."]}


def _discover_mcp_tools(folder):
    """Spawn the MCP server, do the init handshake, call tools/list, return tool schemas.

    Returns:
        dict: {"tools": [...], "error": "..."}
        - On success: tools is a list of tool schema dicts, error is None
        - On failure: tools is None, error is a human-readable message
    """
    # Quick pre-checks
    if not os.path.isdir(os.path.join(folder, "node_modules")):
        return {"tools": None, "error": (
            "node_modules not found.\n\n"
            "Run this in the MCP server folder first:\n"
            f"  cd {folder}\n"
            "  npm install"
        )}

    entry = _find_mcp_entry(folder)
    if not entry:
        return {"tools": None, "error": (
            "No MCP server entry point found.\n"
            "Expected one of: package.json (with 'main' or 'bin'), "
            "cli.js, index.js, main.js, or server.js"
        )}

    proc = None
    try:
        proc = subprocess.Popen(
            entry["cmd"],
            cwd=folder,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # Check if process died immediately
        import time
        time.sleep(0.3)
        ret = proc.poll()
        if ret is not None:
            stderr_out = proc.stderr.read()
            return {"tools": None, "error": (
                f"MCP server exited immediately (code {ret}).\n\n"
                f"Command: {' '.join(entry['cmd'])}\n"
                f"Folder: {folder}\n\n"
                f"Stderr:\n{stderr_out[:1000]}"
            )}

        # ── initialize ──
        init_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "LoOper", "version": "1.0"},
            },
        }) + "\n"
        proc.stdin.write(init_req)
        proc.stdin.flush()
        init_line = proc.stdout.readline()
        logger.debug("[MCP Discover] Init: %.200s", init_line)

        if not init_line:
            stderr_out = proc.stderr.read()
            return {"tools": None, "error": (
                f"MCP server did not respond to initialize.\n\n"
                f"Command: {' '.join(entry['cmd'])}\n"
                f"Stderr:\n{stderr_out[:1000]}"
            )}

        # ── initialized notification ──
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }) + "\n")
        proc.stdin.flush()

        # ── tools/list ──
        list_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }) + "\n"
        proc.stdin.write(list_req)
        proc.stdin.flush()

        # Read response lines until we get the tools/list result
        tools = []
        for _ in range(100):
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if "result" in parsed and isinstance(parsed["result"], dict):
                    tool_list = parsed["result"].get("tools", [])
                    for t in tool_list:
                        schema = t.get("inputSchema", {})
                        # Build empty args template from schema
                        args_template = {}
                        props = schema.get("properties", {})
                        for prop_name, prop_schema in props.items():
                            ptype = prop_schema.get("type", "string")
                            if ptype == "string":
                                args_template[prop_name] = ""
                            elif ptype in ("number", "integer"):
                                args_template[prop_name] = 0
                            elif ptype == "boolean":
                                args_template[prop_name] = False
                            elif ptype == "array":
                                args_template[prop_name] = []
                            elif ptype == "object":
                                args_template[prop_name] = {}
                            else:
                                args_template[prop_name] = ""
                        tools.append({
                            "name": t.get("name", ""),
                            "description": t.get("description", ""),
                            "inputSchema": schema,
                            "args_json": json.dumps(args_template, indent=2),
                        })
                    break
                elif "error" in parsed:
                    logger.warning("[MCP Discover] tools/list error: %s", parsed["error"])
                    return {"tools": None, "error": f"tools/list returned error: {json.dumps(parsed['error'])}"}
            except json.JSONDecodeError:
                continue

        if tools:
            return {"tools": tools, "error": None}
        else:
            stderr_out = proc.stderr.read()
            return {"tools": None, "error": (
                f"No tools discovered from server response.\n\n"
                f"Command: {' '.join(entry['cmd'])}\n"
                f"Stderr:\n{stderr_out[:1000]}"
            )}

    except FileNotFoundError:
        return {"tools": None, "error": (
            "'node' not found on PATH.\n\n"
            "Install Node.js (>=18) from https://nodejs.org"
        )}
    except Exception as e:
        logger.error("[MCP Discover] Error: %s", e, exc_info=True)
        return {"tools": None, "error": str(e)}
    finally:
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def _read_mcp_response(proc):
    """Read a single JSON-RPC response from the server stdout."""
    for _ in range(50):
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if "result" in parsed or "error" in parsed:
                return parsed
        except json.JSONDecodeError:
            continue
    return {"error": "No JSON-RPC response received"}


def _snapshot_mcp_page(folder, url):
    """Spawn a temp MCP server, navigate to *url*, call
    ``browser_snapshot``, and return the page text.

    Returns:
        dict: {"text": "...", "error": "..."}
    """
    if not url:
        return {"text": None, "error": "No URL provided."}
    if not os.path.isdir(folder):
        return {"text": None, "error": "MCP folder not found."}

    entry = _find_mcp_entry(folder)
    if not entry:
        return {"text": None, "error": "No MCP server entry found."}

    proc = None
    try:
        proc = subprocess.Popen(
            entry["cmd"],
            cwd=folder,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        time.sleep(0.3)
        ret = proc.poll()
        if ret is not None:
            stderr_out = proc.stderr.read()
            return {"text": None, "error": f"Server exited (code {ret}): {stderr_out[:500]}"}

        # initialize
        init_req = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "LoOper", "version": "1.0"},
            },
        }) + "\n"
        proc.stdin.write(init_req)
        proc.stdin.flush()
        init_line = proc.stdout.readline()
        if not init_line:
            return {"text": None, "error": "Server did not respond to initialize."}

        # initialized notification
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }) + "\n")
        proc.stdin.flush()

        # ── browser_navigate ──
        nav_req = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "browser_navigate", "arguments": {"url": url}},
        }) + "\n"
        proc.stdin.write(nav_req)
        proc.stdin.flush()
        nav_result = _read_mcp_response(proc)
        if nav_result.get("error"):
            return {"text": None, "error": f"Navigate failed: {nav_result['error']}"}

        # ── browser_snapshot ──
        snap_req = json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "browser_snapshot", "arguments": {}},
        }) + "\n"
        proc.stdin.write(snap_req)
        proc.stdin.flush()
        snap_result = _read_mcp_response(proc)
        if snap_result.get("error"):
            return {"text": None, "error": f"Snapshot failed: {snap_result['error']}"}

        content = snap_result.get("result", {})
        if isinstance(content, dict):
            content_list = content.get("content", [])
            texts = []
            for c in content_list:
                if isinstance(c, dict) and c.get("type") == "text":
                    texts.append(c.get("text", ""))
            return {"text": "\n".join(texts) if texts else json.dumps(content, indent=2),
                    "error": None}
        return {"text": str(content), "error": None}

    except Exception as e:
        logger.error("[MCP Snapshot] Error: %s", e, exc_info=True)
        return {"text": None, "error": str(e)}
    finally:
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


class MCPOperationsMixin:
    def add_mcp_node(self, pos=None):
        """Add a new MCP Server node."""
        logger.info(f"Adding MCP node at position: {pos}")
        try:
            node = self.parent_widget.graph_manager.create_node(
                'mcp.MCPNode',
                name='MCP Server',
                pos=pos
            )
            if node:
                logger.info(f"Successfully added MCP node {node.id}")
                return node
            else:
                logger.error("Failed to create MCP node")
        except Exception as e:
            logger.error(f"Error adding MCP node: {e}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to add MCP node: {str(e)}"
            )
        return None

    def edit_mcp_node(self, node):
        """Edit an MCP node's properties with auto-discovery and typed args form."""
        logger.info(f"Editing MCP node: {node.id}")
        try:
            current_folder = node.get_property('mcp_folder') or ''
            current_tool = node.get_property('tool_name') or ''
            current_args = node.get_property('tool_args') or '{}'
            # Try to load cached tools from node property
            cached_tools_json = node.get_property('mcp_tools') or ''
            discovered_tools = None
            if cached_tools_json:
                try:
                    discovered_tools = json.loads(cached_tools_json)
                except Exception:
                    discovered_tools = None

            # Parse current args for pre-filling form fields
            try:
                current_args_dict = json.loads(current_args) if current_args else {}
            except Exception:
                current_args_dict = {}

            # Mutable ref so closures and discovery callback share the same tools list
            tools_ref = {'tools': discovered_tools, 'args_dict': current_args_dict}

            from ...dialogs.base_dialog import ModernDialog
            dialog = ModernDialog(
                self.parent_widget, title="MCP Server Node", help_topic="mcp-node"
            )
            dialog.resize(580, 680)
            card = QGroupBox("MCP Server")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            dialog.content_layout.addWidget(card)

            # ── Folder row with Browse button ──
            folder_row = QHBoxLayout()
            folder_input = QLineEdit(dialog)
            folder_input.setText(current_folder)
            folder_input.setPlaceholderText(r"Select cloned MCP server folder...")
            folder_row.addWidget(folder_input, 1)

            browse_btn = QPushButton("Browse...", dialog)
            browse_btn.setCursor(Qt.PointingHandCursor)
            browse_btn.clicked.connect(lambda: self._browse_mcp_folder(folder_input))
            folder_row.addWidget(browse_btn)
            layout.addLayout(folder_row)

            # ── Discover button ──
            discover_btn = QPushButton("Discover Tools", dialog)
            discover_btn.setCursor(Qt.PointingHandCursor)
            discover_btn.setToolTip("Spawn the MCP server and list available tools")
            layout.addWidget(discover_btn)

            # ── Keep-alive toggle ──
            keep_alive_cb = QCheckBox("Keep server alive between chain iterations", dialog)
            keep_alive_cb.setChecked(node.get_property('keep_alive') or False)
            keep_alive_cb.setToolTip(
                "When checked, the MCP server stays running across multiple node executions\n"
                "in the same chain run (useful for loops). Uncheck to kill after each call."
            )
            layout.addWidget(keep_alive_cb)

            # ── Tool dropdown ──
            tool_row = QHBoxLayout()
            tool_row.addWidget(QLabel("Tool:", dialog))
            tool_combo = QComboBox(dialog)
            tool_combo.setMinimumWidth(260)
            tool_row.addWidget(tool_combo, 1)
            layout.addLayout(tool_row)

            # ── Description label ──
            desc_label = QLabel("", dialog)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet("color: #888; font-size: 12px; padding: 2px 0;")
            layout.addWidget(desc_label)

            # ── Dynamic args form (scrollable) ──
            args_scroll = QScrollArea(dialog)
            args_scroll.setWidgetResizable(True)
            args_scroll.setFrameShape(QFrame.NoFrame)
            args_scroll.setMinimumHeight(160)
            args_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

            args_form = QWidget()
            args_form_layout = QFormLayout(args_form)
            args_form_layout.setContentsMargins(0, 4, 0, 4)
            args_form_layout.setSpacing(6)
            field_widgets = {}  # prop_name → widget

            # Placeholder label shown when no tool is selected
            args_placeholder = QLabel("Select a tool from the dropdown above to configure arguments")
            args_placeholder.setStyleSheet("color: #888; font-style: italic; padding: 12px;")
            args_placeholder.setWordWrap(True)
            args_form_layout.addRow(args_placeholder)

            args_scroll.setWidget(args_form)
            layout.addWidget(args_scroll, 1)

            # ── Snapshot Page (URL + button) ──
            snapshot_row = QHBoxLayout()
            snapshot_url = QLineEdit(dialog)
            snapshot_url.setPlaceholderText("URL to snapshot (e.g. same as navigate target)")
            snapshot_row.addWidget(snapshot_url, 1)

            snapshot_btn = QPushButton("📷 Snapshot Page", dialog)
            snapshot_btn.setCursor(Qt.PointingHandCursor)
            snapshot_btn.setToolTip(
                "Spawn a temporary browser, navigate to the URL above,\n"
                "and show every visible text element on the page.\n"
                "Use this to discover the exact strings to use in\n"
                "text / textGone fields of browser_wait_for."
            )
            snapshot_row.addWidget(snapshot_btn)
            layout.addLayout(snapshot_row)

            # Snapshot result (read-only, hidden until first snapshot)
            snapshot_output = QTextEdit(dialog)
            snapshot_output.setReadOnly(True)
            snapshot_output.setMaximumHeight(140)
            snapshot_output.setPlaceholderText(
                "Page text will appear here after clicking 'Snapshot Page'"
            )
            snapshot_output.setStyleSheet(
                f"QTextEdit {{ background: {WELL_BG}; color: {TEXT_COLOR}; "
                f"border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; "
                f"font-family: Consolas; font-size: 12px; }}"
            )
            snapshot_output.hide()
            layout.addWidget(snapshot_output)

            def do_snapshot():
                folder = folder_input.text().strip()
                url = snapshot_url.text().strip()
                if not folder or not os.path.isdir(folder):
                    QMessageBox.warning(dialog, "Invalid Folder",
                                        "Please set a valid MCP server folder first.")
                    return
                if not url:
                    QMessageBox.warning(dialog, "No URL",
                                        "Please enter a URL to snapshot.")
                    return
                snapshot_btn.setEnabled(False)
                snapshot_btn.setText("Snapshotting...")
                snapshot_output.show()
                snapshot_output.setPlainText("Loading page and capturing snapshot...")
                QTimer.singleShot(100, lambda: _do_snapshot_work(
                    folder, url, snapshot_btn, snapshot_output))

            def _do_snapshot_work(folder, url, btn, output):
                try:
                    result = _snapshot_mcp_page(folder, url)
                    if result.get("error"):
                        output.setPlainText(f"Error: {result['error']}")
                    else:
                        output.setPlainText(result.get("text", "(empty snapshot)"))
                except Exception as e:
                    output.setPlainText(f"Snapshot error: {e}")
                finally:
                    btn.setEnabled(True)
                    btn.setText("📷 Snapshot Page")

            snapshot_btn.clicked.connect(do_snapshot)

            # ── Buttons ──
            buttons = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            dialog.content_layout.addWidget(buttons)

            # Populate tool combo if we have cached tools
            self._populate_tool_combo(tool_combo, tools_ref['tools'], current_tool)

            # ── Tool selection rebuilds the form ──
            def rebuild_form():
                idx = tool_combo.currentIndex()
                tools = tools_ref['tools']
                if tools and 0 <= idx < len(tools):
                    t = tools[idx]
                    desc_label.setText(t.get("description", ""))
                    self._build_args_form(args_form_layout, field_widgets, t, tools_ref['args_dict'])
                else:
                    desc_label.setText("")
                    self._clear_args_form(args_form_layout, field_widgets)
                # Force layout recalculation so the scroll area shows all fields
                args_form_layout.activate()
                args_form.updateGeometry()
                args_scroll.updateGeometry()

            tool_combo.currentIndexChanged.connect(lambda _idx: rebuild_form())

            # Trigger initial form build
            if tools_ref['tools'] and tool_combo.currentIndex() >= 0:
                rebuild_form()

            # ── Discover action ──
            def do_discover():
                folder = folder_input.text().strip()
                if not folder or not os.path.isdir(folder):
                    QMessageBox.warning(dialog, "Invalid Folder",
                                        "Please select a valid MCP server folder first.")
                    return
                discover_btn.setEnabled(False)
                discover_btn.setText("Discovering...")
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(100, lambda: self._run_discovery(
                    folder, tool_combo, node, discover_btn, tools_ref, rebuild_form))

            discover_btn.clicked.connect(do_discover)

            if dialog.exec_() != QDialog.Accepted:
                logger.debug("Dialog cancelled")
                return

            folder = folder_input.text().strip()
            node.set_property('mcp_folder', folder)
            node.set_property('keep_alive', keep_alive_cb.isChecked())

            # Collect args from form fields
            collected_args = self._collect_args_from_form(field_widgets)
            args_json_str = json.dumps(collected_args)

            # Save selected tool
            tools = tools_ref['tools']
            if tools and tool_combo.currentIndex() >= 0:
                idx = tool_combo.currentIndex()
                selected = tools[idx]
                node.set_property('tool_name', selected["name"])
                node.set_property('tool_args', args_json_str)
                node.set_name(f"MCP: {selected['name']}")
            else:
                node.set_property('tool_name', '')
                node.set_property('tool_args', args_json_str)
                node.set_name("MCP Server")

            # Persist discovered tools for next edit
            if tools:
                node.set_property('mcp_tools', json.dumps(tools))

            logger.info(f"Successfully edited MCP node {node.id}")

        except Exception as e:
            logger.error(f"Error editing MCP node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to edit MCP node: {str(e)}"
            )

    # ── Helpers ──

    def _browse_mcp_folder(self, line_edit):
        """Open a folder picker dialog and set the line edit."""
        folder = QFileDialog.getExistingDirectory(
            self.parent_widget,
            "Select MCP Server Folder",
            line_edit.text() or os.path.expanduser("~"),
        )
        if folder:
            line_edit.setText(folder)

    def _run_discovery(self, folder, tool_combo, node, discover_btn, tools_ref=None, rebuild_form=None):
        """Run MCP tool discovery and populate the combo box."""
        try:
            result = _discover_mcp_tools(folder)
            if result.get("error"):
                QMessageBox.warning(
                    self.parent_widget,
                    "Discovery Failed",
                    result["error"]
                )
                discover_btn.setEnabled(True)
                discover_btn.setText("Discover Tools")
                return

            tools = result.get("tools")
            if not tools:
                QMessageBox.warning(
                    self.parent_widget,
                    "No Tools Found",
                    "The MCP server responded but returned no tools."
                )
                discover_btn.setEnabled(True)
                discover_btn.setText("Discover Tools")
                return

            # Update the shared tools ref so the dialog sees the new tools
            if tools_ref is not None:
                tools_ref['tools'] = tools
                tools_ref['args_dict'] = {}  # reset args for fresh discovery

            self._populate_tool_combo(tool_combo, tools, "")
            # Persist
            node.set_property('mcp_tools', json.dumps(tools))
            logger.info("[MCP] Discovered %d tools from %s", len(tools), folder)

            # Trigger form rebuild for the newly selected tool
            if rebuild_form is not None:
                rebuild_form()

        except Exception as e:
            logger.error("[MCP] Discovery error: %s", e, exc_info=True)
            QMessageBox.critical(
                self.parent_widget,
                "Discovery Error",
                f"Failed to discover tools: {str(e)}"
            )
        finally:
            discover_btn.setEnabled(True)
            discover_btn.setText("Discover Tools")

    def _populate_tool_combo(self, combo, tools, current_name):
        """Fill the tool combo from discovered tools, selecting current_name if found."""
        combo.blockSignals(True)
        combo.clear()
        if not tools:
            combo.addItem("(no tools discovered)")
            combo.blockSignals(False)
            return

        select_idx = 0
        for i, t in enumerate(tools):
            label = t["name"]
            desc = t.get("description", "")
            if desc:
                label += f"  —  {desc[:60]}"
            combo.addItem(label)
            if t["name"] == current_name:
                select_idx = i

        combo.setCurrentIndex(select_idx)
        combo.blockSignals(False)

    # ── Dynamic typed-args form ──

    def _clear_args_form(self, form_layout, field_widgets):
        """Remove all widgets from the args form (QFormLayout-safe).

        Uses row-based removal to avoid the ``takeAt(0)`` hazard where
        QFormLayout may return ``None`` after items within a row are
        exhausted but the row itself persists.
        """
        field_widgets.clear()
        # Remove rows in reverse so index shifts don't matter
        for r in reversed(range(form_layout.rowCount())):
            item = form_layout.takeAt(r)
            if item is None:
                continue
            # A QFormLayout row can contain 1 (spanning) or 2 (label + field) items
            # Remove every sub-item so no widget is orphaned
            self._destroy_layout_item(item)

    def _destroy_layout_item(self, item):
        """Recursively destroy a layout item and all its widgets/sub-layouts."""
        if item is None:
            return
        w = item.widget()
        if w:
            w.deleteLater()
            return
        sub = item.layout()
        if sub:
            while sub.count():
                child = sub.takeAt(0)
                self._destroy_layout_item(child)

    def _build_args_form(self, form_layout, field_widgets, tool_schema, current_args_dict=None):
        """Build typed form fields from the tool's inputSchema."""
        self._clear_args_form(form_layout, field_widgets)
        if current_args_dict is None:
            current_args_dict = {}

        schema = tool_schema.get("inputSchema", {})
        props = schema.get("properties", {})
        required = schema.get("required", [])

        if not props:
            no_args = QLabel("(this tool takes no arguments)")
            no_args.setStyleSheet("color: #666; font-style: italic;")
            form_layout.addRow(no_args)
            return

        for prop_name, prop_schema in props.items():
            ptype = prop_schema.get("type", "string")
            description = prop_schema.get("description", "")
            default = prop_schema.get("default")
            enum_vals = prop_schema.get("enum")
            items_schema = prop_schema.get("items", {})

            # Determine if the schema uses anyOf/oneOf
            any_of = prop_schema.get("anyOf") or prop_schema.get("oneOf")

            # Label: name [+ required marker] [+ description tooltip]
            label_text = prop_name
            if prop_name in required:
                label_text += " *"
            label = QLabel(label_text)
            if description:
                label.setToolTip(description)

            # Determine current value
            cur_val = current_args_dict.get(prop_name)
            if cur_val is None and default is not None:
                cur_val = default

            widget = None

            if enum_vals and isinstance(enum_vals, list):
                # Enum → combo box
                widget = QComboBox()
                for ev in enum_vals:
                    widget.addItem(str(ev))
                if cur_val is not None:
                    idx = widget.findText(str(cur_val))
                    if idx >= 0:
                        widget.setCurrentIndex(idx)

            elif ptype == "boolean":
                widget = QCheckBox()
                if cur_val is not None:
                    widget.setChecked(bool(cur_val))
                else:
                    widget.setChecked(default if isinstance(default, bool) else False)

            elif ptype in ("number", "integer"):
                is_int = ptype == "integer"
                if is_int:
                    widget = QSpinBox()
                    widget.setRange(-999999, 999999)
                else:
                    widget = QDoubleSpinBox()
                    widget.setRange(-999999.0, 999999.0)
                    widget.setDecimals(4)
                if cur_val is not None:
                    try:
                        widget.setValue(float(cur_val) if not is_int else int(cur_val))
                    except (ValueError, TypeError):
                        pass
                elif default is not None:
                    try:
                        widget.setValue(float(default) if not is_int else int(default))
                    except (ValueError, TypeError):
                        pass

            elif ptype == "array":
                # Array → QLineEdit expecting JSON array string
                widget = QLineEdit()
                if cur_val is not None:
                    try:
                        widget.setText(json.dumps(cur_val) if isinstance(cur_val, (list, tuple)) else str(cur_val))
                    except Exception:
                        widget.setText(str(cur_val))
                elif default is not None:
                    try:
                        widget.setText(json.dumps(default) if isinstance(default, (list, tuple)) else str(default))
                    except Exception:
                        pass
                # Check if items have enum values → hint the format
                item_enum = items_schema.get("enum") if isinstance(items_schema, dict) else None
                hint = "JSON array"
                if item_enum:
                    hint += f" of: {', '.join(str(e) for e in item_enum)}"
                widget.setPlaceholderText(hint)

            elif any_of:
                # anyOf/oneOf → QLineEdit for manual JSON input
                widget = QLineEdit()
                if cur_val is not None:
                    try:
                        widget.setText(json.dumps(cur_val) if not isinstance(cur_val, str) else cur_val)
                    except Exception:
                        widget.setText(str(cur_val))
                elif default is not None:
                    try:
                        widget.setText(json.dumps(default) if not isinstance(default, str) else str(default))
                    except Exception:
                        pass
                widget.setPlaceholderText("JSON value (any type)")

            elif ptype == "object":
                # Object → QLineEdit expecting JSON object string
                widget = QLineEdit()
                if cur_val is not None:
                    try:
                        widget.setText(json.dumps(cur_val) if isinstance(cur_val, dict) else str(cur_val))
                    except Exception:
                        widget.setText(str(cur_val))
                elif default is not None:
                    try:
                        widget.setText(json.dumps(default) if isinstance(default, dict) else str(default))
                    except Exception:
                        pass
                widget.setPlaceholderText("JSON object { ... }")

            else:
                # Default: string → QLineEdit
                widget = QLineEdit()
                if cur_val is not None:
                    widget.setText(str(cur_val))
                elif default is not None:
                    widget.setText(str(default))
                if description:
                    widget.setPlaceholderText(description[:80])

            if widget:
                # Store the JSON Schema type on the widget so
                # _collect_args_from_form can correctly parse array/object JSON text
                try:
                    widget.setProperty('mcp_ptype', ptype)
                except Exception:
                    pass
                field_widgets[prop_name] = widget
                form_layout.addRow(label, widget)

    def _collect_args_from_form(self, field_widgets):
        """Read values from all form fields and return a dict.

        For array/object types the QLineEdit text is parsed as JSON
        so the downstream ``json.loads(tool_args)`` round-trip yields
        the correct Python type.
        """
        result = {}
        for prop_name, widget in field_widgets.items():
            # Retrieve stored JSON Schema type to handle array/object parsing
            try:
                mcp_ptype = widget.property('mcp_ptype') or 'string'
            except Exception:
                mcp_ptype = 'string'

            if isinstance(widget, QComboBox):
                val = widget.currentText()
                # Try to parse as number if it looks like one
                try:
                    val = int(val)
                except ValueError:
                    try:
                        val = float(val)
                    except ValueError:
                        pass
                result[prop_name] = val
            elif isinstance(widget, QCheckBox):
                result[prop_name] = widget.isChecked()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                result[prop_name] = widget.value()
            elif isinstance(widget, QLineEdit):
                raw = widget.text()
                if mcp_ptype in ('array', 'object'):
                    try:
                        result[prop_name] = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        result[prop_name] = raw
                else:
                    result[prop_name] = raw
        return result

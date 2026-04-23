from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QCheckBox, QPushButton, QTabWidget, QWidget, 
    QSpinBox, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QRadioButton, QButtonGroup, QTextEdit,
    QAbstractItemView, QAbstractButton, QGroupBox
)
from PyQt5.QtCore import Qt
from .base_dialog import ModernDialog
from ..constants import TEXT_COLOR, LIGHT_GREY, ACCENT_COLOR
from ..i18n import _
import json
import datetime


class ContextDialog(ModernDialog):
    """Dialog for configuring Context Node properties."""
    
    def __init__(self, parent=None, config=None, node_id=None):
        super().__init__(parent, title=_("Context Node Configuration"), help_topic="context-node-dialog")
        self.resize(750, 550)
        
        self.config = config or {}
        self.node_id = node_id
        
        # Setup Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {LIGHT_GREY};
                background: transparent;
            }}
            QTabBar::tab {{
                background: transparent;
                color: {TEXT_COLOR};
                padding: 8px 12px;
                border: 1px solid transparent;
            }}
            QTabBar::tab:selected {{
                border-bottom: 2px solid {ACCENT_COLOR};
                font-weight: bold;
            }}
        """)
        
        # Tab 1: Keys and Merge Strategy
        self.keys_tab = QWidget()
        self.setup_keys_tab()
        self.tabs.addTab(self.keys_tab, _("Keys & Merge"))
        
        # Tab 2: Scope and Retention
        self.scope_tab = QWidget()
        self.setup_scope_tab()
        self.tabs.addTab(self.scope_tab, _("Scope & Retention"))
        
        # Tab 3: Output Format
        self.output_tab = QWidget()
        self.setup_output_tab()
        self.tabs.addTab(self.output_tab, _("Output Format"))
        
        # Tab 4: Current Contents (read-only inspector)
        self.contents_tab = QWidget()
        self.setup_contents_tab()
        self.tabs.addTab(self.contents_tab, _("Current Contents"))
        
        self.content_layout.addWidget(self.tabs)
        self.add_buttons()
        
    def add_buttons(self):
        """Add Cancel and Save buttons at the bottom."""
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setStyleSheet(self.get_button_style())
        
        save_btn = QPushButton(_("Save"))
        save_btn.clicked.connect(self.accept)
        save_btn.setStyleSheet(self.get_button_style().replace(LIGHT_GREY, ACCENT_COLOR).replace(TEXT_COLOR, "#121212"))
        
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(save_btn)
        
        self.content_layout.addLayout(button_layout)
        
    def setup_keys_tab(self):
        """Setup the Keys and Merge Strategy tab."""
        layout = QVBoxLayout(self.keys_tab)
        layout.setSpacing(15)
        
        # Keys Table Section
        table_label = QLabel(_("Context Keys Configuration:"))
        table_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        layout.addWidget(table_label)
        
        # Table for keys
        self.keys_table = QTableWidget()
        self.keys_table.setColumnCount(3)
        self.keys_table.setHorizontalHeaderLabels([_("Key Name"), _("Merge Policy"), _("Max Entries")])
        
        # Hide row numbers and corner widget
        self.keys_table.verticalHeader().setVisible(False)
        self.keys_table.setCornerButtonEnabled(False)
        
        # Set column widths - fixed widths for policy and entries, stretch for key name
        header = self.keys_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.keys_table.setColumnWidth(1, 130)
        self.keys_table.setColumnWidth(2, 100)
        
        self.keys_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.keys_table.setAlternatingRowColors(True)
        self.keys_table.setStyleSheet(f"""
            QTableWidget {{
                background-color: #252526;
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                gridline-color: {LIGHT_GREY};
            }}
            QTableWidget::item {{
                padding: 5px;
            }}
            QTableWidget::item:selected {{
                background-color: {ACCENT_COLOR};
                color: #121212;
            }}
            QHeaderView::section {{
                background-color: #2d2d2d;
                color: {TEXT_COLOR};
                padding: 5px;
                border: 1px solid {LIGHT_GREY};
            }}
        """)
        layout.addWidget(self.keys_table)
        
        # Style the corner widget to be transparent (fixes white square in top-left)
        corner_btn = self.keys_table.findChild(QAbstractButton)
        if corner_btn:
            corner_btn.setStyleSheet("background: transparent; border: none;")
        
        # Add/Remove buttons
        btn_layout = QHBoxLayout()
        add_key_btn = QPushButton(_("Add Key"))
        add_key_btn.setStyleSheet(self.get_button_style())
        add_key_btn.clicked.connect(self.add_key_row)
        
        remove_key_btn = QPushButton(_("Remove Key"))
        remove_key_btn.setStyleSheet(self.get_button_style())
        remove_key_btn.clicked.connect(self.remove_key_row)
        
        btn_layout.addWidget(add_key_btn)
        btn_layout.addWidget(remove_key_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        # Default Merge Policy
        policy_layout = QHBoxLayout()
        policy_label = QLabel(_("Default Merge Policy:"))
        policy_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        
        self.default_policy_combo = QComboBox()
        self.default_policy_combo.addItems(["append", "replace", "merge_dict"])
        self.default_policy_combo.setStyleSheet(self.get_input_style())
        
        # Load from config
        default_policy = self.config.get('default_merge_policy', 'append')
        idx = self.default_policy_combo.findText(default_policy)
        if idx >= 0:
            self.default_policy_combo.setCurrentIndex(idx)
            
        policy_layout.addWidget(policy_label)
        policy_layout.addWidget(self.default_policy_combo)
        policy_layout.addStretch()
        layout.addLayout(policy_layout)
        
        # Populate table from config
        self.populate_keys_table()
        
    def populate_keys_table(self):
        """Populate the keys table from config."""
        keys_json = self.config.get('keys', '[]')
        policies_json = self.config.get('key_policies', '{}')
        
        try:
            keys_list = json.loads(keys_json) if keys_json else []
        except (json.JSONDecodeError, TypeError):
            keys_list = []
            
        try:
            policies_dict = json.loads(policies_json) if policies_json else {}
        except (json.JSONDecodeError, TypeError):
            policies_dict = {}
        
        self.keys_table.setRowCount(len(keys_list))
        
        for row, key_name in enumerate(keys_list):
            # Key Name (editable)
            key_item = QTableWidgetItem(key_name)
            key_item.setFlags(key_item.flags() | Qt.ItemIsEditable)
            self.keys_table.setItem(row, 0, key_item)
            
            # Merge Policy (combo box)
            policy_combo = QComboBox()
            policy_combo.addItems(["append", "replace", "merge_dict"])
            policy_combo.setStyleSheet(self.get_input_style())
            
            # Get policy for this key
            key_policy = policies_dict.get(key_name, {}).get('merge_policy', 'append')
            policy_idx = policy_combo.findText(key_policy)
            if policy_idx >= 0:
                policy_combo.setCurrentIndex(policy_idx)
            self.keys_table.setCellWidget(row, 1, policy_combo)
            
            # Max Entries (spin box)
            max_entries_spin = QSpinBox()
            max_entries_spin.setRange(1, 10000)
            max_entries_spin.setValue(policies_dict.get(key_name, {}).get('max_entries', 100))
            max_entries_spin.setStyleSheet(self.get_input_style())
            self.keys_table.setCellWidget(row, 2, max_entries_spin)
            
    def add_key_row(self):
        """Add a new row to the keys table."""
        row = self.keys_table.rowCount()
        self.keys_table.insertRow(row)
        
        # Key Name (editable)
        key_item = QTableWidgetItem(_("new_key"))
        key_item.setFlags(key_item.flags() | Qt.ItemIsEditable)
        self.keys_table.setItem(row, 0, key_item)
        
        # Merge Policy (combo box)
        policy_combo = QComboBox()
        policy_combo.addItems(["append", "replace", "merge_dict"])
        policy_combo.setStyleSheet(self.get_input_style())
        self.keys_table.setCellWidget(row, 1, policy_combo)
        
        # Max Entries (spin box)
        max_entries_spin = QSpinBox()
        max_entries_spin.setRange(1, 10000)
        max_entries_spin.setValue(100)
        max_entries_spin.setStyleSheet(self.get_input_style())
        self.keys_table.setCellWidget(row, 2, max_entries_spin)
        
        # Focus on the new row
        self.keys_table.selectRow(row)
        self.keys_table.editItem(key_item)
        
    def remove_key_row(self):
        """Remove the selected row from the keys table."""
        selected_rows = self.keys_table.selectionModel().selectedRows()
        if selected_rows:
            row = selected_rows[0].row()
            self.keys_table.removeRow(row)
            
    def setup_scope_tab(self):
        """Setup the Scope and Retention tab."""
        layout = QVBoxLayout(self.scope_tab)
        layout.setSpacing(15)
        
        # Scope selection
        scope_label = QLabel(_("Context Scope:"))
        scope_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        layout.addWidget(scope_label)
        
        self.scope_button_group = QButtonGroup(self)
        
        # Run scope
        run_radio = QRadioButton(_("Run"))
        run_radio.setStyleSheet(f"color: {TEXT_COLOR};")
        self.scope_button_group.addButton(run_radio, 0)
        run_desc = QLabel(_("Data is cleared when the chain execution finishes"))
        run_desc.setStyleSheet(f"color: {TEXT_COLOR}; font-style: italic; margin-left: 20px;")
        run_desc.setWordWrap(True)
        
        # Chain scope
        chain_radio = QRadioButton(_("Chain"))
        chain_radio.setStyleSheet(f"color: {TEXT_COLOR};")
        self.scope_button_group.addButton(chain_radio, 1)
        chain_desc = QLabel(_("Data persists across runs of this chain"))
        chain_desc.setStyleSheet(f"color: {TEXT_COLOR}; font-style: italic; margin-left: 20px;")
        chain_desc.setWordWrap(True)
        
        # Global scope
        global_radio = QRadioButton(_("Global"))
        global_radio.setStyleSheet(f"color: {TEXT_COLOR};")
        self.scope_button_group.addButton(global_radio, 2)
        global_desc = QLabel(_("Data is shared across all chains"))
        global_desc.setStyleSheet(f"color: {TEXT_COLOR}; font-style: italic; margin-left: 20px;")
        global_desc.setWordWrap(True)
        
        # Set current scope from config
        current_scope = self.config.get('scope', 'chain').lower()
        if current_scope == 'run':
            run_radio.setChecked(True)
        elif current_scope == 'chain':
            chain_radio.setChecked(True)
        elif current_scope == 'global':
            global_radio.setChecked(True)
        else:
            chain_radio.setChecked(True)  # Default
            
        layout.addWidget(run_radio)
        layout.addWidget(run_desc)
        layout.addWidget(chain_radio)
        layout.addWidget(chain_desc)
        layout.addWidget(global_radio)
        layout.addWidget(global_desc)
        
        layout.addSpacing(20)
        
        # Max Entries per Key
        entries_layout = QHBoxLayout()
        entries_label = QLabel(_("Max Entries per Key:"))
        entries_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        
        self.max_entries_spin = QSpinBox()
        self.max_entries_spin.setRange(1, 10000)
        self.max_entries_spin.setValue(int(self.config.get('max_entries_per_key', 100)))
        self.max_entries_spin.setStyleSheet(self.get_input_style())
        
        entries_info = QLabel(_("Maximum number of entries stored per context key"))
        entries_info.setStyleSheet(f"color: {TEXT_COLOR}; font-style: italic;")
        
        entries_layout.addWidget(entries_label)
        entries_layout.addWidget(self.max_entries_spin)
        entries_layout.addWidget(entries_info)
        entries_layout.addStretch()
        layout.addLayout(entries_layout)
        
        layout.addSpacing(20)
        
        # Retrieval Settings Group
        retrieval_group = QGroupBox(_("Retrieval Settings"))
        retrieval_group.setStyleSheet(f"""
            QGroupBox {{
                color: {TEXT_COLOR};
                font-weight: bold;
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }}
        """)
        retrieval_layout = QVBoxLayout(retrieval_group)
        retrieval_layout.setSpacing(10)
        
        # Enable semantic retrieval checkbox
        self.semantic_retrieval_checkbox = QCheckBox(_("Enable semantic retrieval"))
        self.semantic_retrieval_checkbox.setStyleSheet(f"color: {TEXT_COLOR};")
        self.semantic_retrieval_checkbox.setChecked(self.config.get('use_semantic_retrieval', False))
        retrieval_layout.addWidget(self.semantic_retrieval_checkbox)
        
        # Explanation label
        semantic_desc = QLabel(_("When enabled, the Context node uses the downstream LLM's prompt to retrieve only the most relevant stored entries instead of returning everything."))
        semantic_desc.setStyleSheet(f"color: {TEXT_COLOR}; font-style: italic; margin-left: 20px;")
        semantic_desc.setWordWrap(True)
        retrieval_layout.addWidget(semantic_desc)
        
        # Top-K results spinbox
        topk_layout = QHBoxLayout()
        topk_label = QLabel(_("Top-K results:"))
        topk_label.setStyleSheet(f"color: {TEXT_COLOR};")
        
        self.retrieval_top_k_spin = QSpinBox()
        self.retrieval_top_k_spin.setRange(1, 20)
        self.retrieval_top_k_spin.setValue(int(self.config.get('retrieval_top_k', 3)))
        self.retrieval_top_k_spin.setStyleSheet(self.get_input_style())
        
        topk_layout.addWidget(topk_label)
        topk_layout.addWidget(self.retrieval_top_k_spin)
        topk_layout.addStretch()
        retrieval_layout.addLayout(topk_layout)
        
        layout.addWidget(retrieval_group)
        layout.addStretch()
        
    def setup_output_tab(self):
        """Setup the Output Format tab."""
        layout = QVBoxLayout(self.output_tab)
        layout.setSpacing(15)
        
        # Output Format selection
        format_label = QLabel(_("Output Format:"))
        format_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        layout.addWidget(format_label)
        
        self.format_button_group = QButtonGroup(self)
        
        # Structured JSON
        structured_radio = QRadioButton(_("Structured JSON"))
        structured_radio.setStyleSheet(f"color: {TEXT_COLOR};")
        self.format_button_group.addButton(structured_radio, 0)
        structured_desc = QLabel(_("Output context as a structured JSON object with keys"))
        structured_desc.setStyleSheet(f"color: {TEXT_COLOR}; font-style: italic; margin-left: 20px;")
        structured_desc.setWordWrap(True)
        
        # Narrative prose
        narrative_radio = QRadioButton(_("Narrative prose"))
        narrative_radio.setStyleSheet(f"color: {TEXT_COLOR};")
        self.format_button_group.addButton(narrative_radio, 1)
        narrative_desc = QLabel(_("Output context as narrative text suitable for LLM prompts"))
        narrative_desc.setStyleSheet(f"color: {TEXT_COLOR}; font-style: italic; margin-left: 20px;")
        narrative_desc.setWordWrap(True)
        
        # Set current format from config
        current_format = self.config.get('output_format', 'structured').lower()
        if current_format == 'structured':
            structured_radio.setChecked(True)
        elif current_format == 'narrative':
            narrative_radio.setChecked(True)
        else:
            structured_radio.setChecked(True)  # Default
            
        layout.addWidget(structured_radio)
        layout.addWidget(structured_desc)
        layout.addWidget(narrative_radio)
        layout.addWidget(narrative_desc)
        
        layout.addSpacing(20)
        
        # Auto-capture responses
        capture_layout = QHBoxLayout()
        self.auto_capture_check = QCheckBox(_("Auto-capture upstream LLM responses"))
        self.auto_capture_check.setStyleSheet(f"color: {TEXT_COLOR};")
        
        # Load from config
        auto_capture_val = self.config.get('auto_capture_responses', 'true')
        if isinstance(auto_capture_val, str):
            self.auto_capture_check.setChecked(auto_capture_val.lower() in ('true', '1', 'yes', 'on'))
        else:
            self.auto_capture_check.setChecked(bool(auto_capture_val))
            
        capture_layout.addWidget(self.auto_capture_check)
        capture_layout.addStretch()
        layout.addLayout(capture_layout)
        
        capture_desc = QLabel(_("Automatically capture responses from upstream LLM nodes"))
        capture_desc.setStyleSheet(f"color: {TEXT_COLOR}; font-style: italic; margin-left: 20px;")
        layout.addWidget(capture_desc)
        
        layout.addSpacing(20)
        
        # Context Label
        label_layout = QVBoxLayout()
        context_label = QLabel(_("Context Label:"))
        context_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        
        self.context_label_edit = QLineEdit()
        self.context_label_edit.setText(self.config.get('context_node_label', ''))
        self.context_label_edit.setPlaceholderText(_("Optional label to identify this context node"))
        self.context_label_edit.setStyleSheet(self.get_input_style())
        
        label_info = QLabel(_("A descriptive label for this context node (optional)"))
        label_info.setStyleSheet(f"color: {TEXT_COLOR}; font-style: italic;")
        
        label_layout.addWidget(context_label)
        label_layout.addWidget(self.context_label_edit)
        label_layout.addWidget(label_info)
        layout.addLayout(label_layout)
        
        layout.addStretch()
        
    def setup_contents_tab(self):
        """Setup the Current Contents (read-only inspector) tab."""
        layout = QVBoxLayout(self.contents_tab)
        layout.setSpacing(10)
        
        # Info label
        info_label = QLabel(_("Context Contents Inspector"))
        info_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        layout.addWidget(info_label)
        
        # Read-only text display
        self.contents_display = QTextEdit()
        self.contents_display.setReadOnly(True)
        self.contents_display.setStyleSheet(f"""
            QTextEdit {{
                background-color: #1E1E1E;
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 10px;
            }}
        """)
        self.contents_display.setPlaceholderText(_("Context contents will be displayed here during chain execution."))
        layout.addWidget(self.contents_display)
        
        # Refresh button
        refresh_btn = QPushButton(_("Refresh"))
        refresh_btn.setStyleSheet(self.get_button_style())
        refresh_btn.clicked.connect(self._refresh_contents)
        layout.addWidget(refresh_btn)
        
        # Load initial data
        self._refresh_contents()
        
    def _refresh_contents(self):
        """Query the database and display stored context entries."""
        if not self.node_id:
            self.contents_display.setPlainText(_("No node ID available. Save the chain first."))
            return
        
        try:
            from LoOper.AI.context_database import ContextDatabase
            db = ContextDatabase()
            
            # Determine scope from current config
            scope = self.config.get('scope', 'chain') if self.config else 'chain'
            
            # Use query() to fetch entries by scope, then filter by node_id
            # This works regardless of whether we know scope_id at dialog time
            rows = db.query(scope=scope)
            node_rows = [r for r in rows if r.get('node_id') == self.node_id]
            
            if node_rows:
                # Group by key
                keys_data = {}
                for row in node_rows:
                    key = row.get('key', 'unknown')
                    if key not in keys_data:
                        keys_data[key] = []
                    keys_data[key].append(row)
                
                lines = []
                for key, entries in keys_data.items():
                    lines.append(f"=== Key: {key} ===")
                    for i, entry in enumerate(entries):
                        lines.append(f"\n  Entry {i+1}:")
                        val = entry.get('value')
                        if isinstance(val, dict):
                            for k, v in val.items():
                                lines.append(f"    {k}: {v}")
                        elif isinstance(val, list):
                            for j, item in enumerate(val):
                                lines.append(f"    [{j}]: {item}")
                        else:
                            lines.append(f"    {val}")
                        # Show metadata if present
                        if entry.get('source_type'):
                            lines.append(f"    source: {entry['source_type']}")
                        if entry.get('updated_at'):
                            ts = datetime.datetime.fromtimestamp(entry['updated_at']).strftime('%Y-%m-%d %H:%M:%S')
                            lines.append(f"    updated: {ts}")
                    lines.append("")
                
                self.contents_display.setPlainText("\n".join(lines))
            else:
                self.contents_display.setPlainText(_("No context data stored yet.\n\nRun the chain to start accumulating context."))
            
            db.close()
        except Exception as e:
            self.contents_display.setPlainText(f"Error loading context data: {e}")
        
    def get_input_style(self):
        """Return the standard input field style."""
        return f"""
            QLineEdit, QSpinBox, QComboBox {{
                background-color: #252526;
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                padding: 5px;
                border-radius: 4px;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background-color: {LIGHT_GREY};
                border: none;
            }}
            QComboBox::drop-down {{
                border: none;
                background-color: {LIGHT_GREY};
                width: 20px;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
            }}
        """
        
    def get_button_style(self):
        """Return the standard button style."""
        return f"""
            QPushButton {{
                background-color: {LIGHT_GREY};
                color: {TEXT_COLOR};
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #3E3E3E;
            }}
        """
        
    def get_config(self):
        """Return the configuration as a dictionary."""
        # Build keys list from table
        keys_list = []
        policies_dict = {}
        
        for row in range(self.keys_table.rowCount()):
            key_item = self.keys_table.item(row, 0)
            if key_item:
                key_name = key_item.text().strip()
                if key_name:
                    keys_list.append(key_name)
                    
                    # Get policy for this key
                    policy_widget = self.keys_table.cellWidget(row, 1)
                    max_entries_widget = self.keys_table.cellWidget(row, 2)
                    
                    policies_dict[key_name] = {
                        'merge_policy': policy_widget.currentText() if policy_widget else 'append',
                        'max_entries': max_entries_widget.value() if max_entries_widget else 100
                    }
        
        # Get scope
        scope_checked = self.scope_button_group.checkedId()
        scope = 'run' if scope_checked == 0 else 'chain' if scope_checked == 1 else 'global'
        
        # Get output format
        format_checked = self.format_button_group.checkedId()
        output_format = 'structured' if format_checked == 0 else 'narrative'
        
        return {
            'scope': scope,
            'default_merge_policy': self.default_policy_combo.currentText(),
            'keys': json.dumps(keys_list),
            'key_policies': json.dumps(policies_dict),
            'max_entries_per_key': str(self.max_entries_spin.value()),
            'auto_capture_responses': 'true' if self.auto_capture_check.isChecked() else 'false',
            'output_format': output_format,
            'context_node_label': self.context_label_edit.text(),
            'use_semantic_retrieval': self.semantic_retrieval_checkbox.isChecked(),
            'retrieval_top_k': self.retrieval_top_k_spin.value(),
            'shared_context_chain_file': self.config.get('shared_context_chain_file', ''),  # Preserved from drag-drop
            'shared_context_node_id': self.config.get('shared_context_node_id', ''),  # Preserved from drag-drop
        }

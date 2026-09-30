# dialogs/chain_import_dialogs.py

from .base_dialog import ModernDialog
from .toggle_switch import ModernToggle
import os
from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
                             QDialogButtonBox, QPushButton, QFileDialog, QComboBox, 
                             QTextEdit, QGroupBox, QMessageBox, QListWidget,
                             QListWidgetItem)
from PyQt5.QtCore import Qt
from ..constants import TEXT_COLOR
from ..i18n import _


from .toggle_switch import ModernToggle

class ChainImportDialog(ModernDialog):
    """Dialog for configuring chain import settings"""
    
    def __init__(self, chain_import_config=None, parent=None):
        super().__init__(parent, title=_("Chain Import Configuration"), help_topic="chain-import-dialog")
        self.setModal(True)
        self.resize(620, 620)
        self.setMinimumSize(600, 520)
        
        # Style logic moved to ModernDialog
        
        # Initialize with default or provided config
        self.config = chain_import_config or {
            'chain_file': '',
            'import_mode': 'full',
            'prefix': '',
            'loop_count': 1,
            'extra_delay': 0,
            'enabled': True,
            'emit_data': False,
            'data_output_nodes': []
        }
        
        self.setup_ui()
        self.load_config()
        
    def setup_ui(self):
        """Setup the user interface"""
        # Use content_layout from ModernDialog
        layout = self.content_layout
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # Chain File Selection Group
        file_group = QGroupBox(_("Chain File Selection"))
        file_layout = QVBoxLayout(file_group)
        file_layout.setContentsMargins(10, 15, 10, 10)
        file_layout.setSpacing(8)
        
        # Chain file path
        file_row = QHBoxLayout()
        self.chain_file_label = QLabel(_("Chain File:"))
        self.chain_file_input = QLineEdit()
        self.chain_file_input.setReadOnly(True)
        self.browse_button = QPushButton(_("Browse..."))
        self.browse_button.clicked.connect(self.browse_chain_file)
        
        file_row.addWidget(self.chain_file_label)
        file_row.addWidget(self.chain_file_input, 1)
        file_row.addWidget(self.browse_button)
        file_layout.addLayout(file_row)
        
        # Chain info display
        self.chain_info = QTextEdit()
        self.chain_info.setMaximumHeight(100)
        self.chain_info.setReadOnly(True)
        file_layout.addWidget(QLabel(_("Chain Information:")))
        file_layout.addWidget(self.chain_info)
        
        layout.addWidget(file_group)
        
        # Import Configuration Group
        config_group = QGroupBox(_("Import Configuration"))
        config_layout = QVBoxLayout(config_group)
        config_layout.setContentsMargins(10, 15, 10, 10)
        config_layout.setSpacing(8)
        
        # Import mode
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(_("Import Mode:")))
        self.import_mode_combo = QComboBox()
        self.import_mode_combo.addItems(['full', 'sequences_only', 'nodes_only'])
        self.import_mode_combo.setToolTip(
            _("full: Import all sequences and nodes") + "\n" +
            _("sequences_only: Import only sequence nodes") + "\n" +
            _("nodes_only: Import only conditional, LLM, and TTS nodes")
        )
        mode_row.addWidget(self.import_mode_combo)
        mode_row.addStretch()
        config_layout.addLayout(mode_row)
        
        # Node prefix
        prefix_row = QHBoxLayout()
        prefix_row.addWidget(QLabel(_("Node Prefix:")))
        self.prefix_input = QLineEdit()
        self.prefix_input.setPlaceholderText(_("Optional prefix for imported node names"))
        prefix_row.addWidget(self.prefix_input)
        config_layout.addLayout(prefix_row)
        
        # Loop count
        loop_row = QHBoxLayout()
        loop_row.addWidget(QLabel(_("Loop Count:")))
        self.loop_count_input = QLineEdit()
        self.loop_count_input.setPlaceholderText("1")
        self.loop_count_input.setToolTip(_("Number of times to execute the imported chain"))
        loop_row.addWidget(self.loop_count_input)
        loop_row.addStretch()
        config_layout.addLayout(loop_row)
        
        # Extra delay
        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel(_("Extra Delay (s):")))
        self.extra_delay_input = QLineEdit()
        self.extra_delay_input.setPlaceholderText("0")
        self.extra_delay_input.setToolTip(_("Extra delay between loop iterations in seconds"))
        delay_row.addWidget(self.extra_delay_input)
        delay_row.addStretch()
        config_layout.addLayout(delay_row)
        
        # Enabled checkbox
        self.enabled_checkbox = ModernToggle(text=_("Enable this chain import"))
        self.enabled_checkbox.setChecked(True)
        config_layout.addWidget(self.enabled_checkbox)
        
        layout.addWidget(config_group)
        
        # Output content sent to the outer connection (the tool consumer /
        # agent context).  No ports are involved: the selected Output nodes'
        # content is delivered directly.
        data_group = QGroupBox(_("Output content for the outer connection"))
        data_layout = QVBoxLayout(data_group)
        data_layout.setContentsMargins(10, 15, 10, 10)
        data_layout.setSpacing(8)

        self.emit_data_toggle = ModernToggle(
            text=_("Send only the selected Output nodes' content"))
        self.emit_data_toggle.setToolTip(
            _("When enabled, only the Output nodes checked below have their "
              "content delivered to the outer connection (the tool consumer / "
              "agent context).  When disabled, every Output node's content is "
              "delivered.")
        )
        data_layout.addWidget(self.emit_data_toggle)

        data_layout.addWidget(QLabel(_("Output nodes to send (none checked = all):")))
        self.data_outputs_list = QListWidget()
        self.data_outputs_list.setMaximumHeight(120)
        self.data_outputs_list.setEnabled(self.emit_data_toggle.isChecked())
        self.emit_data_toggle.toggled.connect(self.data_outputs_list.setEnabled)
        data_layout.addWidget(self.data_outputs_list)

        self.data_warning_label = QLabel("")
        self.data_warning_label.setWordWrap(True)
        data_layout.addWidget(self.data_warning_label)

        layout.addWidget(data_group)
        
        # Sandbox execution options
        sandbox_group = QGroupBox(_("Sandbox Execution"))
        sandbox_layout = QVBoxLayout()
        
        # Run in Sandbox Toggle
        self.sandbox_toggle = ModernToggle(text=_("Run chain inside Windows Sandbox"))
        self.sandbox_toggle.setChecked(self.config.get('run_in_sandbox', False))
        sandbox_layout.addWidget(self.sandbox_toggle)
        
        # Show Sandbox Window Toggle
        self.show_sandbox_window_toggle = ModernToggle(text=_("Show Sandbox Window (for debugging)"))
        self.show_sandbox_window_toggle.setChecked(self.config.get('show_sandbox_window', True))
        # Disable if sandbox is not enabled
        self.show_sandbox_window_toggle.setEnabled(self.sandbox_toggle.isChecked())
        
        self.sandbox_toggle.toggled.connect(self.show_sandbox_window_toggle.setEnabled)
        
        sandbox_layout.addWidget(self.show_sandbox_window_toggle)
        sandbox_group.setLayout(sandbox_layout)
        layout.addWidget(sandbox_group)
        
        # Button box
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = self.button_box.button(QDialogButtonBox.Ok)
        cancel_btn = self.button_box.button(QDialogButtonBox.Cancel)
        if ok_btn:
            ok_btn.setText(_("OK"))
        if cancel_btn:
            cancel_btn.setText(_("Cancel"))
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)
        
        # Connect signals
        self.chain_file_input.textChanged.connect(self.validate_chain_file)
        
    def browse_chain_file(self):
        """Open file dialog to select chain file"""
        file_path, selected_filter = QFileDialog.getOpenFileName(
            self,
            _("Select Chain File"),
            "",
            _("JSON Files (*.json);;All Files (*.*)")
        )
        
        if file_path:
            self.chain_file_input.setText(file_path)
            self.validate_chain_file()
    
    def validate_chain_file(self):
        """Validate the selected chain file and display information"""
        file_path = self.chain_file_input.text()
        
        if not file_path:
            self.chain_info.setText(_("No file selected"))
            self._populate_data_outputs([])
            self.button_box.button(QDialogButtonBox.Ok).setEnabled(False)
            return
        
        if not os.path.exists(file_path):
            self.chain_info.setText(_("File does not exist"))
            self._populate_data_outputs([])
            self.button_box.button(QDialogButtonBox.Ok).setEnabled(False)
            return
        
        try:
            import json
            with open(file_path, 'r') as f:
                chain_config = json.load(f)
            
            # Count different node types
            sequences = len(chain_config.get('sequences', []))
            conditionals = len(chain_config.get('conditional_nodes', []))
            llm_nodes = len(chain_config.get('llm_nodes', []))
            tts_nodes = len(chain_config.get('tts_nodes', []))
            web_sequences = len(chain_config.get('web_sequences', []))
            total_nodes = sequences + conditionals + llm_nodes + tts_nodes + web_sequences
            
            info_text = _(
                "Chain: {filename}\nTotal Nodes: {total}\n  - Sequences: {seq}\n  - Conditionals: {cond}\n  - LLM Nodes: {llm}\n  - TTS Nodes: {tts}\n  - Web Sequences: {web}"
            ).format(
                filename=os.path.basename(file_path),
                total=total_nodes,
                seq=sequences,
                cond=conditionals,
                llm=llm_nodes,
                tts=tts_nodes,
                web=web_sequences
            )
            
            self.chain_info.setText(info_text)
            self.button_box.button(QDialogButtonBox.Ok).setEnabled(total_nodes > 0)
            self._populate_data_outputs(chain_config.get('output_nodes', []))
            
        except Exception as e:
            self.chain_info.setText(_("Error reading chain file: {error}").format(error=str(e)))
            self._populate_data_outputs([])
            self.button_box.button(QDialogButtonBox.Ok).setEnabled(False)
    
    def _populate_data_outputs(self, output_nodes):
        """Rebuild the Output-node checklist from the selected chain file.

        Every Output node of the imported chain can be selected; its content
        is what gets delivered to the outer connection.  Items already present
        in the stored config stay checked so editing the node does not
        silently drop the selection.
        """
        self.data_outputs_list.clear()
        selected = {str(v) for v in (self.config.get('data_output_nodes') or [])}
        for out_cfg in output_nodes or []:
            if not isinstance(out_cfg, dict):
                continue
            node_id = str(out_cfg.get('node_id') or out_cfg.get('id') or '')
            if not node_id:
                continue
            label = str(out_cfg.get('label') or '')
            item = QListWidgetItem(label or node_id)
            item.setToolTip(node_id)
            item.setData(Qt.UserRole, node_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if node_id in selected else Qt.Unchecked)
            self.data_outputs_list.addItem(item)
        self.data_warning_label.setText("")
    
    def load_config(self):
        """Load configuration into the dialog"""
        self.chain_file_input.setText(self.config.get('chain_file', ''))
        self.import_mode_combo.setCurrentText(self.config.get('import_mode', 'full'))
        self.prefix_input.setText(self.config.get('prefix', ''))
        self.loop_count_input.setText(str(self.config.get('loop_count', 1)))
        self.extra_delay_input.setText(str(self.config.get('extra_delay', 0)))
        self.enabled_checkbox.setChecked(self.config.get('enabled', True))
        self.emit_data_toggle.setChecked(bool(self.config.get('emit_data')))
        
        # Validate the loaded file (also rebuilds the Output-node checklist)
        self.validate_chain_file()
    
    def set_config(self, config):
        """Set the configuration for the dialog"""
        self.config = config or {
            'chain_file': '',
            'import_mode': 'full',
            'prefix': '',
            'loop_count': 1,
            'extra_delay': 0,
            'enabled': True,
            'emit_data': False,
            'data_output_nodes': []
        }
        self.load_config()
    
    def get_config(self):
        """Get the configuration from the dialog"""
        try:
            loop_count = int(self.loop_count_input.text()) if self.loop_count_input.text() else 1
            extra_delay = float(self.extra_delay_input.text()) if self.extra_delay_input.text() else 0
        except ValueError:
            loop_count = 1
            extra_delay = 0
            
        return {
            'chain_file': self.chain_file_input.text(),
            'import_mode': self.import_mode_combo.currentText(),
            'prefix': self.prefix_input.text().strip(),
            'loop_count': loop_count,
            'extra_delay': extra_delay,
            'enabled': self.enabled_checkbox.isChecked(),
            'run_in_sandbox': self.sandbox_toggle.isChecked(),
            'show_sandbox_window': self.show_sandbox_window_toggle.isChecked(),
            'emit_data': self.emit_data_toggle.isChecked(),
            'data_output_nodes': [
                self.data_outputs_list.item(i).data(Qt.UserRole)
                for i in range(self.data_outputs_list.count())
                if self.data_outputs_list.item(i).checkState() == Qt.Checked
            ]
        }
    
    def accept(self):
        """Override accept to validate before closing"""
        config = self.get_config()
        
        if not config['chain_file']:
            QMessageBox.warning(self, _("Warning"), _("Please select a chain file."))
            return
        
        if not os.path.exists(config['chain_file']):
            QMessageBox.warning(self, _("Warning"), _("Selected chain file does not exist."))
            return
        
        super().accept()

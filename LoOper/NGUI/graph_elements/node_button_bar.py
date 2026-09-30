import logging
from PyQt5.QtWidgets import (
    QGraphicsObject, QGraphicsItem, QToolTip
)
from PyQt5.QtCore import QRectF, Qt, QObject, QPointF, QTimer
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QPolygonF

from ..constants import TEXT_COLOR

logger = logging.getLogger(__name__)

class NodeButtonBarItem(QGraphicsObject):
    """
    A custom graphics item that renders buttons.
    Designed to be a CHILD of the node item.
    This ensures it moves with the node automatically.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.ItemIsFocusable, False)
        # Ensure we are drawn on top of the parent (node)
        self.setZValue(1000.0)
        
        # Geometry
        self._width = 160  # Default, will be updated
        self._height = 32  # Slightly taller
        self._radius = 0   # No radius, fill style
        
        # Button definitions
        # We will calculate rects dynamically in layout_buttons()
        self.buttons = [
            {
                'id': 'record',
                'rect': QRectF(0, 0, 0, 0),
                'icon': self._draw_record_icon,
                'tooltip': 'Record / Re-record',
                'visible': False,
                'hover': False
            },
            {
                'id': 'play',
                'rect': QRectF(0, 0, 0, 0), 
                'icon': self._draw_play_icon,
                'tooltip': 'Play / Execute',
                'visible': True,
                'hover': False
            },
            {
                'id': 'options',
                'rect': QRectF(0, 0, 0, 0),
                'icon': self._draw_options_icon,
                'tooltip': 'Options',
                'visible': True,
                'hover': False
            },
            {
                'id': 'expand',
                'rect': QRectF(0, 0, 0, 0),
                'icon': self._draw_expand_icon,
                'tooltip': 'Expand Chain',
                'visible': False,
                'hover': False
            }
        ]
        
        # Callbacks
        self.on_click = None # function(btn_id)
        
        self._layout_buttons()

    def set_width(self, width):
        if self._width != width:
            self._width = width
            self._layout_buttons()
            self.update()

    def _layout_buttons(self):
        """
        Calculate button positions based on width.
        We will distribute them evenly across the width.
        Margin: 4px
        """
        margin = 4
        available_width = self._width - (margin * 2)
        y_pos = 4
        btn_size = 24
        
        # We have 3 slots: Left, Center, Right
        # Record -> Left
        # Play -> Center
        # Options -> Right
        
        # Left Slot
        self.buttons[0]['rect'] = QRectF(margin + 10, y_pos, btn_size, btn_size)
        
        # Expand Button (Left Slot, mutually exclusive with Record usually)
        self.buttons[3]['rect'] = QRectF(margin + 10, y_pos, btn_size, btn_size)
        
        # Center Slot
        center_x = self._width / 2
        self.buttons[1]['rect'] = QRectF(center_x - (btn_size/2), y_pos, btn_size, btn_size)
        
        # Right Slot
        self.buttons[2]['rect'] = QRectF(self._width - margin - 10 - btn_size, y_pos, btn_size, btn_size)

    def boundingRect(self):
        return QRectF(0, 0, self._width, self._height)

    def paint(self, painter, option, widget=None):
        # Draw Background
        # Fully transparent frame as requested
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.NoBrush) 
        # Draw rect covering the full width (invisible now, but useful for hit testing if needed)
        painter.drawRect(self.boundingRect())
        
        # Draw Buttons
        for btn in self.buttons:
            if not btn['visible']:
                continue
                
            # Draw hover background
            if btn['hover']:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 255, 255, 40))
                painter.drawRoundedRect(btn['rect'], 4, 4)
            
            # Draw Icon
            painter.save()
            center = btn['rect'].center()
            painter.translate(center)
            btn['icon'](painter)
            painter.restore()

    # --- Icon Drawers ---
    def _draw_record_icon(self, painter):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#ff5555"))
        painter.drawEllipse(QPointF(0, 0), 8, 8)

    def _draw_play_icon(self, painter):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#55ff55"))
        poly = QPolygonF([QPointF(-6, -8), QPointF(8, 0), QPointF(-6, 8)])
        painter.drawPolygon(poly)

    def _draw_options_icon(self, painter):
        # Hamburger / Menu icon
        pen = QPen(QColor(220, 220, 220))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(-7, -6, 7, -6)
        painter.drawLine(-7, 0, 7, 0)
        painter.drawLine(-7, 6, 7, 6)

    def _draw_expand_icon(self, painter):
        # Expand / Fullscreen style icon
        pen = QPen(QColor(220, 220, 220))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        s = 6
        # Top-left
        painter.drawLine(-s, -s, -s+4, -s)
        painter.drawLine(-s, -s, -s, -s+4)
        # Top-right
        painter.drawLine(s, -s, s-4, -s)
        painter.drawLine(s, -s, s, -s+4)
        # Bottom-left
        painter.drawLine(-s, s, -s+4, s)
        painter.drawLine(-s, s, -s, s-4)
        # Bottom-right
        painter.drawLine(s, s, s-4, s)
        painter.drawLine(s, s, s, s-4)

    # --- Events ---
    def hoverMoveEvent(self, event):
        pos = event.pos()
        changed = False
        for btn in self.buttons:
            if not btn['visible']:
                continue
            was_hover = btn['hover']
            is_hover = btn['rect'].contains(pos)
            btn['hover'] = is_hover
            if was_hover != is_hover:
                changed = True
                if is_hover:
                    QToolTip.showText(event.screenPos(), btn['tooltip'])
        
        if changed:
            self.update()

    def hoverLeaveEvent(self, event):
        changed = False
        for btn in self.buttons:
            if btn['hover']:
                btn['hover'] = False
                changed = True
        if changed:
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.pos()
            for btn in self.buttons:
                if btn['visible'] and btn['rect'].contains(pos):
                    if self.on_click:
                        self.on_click(btn['id'])
                    event.accept()
                    return
        super().mousePressEvent(event)

    def set_button_visible(self, btn_id, visible):
        for btn in self.buttons:
            if btn['id'] == btn_id:
                btn['visible'] = visible
                self.update()
                return

class NodeButtonBarManager(QObject):
    def __init__(self, parent_graph_view):
        super().__init__()
        self.parent = parent_graph_view
        self._bars = {}  # node_id -> { 'item': NodeButtonBarItem, 'node': node }
        
        # Timer to maintenance (check new nodes, sync width)
        self._maintenance_timer = QTimer()
        self._maintenance_timer.timeout.connect(self._maintenance_loop)
        self._maintenance_timer.start(100) # Check every 100ms

    def install(self):
        # Initial scan
        if hasattr(self.parent, 'node_graph'):
            for node in self.parent.node_graph.all_nodes():
                self.attach(node)

    def attach(self, node):
        try:
            if node.id in self._bars:
                return

            # Get the QGraphicsItem (NodeItem)
            item = getattr(node, 'view', None)
            if not item:
                item = getattr(node, '_view', None)
            
            if not item:
                # Not ready yet
                return
            
            # Create Item - PARENT TO NODE VIEW
            # This ensures it moves with the node and is drawn on top (with ZValue)
            bar_item = NodeButtonBarItem(parent=item)
            
            # Connect callback
            bar_item.on_click = lambda btn_id: self._handle_click(node, btn_id)

            # Store info
            self._bars[node.id] = {
                'item': bar_item,
                'node': node
            }

            # Apply rules
            self._apply_type_rules(node, bar_item)
            
            # Initial position sync
            self._update_item_pos(bar_item, item)

        except Exception as e:
            logger.debug(f"Attach bar failed for node {node.id}: {e}")

    def detach(self, node_id):
        try:
            info = self._bars.pop(node_id, None)
            if info:
                item = info.get('item')
                if item:
                    # Since it's a child item, it might be deleted with parent.
                    # But if we are detaching manually, we should remove it.
                    import sip
                    if not sip.isdeleted(item):
                        if item.scene():
                            item.scene().removeItem(item)
                        else:
                            # Just set parent to None to detach
                            item.setParentItem(None)
        except Exception as e:
            logger.debug(f"Detach bar failed: {e}")

    def _maintenance_loop(self):
        """
        Periodically check for:
        1. New nodes to attach.
        2. Deleted nodes to detach.
        3. Node resize (update button bar position).
        """
        if not hasattr(self.parent, 'node_graph'):
            return

        # 1. Check for new nodes
        all_nodes = self.parent.node_graph.all_nodes()
        current_ids = set()
        
        for node in all_nodes:
            current_ids.add(node.id)
            if node.id not in self._bars:
                self.attach(node)
        
        # 2. Check for deleted nodes
        for node_id in list(self._bars.keys()):
            if node_id not in current_ids:
                self.detach(node_id)

        # 3. Update positions (centering)
        for node_id, info in self._bars.items():
            item = info['item']
            node = info['node']
            node_view = getattr(node, 'view', None)
            
            # Safety check if view was deleted
            if not node_view:
                continue
                
            self._update_item_pos(item, node_view)

    def _update_item_pos(self, bar_item, node_view):
        # Set width to match node width (minus small margin)
        # Set Y to be just below header (approx 25-30px)
        
        try:
            # Get node width
            node_width = 160
            if hasattr(node_view, 'boundingRect'):
                node_width = node_view.boundingRect().width()
            elif hasattr(node_view, 'width'):
                node_width = node_view.width
            
            # Update Bar Width
            # We want it to span the node width, maybe with 1px margin
            target_width = node_width - 2
            bar_item.set_width(target_width)
            
            # Position
            # x = 1 (centered with 2px difference)
            # y = 25 (Just below header, moved up from 30)
            
            x_pos = 1
            y_pos = 25
            
            new_pos = QPointF(x_pos, y_pos)
            
            if bar_item.pos() != new_pos:
                bar_item.setPos(new_pos)
        except Exception:
            pass

    def _apply_type_rules(self, node, bar_item: NodeButtonBarItem):
        ntype = getattr(node, '__identifier__', node.type_)
        
        bar_item.set_button_visible('record', False)
        bar_item.set_button_visible('play', True)
        bar_item.set_button_visible('options', True)
        bar_item.set_button_visible('expand', False)

        if 'sequence' in ntype or 'SequenceNode' in ntype:
            bar_item.set_button_visible('record', True)
            
        elif 'conditional' in ntype or 'ConditionalNode' in ntype:
            # User requested Play button to be visible for Conditional nodes
            bar_item.set_button_visible('play', True)
            
        elif 'input' in ntype or 'InputNode' in ntype:
            bar_item.set_button_visible('play', False)
            # Show the options (hamburger / three-lines) button so the user can
            # open the input dialog without having to right-click -> context menu.
            bar_item.set_button_visible('options', True)

        elif 'chain_import' in ntype or 'ChainImportNode' in ntype:
            bar_item.set_button_visible('expand', True)

    def _handle_click(self, node, btn_id):
        ntype = getattr(node, '__identifier__', node.type_)
        
        if btn_id == 'options':
            self._on_options(node)
        elif btn_id == 'record':
            self._on_record(node)
        elif btn_id == 'expand':
            self._on_expand_chain(node)
        elif btn_id == 'play':
            if 'sequence' in ntype:
                self._on_play_sequence(node)
            elif 'llm' in ntype:
                self._on_exec_llm(node)
            elif 'chain_import' in ntype:
                self._on_exec_chain_import(node)
            elif 'conditional' in ntype:
                self._on_exec_conditional(node)
            elif 'code' in ntype:
                self._on_exec_code(node)
            elif 'handle' in ntype:
                self._on_exec_handle(node)
            elif 'mcp' in ntype:
                self._on_exec_mcp(node)

    def _get_main_window(self):
        """Traverse up the widget hierarchy to find the MainWindow."""
        # Start from the graph view widget
        curr = self.parent
        
        # Try traversing up using both parentWidget() and parent()
        while curr:
            # Check if this is the MainWindow by looking for its unique methods
            if hasattr(curr, 'execute_chain_config') and hasattr(curr, 'start_recording'):
                return curr
            
            # Get the next parent in the hierarchy
            next_p = None
            if hasattr(curr, 'parentWidget') and callable(curr.parentWidget):
                try:
                    next_p = curr.parentWidget()
                except Exception:
                    next_p = None
            
            if not next_p and hasattr(curr, 'parent') and callable(curr.parent):
                try:
                    next_p = curr.parent()
                except Exception:
                    next_p = None
            
            if not next_p or next_p == curr:
                break
            curr = next_p
            
        return None

    # Actions
    def _execute_node(self, node):
        """Execute a single node by creating a temporary chain config."""
        try:
            main_window = self._get_main_window()
            if main_window:
                # We need config_manager. GraphView has it.
                if hasattr(self.parent, 'config_manager'):
                    chain_config = self.parent.config_manager.get_single_node_config(node)
                    main_window.execute_chain_config(chain_config)
                else:
                    logger.warning("Config manager not found in GraphView")
            else:
                logger.warning("Main window not found for execution")
        except Exception as e:
            logger.error(f"Error executing node: {e}", exc_info=True)

    def _on_record(self, node):
        """Re-record the sequence associated with this node immediately."""
        try:
            ntype = getattr(node, '__identifier__', node.type_)
            # Web sequence nodes record DOM-based browser sessions - never
            # route them through the desktop ElementRecorder.
            if 'web_sequence' in ntype:
                logger.info(f"Re-recording web session for node: {node.id}")
                if hasattr(self.parent, 'record_web_session'):
                    self.parent.record_web_session(node)
                else:
                    logger.warning("GraphView lacks 'record_web_session' method")
                return

            sequence_file = node.get_property('sequence_file')
            if sequence_file:
                # Extract name from filename
                import os
                # Ensure we have a string and handle potential full paths
                seq_path = str(sequence_file)
                name = os.path.splitext(os.path.basename(seq_path))[0]
                
                logger.info(f"Re-recording triggered for sequence name: '{name}' (source: {seq_path})")
                
                main_window = self._get_main_window()
                if main_window:
                    if hasattr(main_window, 'start_recording'):
                        logger.info(f"Calling start_recording('{name}') on main window")
                        main_window.start_recording(name)
                    else:
                        logger.error("Found MainWindow but it lacks 'start_recording' method")
                else:
                    logger.error("Could not locate MainWindow for re-recording")
            else:
                # Fallback to editing if no sequence file is set yet
                logger.info("No sequence file assigned to node, opening properties dialog")
                if hasattr(self.parent, 'edit_sequence'):
                    self.parent.edit_sequence(node)
                else:
                    logger.warning("GraphView lacks 'edit_sequence' method for fallback")
        except Exception as e:
            logger.error(f"Error in _on_record: {e}", exc_info=True)

    def _on_play_sequence(self, node):
        self._execute_node(node)

    def _on_exec_llm(self, node):
        self._execute_node(node)

    def _on_exec_chain_import(self, node):
        self._execute_node(node)

    def _on_exec_code(self, node):
        self._execute_node(node)

    def _on_exec_handle(self, node):
        self._execute_node(node)

    def _on_exec_mcp(self, node):
        self._execute_node(node)

    def _on_exec_conditional(self, node):
        main_window = self._get_main_window()
        if main_window:
            if hasattr(main_window, 'test_conditional_node'):
                main_window.test_conditional_node(node)
            else:
                logger.warning("MainWindow missing test_conditional_node method")
        else:
            logger.warning("Could not find MainWindow")

    def _on_expand_chain(self, node):
        if hasattr(self.parent, 'expand_chain_import'):
            self.parent.expand_chain_import(node)
        elif hasattr(self.parent, 'node_operations') and hasattr(self.parent.node_operations, 'expand_chain_import'):
            self.parent.node_operations.expand_chain_import(node)

    def _on_options(self, node):
        if hasattr(self.parent, 'open_node_settings'):
            self.parent.open_node_settings(node)
        elif hasattr(self.parent, 'edit_node'):
            self.parent.edit_node(node)
        else:
            ntype = getattr(node, '__identifier__', node.type_)
            # Web sequence nodes are a DISTINCT node type from desktop
            # sequences: their options open the web-specific properties dialog
            # (record + headless + speed + native actions), never the desktop
            # SequencePropertiesDialog - the 'sequence' substring in their
            # identifier would otherwise match the branch below.
            if 'web_sequence' in ntype or 'WebSequenceNode' in ntype:
                if hasattr(self.parent, 'edit_web_sequence'):
                    self.parent.edit_web_sequence(node)
                return
            if 'sequence' in ntype:
                if hasattr(self.parent, 'edit_sequence'):
                    self.parent.edit_sequence(node)
            elif 'conditional' in ntype:
                # Correct method name is edit_conditional, not edit_conditional_node
                if hasattr(self.parent, 'edit_conditional'):
                    self.parent.edit_conditional(node)
                elif hasattr(self.parent, 'edit_conditional_node'):
                     self.parent.edit_conditional_node(node)
            elif 'llm' in ntype:
                if hasattr(self.parent, 'edit_llm_node'):
                    self.parent.edit_llm_node(node)
                elif hasattr(self.parent, 'edit_llm'):
                    self.parent.edit_llm(node)
            elif 'chain_import' in ntype:
                if hasattr(self.parent, 'edit_chain_import_node'):
                    self.parent.edit_chain_import_node(node)
            elif 'code' in ntype:
                if hasattr(self.parent, 'edit_code_node'):
                    self.parent.edit_code_node(node)
            elif 'context' in ntype:
                if hasattr(self.parent, 'edit_context_node'):
                    self.parent.edit_context_node(node)
            elif 'handle' in ntype:
                if hasattr(self.parent, 'edit_handle_node'):
                    self.parent.edit_handle_node(node)
            elif 'form_filler' in ntype:
                if hasattr(self.parent, 'edit_form_filler_node'):
                    self.parent.edit_form_filler_node(node)
            elif 'mcp' in ntype:
                if hasattr(self.parent, 'edit_mcp_node'):
                    self.parent.edit_mcp_node(node)
            elif 'input' in ntype:
                if hasattr(self.parent, 'edit_input_node'):
                    self.parent.edit_input_node(node)
            elif 'output' in ntype:
                if hasattr(self.parent, 'edit_output_node'):
                    self.parent.edit_output_node(node)

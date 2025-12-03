"""
Custom Pipe implementation with orthogonal (90-degree) routing.

This module provides custom pipe drawing for NodeGraphQt that enforces
orthogonal connections - wires only use horizontal and vertical segments,
never diagonal lines.
"""
import logging
from PyQt5.QtWidgets import QGraphicsEllipseItem, QGraphicsPathItem
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QPen, QBrush, QColor, QPainterPath
from NodeGraphQt.qgraphics.pipe import PipeItem

logger = logging.getLogger(__name__)

# Store original pipe factory for patching
_original_pipe_factory = None


class ControlPoint(QGraphicsEllipseItem):
    """Draggable control point for adjusting pipe routing with grid snapping."""
    
    def __init__(self, parent_pipe, index, x, y, grid_size=10):
        size = 8
        super().__init__(-size/2, -size/2, size, size)
        self.parent_pipe = parent_pipe
        self.index = index
        self.grid_size = grid_size
        self.dragging = False
        
        # Visual styling
        self.setBrush(QBrush(QColor(100, 150, 255, 200)))
        self.setPen(QPen(QColor(255, 255, 255), 1))
        
        # Make interactive
        self.setFlag(QGraphicsEllipseItem.ItemIsMovable, True)
        self.setFlag(QGraphicsEllipseItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsEllipseItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setPos(x, y)
        self.setZValue(2)  # Appear above pipes
        
    def mousePressEvent(self, event):
        """Start dragging on left click."""
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.setBrush(QBrush(QColor(150, 200, 255, 255)))
        super().mousePressEvent(event)
        
    def mouseMoveEvent(self, event):
        """Update pipe path while dragging."""
        if self.dragging:
            super().mouseMoveEvent(event)
            if self.parent_pipe:
                self.parent_pipe.draw_orthogonal_path()
                
    def mouseReleaseEvent(self, event):
        """Snap to grid on release."""
        if event.button() == Qt.LeftButton and self.dragging:
            self.dragging = False
            # Snap position to grid
            pos = self.pos()
            snapped_x = round(pos.x() / self.grid_size) * self.grid_size
            snapped_y = round(pos.y() / self.grid_size) * self.grid_size
            self.setPos(snapped_x, snapped_y)
            
            if self.parent_pipe:
                self.parent_pipe.draw_orthogonal_path()
            
            self.setBrush(QBrush(QColor(100, 150, 255, 200)))
        super().mouseReleaseEvent(event)
        
    def hoverEnterEvent(self, event):
        """Highlight on hover."""
        self.setBrush(QBrush(QColor(120, 170, 255, 220)))
        self.setCursor(Qt.SizeAllCursor)
        super().hoverEnterEvent(event)
        
    def hoverLeaveEvent(self, event):
        """Remove highlight."""
        if not self.dragging:
            self.setBrush(QBrush(QColor(100, 150, 255, 200)))
        self.setCursor(Qt.ArrowCursor)
        super().hoverLeaveEvent(event)


class CustomPipe(PipeItem):
    """
    Custom pipe that draws connections using only orthogonal (90-degree) lines.
    
    Features:
    - Only horizontal and vertical segments (no diagonals)
    - Draggable control points for manual routing
    - Grid snapping for clean alignment
    """
    
    def __init__(self, input_port=None, output_port=None):
        """Initialize custom pipe with orthogonal routing."""
        from PyQt5 import QtWidgets, QtGui, QtCore
        from NodeGraphQt.constants import PipeEnum, Z_VAL_PIPE, ITEM_CACHE_MODE
        
        # Initialize as QGraphicsPathItem directly
        QGraphicsPathItem.__init__(self)
        
        # Set up graphics item properties
        self.setZValue(Z_VAL_PIPE)
        self.setAcceptHoverEvents(True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable)
        self.setCacheMode(ITEM_CACHE_MODE)

        # Pipe visual properties
        self._color = PipeEnum.COLOR.value
        self._style = PipeEnum.DRAW_TYPE_DEFAULT.value
        self._active = False
        self._highlight = False
        self._input_port = input_port
        self._output_port = output_port

        # Direction pointer (arrow)
        size = 6.0
        self._poly = QtGui.QPolygonF()
        self._poly.append(QtCore.QPointF(-size, size))
        self._poly.append(QtCore.QPointF(0.0, -size * 1.5))
        self._poly.append(QtCore.QPointF(size, size))
        self._dir_pointer = QtWidgets.QGraphicsPolygonItem(self)
        self._dir_pointer.setPolygon(self._poly)
        self._dir_pointer.setFlag(QtWidgets.QGraphicsPathItem.ItemIsSelectable, False)
        
        # Orthogonal routing properties
        self.control_points = []
        self.grid_size = 10
        self._creating_control_points = False
    
    @property
    def input_port(self):
        """Get input port."""
        return self._input_port
    
    @property
    def output_port(self):
        """Get output port."""
        return self._output_port
    
    def itemChange(self, change, value):
        """Override to prevent parent's problematic super() call."""
        return QGraphicsPathItem.itemChange(self, change, value)
    
    def draw_path(self, start_port, end_port=None, cursor_pos=None):
        """
        Draw the pipe path using orthogonal routing.
        
        Args:
            start_port: Starting port (output)
            end_port: Ending port (input), None if drawing to cursor
            cursor_pos: Cursor position for live wire drawing
        """
        if not start_port:
            return
        
        self._output_port = start_port
        if end_port:
            self._input_port = end_port
        
        if self._input_port and self._output_port:
            # Connected wire - draw orthogonal path
            self.draw_orthogonal_path()
        elif cursor_pos:
            # Live wire to cursor - also orthogonal
            self._draw_live_wire(cursor_pos)
    
    def _draw_live_wire(self, cursor_pos):
        """Draw orthogonal wire from port to cursor position."""
        path = QPainterPath()
        start_pos = self._get_port_pos(self._output_port)
        cursor_in_item = self.mapFromScene(cursor_pos)
        
        # Calculate midpoint for routing
        mid_x = (start_pos.x() + cursor_in_item.x()) / 2
        
        # Draw 3-segment orthogonal path
        path.moveTo(start_pos)
        path.lineTo(mid_x, start_pos.y())      # Horizontal to midpoint
        path.lineTo(mid_x, cursor_in_item.y()) # Vertical
        path.lineTo(cursor_in_item.x(), cursor_in_item.y())  # Horizontal to cursor
        
        self.setPath(path)
    
    def draw_orthogonal_path(self):
        """
        Draw orthogonal path between connected ports.
        Uses control points if available, otherwise creates a simple 3-segment route.
        """
        if not self._input_port or not self._output_port:
            return
        
        path = QPainterPath()
        start_pos = self._get_port_pos(self._output_port)
        end_pos = self._get_port_pos(self._input_port)
        
        path.moveTo(start_pos)
        
        if self.control_points:
            # Route through control points with orthogonal segments
            current_pos = start_pos
            for cp in self.control_points:
                cp_pos = cp.pos()
                # Always horizontal first, then vertical
                path.lineTo(cp_pos.x(), current_pos.y())
                path.lineTo(cp_pos.x(), cp_pos.y())
                current_pos = cp_pos
            
            # Final segment to end port
            path.lineTo(end_pos.x(), current_pos.y())
            path.lineTo(end_pos)
        else:
            # Simple 3-segment route through midpoint
            mid_x = (start_pos.x() + end_pos.x()) / 2
            path.lineTo(mid_x, start_pos.y())  # Horizontal to midpoint
            path.lineTo(mid_x, end_pos.y())    # Vertical
            path.lineTo(end_pos)                # Horizontal to end
        
        self.setPath(path)
    
    def _get_port_pos(self, port):
        """Get the position of a port in this item's coordinate system."""
        if port:
            return self.mapFromItem(port, port.boundingRect().center())
        return QPointF(0, 0)
    
    def reset(self):
        """Reset and redraw the pipe."""
        if self._input_port and self._output_port:
            self.draw_orthogonal_path()
            if self._active:
                self.activate()
            else:
                self.deactivate()
    
    def activate(self):
        """Highlight pipe when active."""
        self._active = True
        self.setPen(QPen(QColor(255, 255, 255), 2.5))
        
    def deactivate(self):
        """Return to normal appearance."""
        self._active = False
        self.setPen(QPen(self._color, 2.0))
    
    def create_control_points(self):
        """Create default control points at the midpoint for manual routing."""
        if self._creating_control_points or not self.scene():
            return
        
        self._creating_control_points = True
        try:
            self.clear_control_points()
            
            if self._input_port and self._output_port:
                start_pos = self._get_port_pos(self._input_port)
                end_pos = self._get_port_pos(self._output_port)
                mid_x = (start_pos.x() + end_pos.x()) / 2
                
                # Create two control points for the routing corner
                cp1 = ControlPoint(self, 0, mid_x, start_pos.y(), self.grid_size)
                cp2 = ControlPoint(self, 1, mid_x, end_pos.y(), self.grid_size)
                
                self.scene().addItem(cp1)
                self.scene().addItem(cp2)
                self.control_points.extend([cp1, cp2])
                
                self.draw_orthogonal_path()
        except Exception as e:
            logger.error(f"Error creating control points: {e}")
        finally:
            self._creating_control_points = False
    
    def clear_control_points(self):
        """Remove all control points."""
        for cp in self.control_points:
            if cp.scene():
                cp.scene().removeItem(cp)
        self.control_points.clear()
    
    def paint(self, painter, option, widget):
        """
        Custom paint that draws only the orthogonal path.
        Does NOT call parent's paint() which would draw curved/diagonal lines.
        """
        # Draw the path directly using QGraphicsPathItem's paint
        QGraphicsPathItem.paint(self, painter, option, widget)
        
        # Create control points on first paint if needed
        if not self.control_points and not self._creating_control_points:
            self.create_control_points()
    
    def boundingRect(self):
        """Extended bounding rect for better selection."""
        return QGraphicsPathItem.boundingRect(self).adjusted(-10, -10, 10, 10)
    
    def delete(self):
        """Clean up when pipe is deleted."""
        self.clear_control_points()
        if hasattr(PipeItem, 'delete'):
            PipeItem.delete(self)


def patch_pipe_factory():
    """
    Patch NodeGraphQt to use our CustomPipe class for all pipe creation.
    Also patches the original PipeItem class to fix super() issues.
    """
    global _original_pipe_factory
    
    try:
        import NodeGraphQt.qgraphics.pipe as pipe_module
        from PyQt5 import QtWidgets, QtGui, QtCore
        from NodeGraphQt.constants import PipeEnum, Z_VAL_PIPE, ITEM_CACHE_MODE
        
        if _original_pipe_factory is None:
            _original_pipe_factory = pipe_module.PipeItem
        
        # Create patched __init__ for any PipeItem instances created internally
        def patched_init(self, input_port=None, output_port=None):
            QGraphicsPathItem.__init__(self)
            self.setZValue(Z_VAL_PIPE)
            self.setAcceptHoverEvents(True)
            self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable)
            self.setCacheMode(ITEM_CACHE_MODE)
            self._color = PipeEnum.COLOR.value
            self._style = PipeEnum.DRAW_TYPE_DEFAULT.value
            self._active = False
            self._highlight = False
            self._input_port = input_port
            self._output_port = output_port
            size = 6.0
            self._poly = QtGui.QPolygonF()
            self._poly.append(QtCore.QPointF(-size, size))
            self._poly.append(QtCore.QPointF(0.0, -size * 1.5))
            self._poly.append(QtCore.QPointF(size, size))
            self._dir_pointer = QtWidgets.QGraphicsPolygonItem(self)
            self._dir_pointer.setPolygon(self._poly)
            self._dir_pointer.setFlag(QtWidgets.QGraphicsPathItem.ItemIsSelectable, False)
            if not hasattr(self, 'control_points'):
                self.control_points = []
                self.grid_size = 10
                self._creating_control_points = False
        
        def patched_item_change(self, change, value):
            return QGraphicsPathItem.itemChange(self, change, value)
        
        # Apply patches
        _original_pipe_factory.__init__ = patched_init
        _original_pipe_factory.itemChange = patched_item_change
        
        # Replace PipeItem class with CustomPipe
        pipe_module.PipeItem = CustomPipe
        
        logger.info("Successfully patched PipeItem factory for orthogonal routing")
        
    except Exception as e:
        logger.error(f"Failed to patch pipe factory: {e}")

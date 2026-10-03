#!/usr/bin/python
from collections import OrderedDict

from Qt import QtCore, QtGui, QtWidgets

from NodeGraphQt.constants import (ICON_NODE_BASE, ITEM_CACHE_MODE, Z_VAL_NODE,
                                   LayoutDirectionEnum, NodeEnum, PortEnum,
                                   PortTypeEnum)
from NodeGraphQt.errors import NodeWidgetError
from NodeGraphQt.qgraphics.node_abstract import AbstractNodeItem
from NodeGraphQt.qgraphics.node_overlay_disabled import XDisabledItem
from NodeGraphQt.qgraphics.node_text_item import NodeTextItem
from NodeGraphQt.qgraphics.port import CustomPortItem, PortItem


# LoOper node look: every node is a uniform rounded SQUARE.  The node-type
# glyph is centred on the card (sized by the caller) and the node name is kept
# in the tooltip, so the card stays icon-only and the glyph is never overlapped.
SQUARE_NODE_SIZE = 96.0
SQUARE_RADIUS = 16.0


class NodeItem(AbstractNodeItem):
    """
    Base Node item.

    Args:
        name (str): name displayed on the node.
        parent (QtWidgets.QGraphicsItem): parent item.
    """

    def __init__(self, name='node', parent=None):
        super(NodeItem, self).__init__(name, parent)
        pixmap = QtGui.QPixmap(ICON_NODE_BASE)
        if pixmap.size().height() > NodeEnum.ICON_SIZE.value:
            pixmap = pixmap.scaledToHeight(
                NodeEnum.ICON_SIZE.value,
                QtCore.Qt.SmoothTransformation
            )
        self._properties['icon'] = ICON_NODE_BASE
        self._icon_item = QtWidgets.QGraphicsPixmapItem(pixmap, self)
        self._icon_item.setTransformationMode(QtCore.Qt.SmoothTransformation)
        self._text_item = NodeTextItem(self.name, self)
        self._x_item = XDisabledItem(self, 'DISABLED')
        self._input_items = OrderedDict()
        self._output_items = OrderedDict()
        self._widgets = OrderedDict()
        self._proxy_mode = False
        self._proxy_mode_threshold = 70

    def post_init(self, viewer, pos=None):
        """
        Called after node has been added into the scene.

        Args:
            viewer (NodeGraphQt.widgets.viewer.NodeViewer): main viewer
            pos (tuple): the cursor pos if node is called with tab search.
        """
        if self.layout_direction == LayoutDirectionEnum.VERTICAL.value:
            font = QtGui.QFont()
            font.setPointSize(15)
            self.text_item.setFont(font)

            # hide port text items for vertical layout.
            if self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
                for text_item in self._input_items.values():
                    text_item.setVisible(False)
                for text_item in self._output_items.values():
                    text_item.setVisible(False)

    def _paint_horizontal(self, painter, option, widget):
        painter.save()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtCore.Qt.NoBrush)

        # base background (rounded square).
        margin = 1.0
        rect = self.boundingRect()
        rect = QtCore.QRectF(rect.left() + margin,
                             rect.top() + margin,
                             rect.width() - (margin * 2),
                             rect.height() - (margin * 2))

        radius = SQUARE_RADIUS
        painter.setBrush(QtGui.QColor(*self.color))
        painter.drawRoundedRect(rect, radius, radius)

        # light overlay on background when selected.
        if self.selected:
            painter.setBrush(QtGui.QColor(*NodeEnum.SELECTED_COLOR.value))
            painter.drawRoundedRect(rect, radius, radius)

        # node border
        if self.selected:
            border_width = 1.2
            border_color = QtGui.QColor(
                *NodeEnum.SELECTED_BORDER_COLOR.value
            )
        else:
            border_width = 0.8
            border_color = QtGui.QColor(*self.border_color)

        border_rect = QtCore.QRectF(rect.left(), rect.top(),
                                    rect.width(), rect.height())

        pen = QtGui.QPen(border_color, border_width)
        # LoOper guard: viewer() is None while the node's scene has no attached
        # view (it is being removed). Touching it here raised AttributeError
        # ('NoneType' has no attribute 'get_zoom') mid-paint and aborted the app.
        _viewer = self.viewer()
        pen.setCosmetic(_viewer.get_zoom() < 0.0 if _viewer else False)
        path = QtGui.QPainterPath()
        path.addRoundedRect(border_rect, radius, radius)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(pen)
        painter.drawPath(path)

        painter.restore()

    def _paint_vertical(self, painter, option, widget):
        painter.save()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtCore.Qt.NoBrush)

        # base background.
        margin = 1.0
        rect = self.boundingRect()
        rect = QtCore.QRectF(rect.left() + margin,
                             rect.top() + margin,
                             rect.width() - (margin * 2),
                             rect.height() - (margin * 2))

        radius = 4.0
        painter.setBrush(QtGui.QColor(*self.color))
        painter.drawRoundedRect(rect, radius, radius)

        # light overlay on background when selected.
        if self.selected:
            painter.setBrush(
                QtGui.QColor(*NodeEnum.SELECTED_COLOR.value)
            )
            painter.drawRoundedRect(rect, radius, radius)

        # top & bottom edge background.
        padding = 2.0
        height = 10
        if self.selected:
            painter.setBrush(QtGui.QColor(*NodeEnum.SELECTED_COLOR.value))
        else:
            painter.setBrush(QtGui.QColor(0, 0, 0, 80))
        for y in [rect.y() + padding, rect.height() - height - 1]:
            edge_rect = QtCore.QRectF(rect.x() + padding, y,
                                     rect.width() - (padding * 2), height)
            painter.drawRoundedRect(edge_rect, 3.0, 3.0)

        # node border
        border_width = 0.8
        border_color = QtGui.QColor(*self.border_color)
        if self.selected:
            border_width = 1.2
            border_color = QtGui.QColor(
                *NodeEnum.SELECTED_BORDER_COLOR.value
            )
        border_rect = QtCore.QRectF(rect.left(), rect.top(),
                                    rect.width(), rect.height())

        pen = QtGui.QPen(border_color, border_width)
        # LoOper guard: viewer() is None while the node's scene has no attached
        # view (it is being removed). Touching it here raised AttributeError
        # ('NoneType' has no attribute 'get_zoom') mid-paint and aborted the app.
        _viewer = self.viewer()
        pen.setCosmetic(_viewer.get_zoom() < 0.0 if _viewer else False)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(pen)
        painter.drawRoundedRect(border_rect, radius, radius)

        painter.restore()

    def paint(self, painter, option, widget):
        """
        Draws the node base not the ports.

        Args:
            painter (QtGui.QPainter): painter used for drawing the item.
            option (QtGui.QStyleOptionGraphicsItem):
                used to describe the parameters needed to draw.
            widget (QtWidgets.QWidget): not used.
        """
        self.auto_switch_mode()
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._paint_horizontal(painter, option, widget)
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._paint_vertical(painter, option, widget)
        else:
            raise RuntimeError('Node graph layout direction not valid!')

    def mousePressEvent(self, event):
        """
        Re-implemented to ignore event if LMB is over port collision area.

        Args:
            event (QtWidgets.QGraphicsSceneMouseEvent): mouse event.
        """
        if event.button() == QtCore.Qt.LeftButton:
            for p in self._input_items.keys():
                if p.hovered:
                    event.ignore()
                    return
            for p in self._output_items.keys():
                if p.hovered:
                    event.ignore()
                    return
        super(NodeItem, self).mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """
        Re-implemented to ignore event if Alt modifier is pressed.

        Args:
            event (QtWidgets.QGraphicsSceneMouseEvent): mouse event.
        """
        if event.modifiers() == QtCore.Qt.AltModifier:
            event.ignore()
            return
        super(NodeItem, self).mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """
        Re-implemented to emit "node_double_clicked" signal.

        Args:
            event (QtWidgets.QGraphicsSceneMouseEvent): mouse event.
        """
        if event.button() == QtCore.Qt.LeftButton:
            if not self.disabled:
                # enable text item edit mode.
                items = self.scene().items(event.scenePos())
                if self._text_item in items:
                    self._text_item.set_editable(True)
                    self._text_item.setFocus()
                    event.ignore()
                    return

            viewer = self.viewer()
            if viewer:
                viewer.node_double_clicked.emit(self.id)
        super(NodeItem, self).mouseDoubleClickEvent(event)

    def itemChange(self, change, value):
        """
        Re-implemented to update pipes on selection changed.

        Args:
            change:
            value:
        """
        if change == QtWidgets.QGraphicsItem.ItemSelectedChange and self.scene():
            self.reset_pipes()
            if value:
                self.highlight_pipes()
            self.setZValue(Z_VAL_NODE)
            if not self.selected:
                self.setZValue(Z_VAL_NODE + 1)

        return super(NodeItem, self).itemChange(change, value)

    def _tooltip_disable(self, state):
        """
        Updates the node tooltip when the node is enabled/disabled.

        Args:
            state (bool): node disable state.
        """
        tooltip = '<b>{}</b>'.format(self.name)
        if state:
            tooltip += ' <font color="red"><b>(DISABLED)</b></font>'
        tooltip += '<br/>{}<br/>'.format(self.type_)
        self.setToolTip(tooltip)

    def _set_base_size(self, add_w=0.0, add_h=0.0):
        """
        Sets the initial base size for the node.

        LoOper: every node is a uniform rounded square regardless of its label
        or port count, so the graph reads as a grid of icons.

        Args:
            add_w (float): ignored.
            add_h (float): ignored.
        """
        self._width = SQUARE_NODE_SIZE
        self._height = SQUARE_NODE_SIZE

    def _set_text_color(self, color):
        """
        set text color.

        Args:
            color (tuple): color value in (r, g, b, a).
        """
        text_color = QtGui.QColor(*color)
        for port, text in self._input_items.items():
            text.setDefaultTextColor(text_color)
        for port, text in self._output_items.items():
            text.setDefaultTextColor(text_color)
        self._text_item.setDefaultTextColor(text_color)

    def activate_pipes(self):
        """
        active pipe color.
        """
        ports = self.inputs + self.outputs
        for port in ports:
            for pipe in port.connected_pipes:
                pipe.activate()

    def highlight_pipes(self):
        """
        Highlight pipe color.
        """
        ports = self.inputs + self.outputs
        for port in ports:
            for pipe in port.connected_pipes:
                pipe.highlight()

    def reset_pipes(self):
        """
        Reset all the pipe colors.
        """
        ports = self.inputs + self.outputs
        for port in ports:
            for pipe in port.connected_pipes:
                pipe.reset()

    def _calc_size_horizontal(self):
        # width, height from node name text.
        text_w = self._text_item.boundingRect().width()
        text_h = self._text_item.boundingRect().height()

        # width, height from node ports.
        port_width = 0.0
        p_input_text_width = 0.0
        p_output_text_width = 0.0
        p_input_height = 0.0
        p_output_height = 0.0
        for port, text in self._input_items.items():
            if not port.isVisible():
                continue
            if not port_width:
                port_width = port.boundingRect().width()
            t_width = text.boundingRect().width()
            if text.isVisible() and t_width > p_input_text_width:
                p_input_text_width = text.boundingRect().width()
            p_input_height += port.boundingRect().height()
        for port, text in self._output_items.items():
            if not port.isVisible():
                continue
            if not port_width:
                port_width = port.boundingRect().width()
            t_width = text.boundingRect().width()
            if text.isVisible() and t_width > p_output_text_width:
                p_output_text_width = text.boundingRect().width()
            p_output_height += port.boundingRect().height()

        port_text_width = p_input_text_width + p_output_text_width

        # width, height from node embedded widgets.
        widget_width = 0.0
        widget_height = 0.0
        for widget in self._widgets.values():
            if not widget.isVisible():
                continue
            w_width = widget.boundingRect().width()
            w_height = widget.boundingRect().height()
            if w_width > widget_width:
                widget_width = w_width
            widget_height += w_height

        side_padding = 0.0
        if all([widget_width, p_input_text_width, p_output_text_width]):
            port_text_width = max([p_input_text_width, p_output_text_width])
            port_text_width *= 2
        elif widget_width:
            side_padding = 10

        width = port_width + max([text_w, port_text_width]) + side_padding
        height = max([text_h, p_input_height, p_output_height, widget_height])
        if widget_width:
            # add additional width for node widget.
            width += widget_width
        if widget_height:
            # add bottom margin for node widget.
            height += 4.0
        height *= 1.05
        return width, height

    def _calc_size_vertical(self):
        p_input_width = 0.0
        p_output_width = 0.0
        p_input_height = 0.0
        p_output_height = 0.0
        for port in self._input_items.keys():
            if port.isVisible():
                p_input_width += port.boundingRect().width()
                if not p_input_height:
                    p_input_height = port.boundingRect().height()
        for port in self._output_items.keys():
            if port.isVisible():
                p_output_width += port.boundingRect().width()
                if not p_output_height:
                    p_output_height = port.boundingRect().height()

        widget_width = 0.0
        widget_height = 0.0
        for widget in self._widgets.values():
            if not widget.isVisible():
                continue
            if widget.boundingRect().width() > widget_width:
                widget_width = widget.boundingRect().width()
            widget_height += widget.boundingRect().height()

        width = max([p_input_width, p_output_width, widget_width])
        height = p_input_height + p_output_height + widget_height
        return width, height

    def calc_size(self, add_w=0.0, add_h=0.0):
        """
        Calculates the minimum node size.

        Args:
            add_w (float): additional width.
            add_h (float): additional height.

        Returns:
            tuple(float, float): width, height.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            width, height = self._calc_size_horizontal()
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            width, height = self._calc_size_vertical()
        else:
            raise RuntimeError('Node graph layout direction not valid!')

        # additional width, height.
        width += add_w
        height += add_h
        return width, height

    def _align_icon_horizontal(self, h_offset, v_offset):
        # centre the glyph in the square card.
        icon_rect = self._icon_item.boundingRect()
        rect = self.boundingRect()
        x = rect.center().x() - (icon_rect.width() / 2)
        y = rect.center().y() - (icon_rect.height() / 2)
        self._icon_item.setPos(x + h_offset, y + v_offset)

    def _align_icon_vertical(self, h_offset, v_offset):
        center_y = self.boundingRect().center().y()
        icon_rect = self._icon_item.boundingRect()
        text_rect = self._text_item.boundingRect()
        x = self.boundingRect().right() + h_offset
        y = center_y - text_rect.height() - (icon_rect.height() / 2) + v_offset
        self._icon_item.setPos(x, y)

    def align_icon(self, h_offset=0.0, v_offset=0.0):
        """
        Align node icon to the default top left of the node.

        Args:
            v_offset (float): additional vertical offset.
            h_offset (float): additional horizontal offset.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._align_icon_horizontal(h_offset, v_offset)
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._align_icon_vertical(h_offset, v_offset)
        else:
            raise RuntimeError('Node graph layout direction not valid!')

    def _align_label_horizontal(self, h_offset, v_offset):
        rect = self.boundingRect()
        text_rect = self._text_item.boundingRect()
        x = rect.center().x() - (text_rect.width() / 2)
        self._text_item.setPos(x + h_offset, rect.y() + v_offset)

    def _align_label_vertical(self, h_offset, v_offset):
        rect = self._text_item.boundingRect()
        x = self.boundingRect().right() + h_offset
        y = self.boundingRect().center().y() - (rect.height() / 2) + v_offset
        self.text_item.setPos(x, y)

    def align_label(self, h_offset=0.0, v_offset=0.0):
        """
        Center node label text to the top of the node.

        Args:
            v_offset (float): vertical offset.
            h_offset (float): horizontal offset.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._align_label_horizontal(h_offset, v_offset)
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._align_label_vertical(h_offset, v_offset)
        else:
            raise RuntimeError('Node graph layout direction not valid!')

    def _align_widgets_horizontal(self, v_offset):
        if not self._widgets:
            return
        rect = self.boundingRect()
        y = rect.y() + v_offset
        inputs = [p for p in self.inputs if p.isVisible()]
        outputs = [p for p in self.outputs if p.isVisible()]
        for widget in self._widgets.values():
            if not widget.isVisible():
                continue
            widget_rect = widget.boundingRect()
            if not inputs:
                x = rect.left() + 10
                widget.widget().setTitleAlign('left')
            elif not outputs:
                x = rect.right() - widget_rect.width() - 10
                widget.widget().setTitleAlign('right')
            else:
                x = rect.center().x() - (widget_rect.width() / 2)
                widget.widget().setTitleAlign('center')
            widget.setPos(x, y)
            y += widget_rect.height()

    def _align_widgets_vertical(self, v_offset):
        if not self._widgets:
            return
        rect = self.boundingRect()
        y = rect.center().y() + v_offset
        widget_height = 0.0
        for widget in self._widgets.values():
            if not widget.isVisible():
                continue
            widget_rect = widget.boundingRect()
            widget_height += widget_rect.height()
        y -= widget_height / 2

        for widget in self._widgets.values():
            if not widget.isVisible():
                continue
            widget_rect = widget.boundingRect()
            x = rect.center().x() - (widget_rect.width() / 2)
            widget.widget().setTitleAlign('center')
            widget.setPos(x, y)
            y += widget_rect.height()

    def align_widgets(self, v_offset=0.0):
        """
        Align node widgets to the default center of the node.

        Args:
            v_offset (float): vertical offset.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._align_widgets_horizontal(v_offset)
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._align_widgets_vertical(v_offset)
        else:
            raise RuntimeError('Node graph layout direction not valid!')

    @staticmethod
    def _port_step(port_height, node_height, count):
        """Even spacing that keeps the whole port stack inside the card."""
        if count <= 1:
            return port_height + 1.0
        return min(port_height + 1.0, (node_height - port_height) / (count - 1))

    def _align_ports_horizontal(self, v_offset):
        # LoOper: the card is a compact square, so stack the ports EVENLY and
        # centre the stack on the left/right edge. The stock layout anchored
        # them at the (hidden) title height and grew downwards, so a node whose
        # ports change at runtime (LLM -> Orchestrator adds the 'tools' input +
        # 'trace'/'route' outputs) pushed its lower ports off the card and left
        # them "floating in the air".
        width = self._width
        height = self._height

        # adjust input position
        inputs = [p for p in self.inputs if p.isVisible()]
        if inputs:
            port_width = inputs[0].boundingRect().width()
            port_height = inputs[0].boundingRect().height()
            port_x = (port_width / 2) * -1
            count = len(inputs)
            step = self._port_step(port_height, height, count)
            start_y = (height - (count - 1) * step) / 2 - (port_height / 2)
            for i, port in enumerate(inputs):
                port.setPos(port_x, start_y + i * step)
        # adjust input text position (vertically centred on each port)
        for port, text in self._input_items.items():
            if port.isVisible():
                txt_x = port.x() + port.boundingRect().width() / 2 - text.boundingRect().width() - 10
                txt_y = port.y() + (port.boundingRect().height() - text.boundingRect().height()) / 2
                text.setPos(txt_x, txt_y)

        # adjust output position
        outputs = [p for p in self.outputs if p.isVisible()]
        if outputs:
            port_width = outputs[0].boundingRect().width()
            port_height = outputs[0].boundingRect().height()
            port_x = width - (port_width / 2)
            count = len(outputs)
            step = self._port_step(port_height, height, count)
            start_y = (height - (count - 1) * step) / 2 - (port_height / 2)
            for i, port in enumerate(outputs):
                port.setPos(port_x, start_y + i * step)
        # adjust output text position (vertically centred on each port)
        for port, text in self._output_items.items():
            if port.isVisible():
                txt_x = port.x() + port.boundingRect().width() / 2 + 10
                txt_y = port.y() + (port.boundingRect().height() - text.boundingRect().height()) / 2
                text.setPos(txt_x, txt_y)

    def _align_ports_vertical(self, v_offset):
        # adjust input position
        inputs = [p for p in self.inputs if p.isVisible()]
        if inputs:
            port_width = inputs[0].boundingRect().width()
            port_height = inputs[0].boundingRect().height()
            half_width = port_width / 2
            delta = self._width / (len(inputs) + 1)
            port_x = delta
            port_y = (port_height / 2) * -1
            for port in inputs:
                port.setPos(port_x - half_width, port_y)
                port_x += delta

        # adjust output position
        outputs = [p for p in self.outputs if p.isVisible()]
        if outputs:
            port_width = outputs[0].boundingRect().width()
            port_height = outputs[0].boundingRect().height()
            half_width = port_width / 2
            delta = self._width / (len(outputs) + 1)
            port_x = delta
            port_y = self._height - (port_height / 2)
            for port in outputs:
                port.setPos(port_x - half_width, port_y)
                port_x += delta

    def align_ports(self, v_offset=0.0):
        """
        Align input, output ports in the node layout.

        Args:
            v_offset (float): port vertical offset.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._align_ports_horizontal(v_offset)
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._align_ports_vertical(v_offset)
        else:
            raise RuntimeError('Node graph layout direction not valid!')

    def _draw_node_horizontal(self):
        height = self._text_item.boundingRect().height() + 4.0

        # update port text items in visibility.
        for port, text in self._input_items.items():
            if port.isVisible():
                text.setVisible(port.display_name)
        for port, text in self._output_items.items():
            if port.isVisible():
                text.setVisible(port.display_name)

        # setup initial base size.
        self._set_base_size(add_h=height)
        # set text color when node is initialized.
        self._set_text_color(self.text_color)
        # set the tooltip
        self._tooltip_disable(self.disabled)

        # --- set the initial node layout ---
        # (do all the graphic item layout offsets here)

        # The card is icon-only: the name lives in the tooltip (set above), so
        # the centred glyph is not overlapped by a label.
        self._text_item.setVisible(False)
        # align icon
        self.align_icon()
        # arrange input and output ports.
        self.align_ports(v_offset=height)
        # arrange node widgets
        self.align_widgets(v_offset=height)

        self.update()

    def _draw_node_vertical(self):
        # hide the port text items in vertical layout.
        for port, text in self._input_items.items():
            text.setVisible(False)
        for port, text in self._output_items.items():
            text.setVisible(False)

        # setup initial base size.
        self._set_base_size()
        # set text color when node is initialized.
        self._set_text_color(self.text_color)
        # set the tooltip
        self._tooltip_disable(self.disabled)

        # --- setup node layout ---
        # (do all the graphic item layout offsets here)

        # align label text
        self.align_label(h_offset=6)
        # align icon
        self.align_icon(h_offset=6, v_offset=4)
        # arrange input and output ports.
        self.align_ports()
        # arrange node widgets
        self.align_widgets()

        self.update()

    def draw_node(self):
        """
        Re-draw the node item in the scene with proper
        calculated size and widgets aligned.
        """
        if self.layout_direction is LayoutDirectionEnum.HORIZONTAL.value:
            self._draw_node_horizontal()
        elif self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            self._draw_node_vertical()
        else:
            raise RuntimeError('Node graph layout direction not valid!')

    def post_init(self, viewer=None, pos=None):
        """
        Called after node has been added into the scene.
        Adjust the node layout and form after the node has been added.

        Args:
            viewer (NodeGraphQt.widgets.viewer.NodeViewer): not used
            pos (tuple): cursor position.
        """
        self.draw_node()

        # set initial node position.
        if pos:
            self.xy_pos = pos

    def auto_switch_mode(self):
        """
        Decide whether to draw the node with proxy mode.
        (this is called at the start in the "self.paint()" function.)
        """
        if ITEM_CACHE_MODE is QtWidgets.QGraphicsItem.ItemCoordinateCache:
            return

        # LoOper guard: viewer() returns None while the node's scene has no
        # attached view - exactly the moment a node is being torn down / removed
        # from the scene. The stock code then raised "'NoneType' object has no
        # attribute 'mapToGlobal'" from inside paint(), which PyQt5 turns into a
        # fatal error and aborts the app: node deletion removed the connections
        # but never finished removing the node. Skip the proxy decision instead.
        viewer = self.viewer()
        if viewer is None:
            return

        rect = self.sceneBoundingRect()
        l = viewer.mapToGlobal(viewer.mapFromScene(rect.topLeft()))
        r = viewer.mapToGlobal(viewer.mapFromScene(rect.topRight()))
        # width is the node width in screen
        width = r.x() - l.x()

        self.set_proxy_mode(width < self._proxy_mode_threshold)

    def set_proxy_mode(self, mode):
        """
        Set whether to draw the node with proxy mode.
        (proxy mode toggles visibility for some qgraphic items in the node.)

        Args:
            mode (bool): true to enable proxy mode.
        """
        if mode is self._proxy_mode:
            return
        self._proxy_mode = mode

        visible = not mode

        # disable overlay item.
        self._x_item.proxy_mode = self._proxy_mode

        # node widget visibility.
        for w in self._widgets.values():
            w.widget().setVisible(visible)

        # port text is not visible in vertical layout.
        if self.layout_direction is LayoutDirectionEnum.VERTICAL.value:
            port_text_visible = False
        else:
            port_text_visible = visible

        # input port text visibility.
        for port, text in self._input_items.items():
            if port.display_name:
                text.setVisible(port_text_visible)

        # output port text visibility.
        for port, text in self._output_items.items():
            if port.display_name:
                text.setVisible(port_text_visible)

        # LoOper: the card is icon-only, so the name text stays hidden (it
        # lives in the tooltip) even when proxy mode toggles back on.
        self._text_item.setVisible(False)
        # ...and the glyph must stay visible at EVERY zoom level: stock proxy
        # mode hid the icon once the node got small on screen, which left a
        # blank square and read as "the icons disappear when zooming out".
        self._icon_item.setVisible(True)

    @property
    def icon(self):
        return self._properties['icon']

    @icon.setter
    def icon(self, path=None):
        self._properties['icon'] = path
        path = path or ICON_NODE_BASE
        pixmap = QtGui.QPixmap(path)
        if pixmap.size().height() > NodeEnum.ICON_SIZE.value:
            pixmap = pixmap.scaledToHeight(
                NodeEnum.ICON_SIZE.value,
                QtCore.Qt.SmoothTransformation
            )
        if pixmap.size().width() > NodeEnum.ICON_SIZE.value:
            pixmap = pixmap.scaledToWidth(
                NodeEnum.ICON_SIZE.value,
                QtCore.Qt.SmoothTransformation
            )
        self._icon_item.setPixmap(pixmap)
        if self.scene():
            self.post_init()

        self.update()

    @AbstractNodeItem.layout_direction.setter
    def layout_direction(self, value=0):
        AbstractNodeItem.layout_direction.fset(self, value)
        self.draw_node()

    @AbstractNodeItem.width.setter
    def width(self, width=0.0):
        # LoOper: keep the node a perfect square.
        AbstractNodeItem.width.fset(self, SQUARE_NODE_SIZE)

    @AbstractNodeItem.height.setter
    def height(self, height=0.0):
        AbstractNodeItem.height.fset(self, SQUARE_NODE_SIZE)

    @AbstractNodeItem.disabled.setter
    def disabled(self, state=False):
        AbstractNodeItem.disabled.fset(self, state)
        for n, w in self._widgets.items():
            w.widget().setDisabled(state)
        self._tooltip_disable(state)
        self._x_item.setVisible(state)

    @AbstractNodeItem.selected.setter
    def selected(self, selected=False):
        AbstractNodeItem.selected.fset(self, selected)
        if selected:
            self.highlight_pipes()

    @AbstractNodeItem.name.setter
    def name(self, name=''):
        AbstractNodeItem.name.fset(self, name)
        if name == self._text_item.toPlainText():
            return
        self._text_item.setPlainText(name)
        if self.scene():
            self.align_label()
        self.update()

    @AbstractNodeItem.color.setter
    def color(self, color=(100, 100, 100, 255)):
        AbstractNodeItem.color.fset(self, color)
        if self.scene():
            self.scene().update()
        self.update()
        
    @AbstractNodeItem.border_color.setter
    def border_color(self, color=(100, 100, 100, 255)):
        AbstractNodeItem.border_color.fset(self, color)
        if self.scene():
            self.scene().update()
        self.update()

    @AbstractNodeItem.text_color.setter
    def text_color(self, color=(100, 100, 100, 255)):
        AbstractNodeItem.text_color.fset(self, color)
        self._set_text_color(color)
        self.update()

    @property
    def text_item(self):
        """
        Get the node name text qgraphics item.

        Returns:
            NodeTextItem: node text object.
        """
        return self._text_item

    @property
    def icon_item(self):
        """
        Get the node icon qgraphics item.

        Returns:
            QtWidgets.QGraphicsPixmapItem: node icon object.
        """
        return self._icon_item

    @property
    def inputs(self):
        """
        Returns:
            list[PortItem]: input port graphic items.
        """
        return list(self._input_items.keys())

    @property
    def outputs(self):
        """
        Returns:
            list[PortItem]: output port graphic items.
        """
        return list(self._output_items.keys())

    def _add_port(self, port):
        """
        Adds a port qgraphics item into the node.

        Args:
            port (PortItem): port item.

        Returns:
            PortItem: port qgraphics item.
        """
        text = QtWidgets.QGraphicsTextItem(port.name, self)
        text.font().setPointSize(8)
        text.setFont(text.font())
        text.setVisible(port.display_name)
        text.setCacheMode(ITEM_CACHE_MODE)
        if port.port_type == PortTypeEnum.IN.value:
            self._input_items[port] = text
        elif port.port_type == PortTypeEnum.OUT.value:
            self._output_items[port] = text
        if self.scene():
            self.post_init()
        return port

    def add_input(self, name='input', multi_port=False, display_name=True,
                  locked=False, painter_func=None):
        """
        Adds a port qgraphics item into the node with the "port_type" set as
        IN_PORT.

        Args:
            name (str): name for the port.
            multi_port (bool): allow multiple connections.
            display_name (bool): display the port name.
            locked (bool): locked state.
            painter_func (function): custom paint function.

        Returns:
            PortItem: input port qgraphics item.
        """
        if painter_func:
            port = CustomPortItem(self, painter_func)
        else:
            port = PortItem(self)
        port.name = name
        port.port_type = PortTypeEnum.IN.value
        port.multi_connection = multi_port
        port.display_name = display_name
        port.locked = locked
        return self._add_port(port)

    def add_output(self, name='output', multi_port=False, display_name=True,
                   locked=False, painter_func=None):
        """
        Adds a port qgraphics item into the node with the "port_type" set as
        OUT_PORT.

        Args:
            name (str): name for the port.
            multi_port (bool): allow multiple connections.
            display_name (bool): display the port name.
            locked (bool): locked state.
            painter_func (function): custom paint function.

        Returns:
            PortItem: output port qgraphics item.
        """
        if painter_func:
            port = CustomPortItem(self, painter_func)
        else:
            port = PortItem(self)
        port.name = name
        port.port_type = PortTypeEnum.OUT.value
        port.multi_connection = multi_port
        port.display_name = display_name
        port.locked = locked
        return self._add_port(port)

    def _delete_port(self, port, text):
        """
        Removes port item and port text from node.

        Args:
            port (PortItem): port object.
            text (QtWidgets.QGraphicsTextItem): port text object.
        """
        port.setParentItem(None)
        text.setParentItem(None)
        self.scene().removeItem(port)
        self.scene().removeItem(text)
        del port
        del text
        # LoOper: removing a port must re-run the layout, otherwise the ports
        # that remain keep their old positions and float off the card edge
        # (seen when the orchestrator switch removes 'tools'/'trace'/'route').
        try:
            if self.scene():
                self.draw_node()
        except Exception:
            pass

    def delete_input(self, port):
        """
        Remove input port from node.

        Args:
            port (PortItem): port object.
        """
        self._delete_port(port, self._input_items.pop(port))

    def delete_output(self, port):
        """
        Remove output port from node.

        Args:
            port (PortItem): port object.
        """
        self._delete_port(port, self._output_items.pop(port))

    def get_input_text_item(self, port_item):
        """
        Args:
            port_item (PortItem): port item.

        Returns:
            QGraphicsTextItem: graphic item used for the port text.
        """
        return self._input_items[port_item]

    def get_output_text_item(self, port_item):
        """
        Args:
            port_item (PortItem): port item.

        Returns:
            QGraphicsTextItem: graphic item used for the port text.
        """
        return self._output_items[port_item]

    @property
    def widgets(self):
        return self._widgets.copy()

    def add_widget(self, widget):
        self._widgets[widget.get_name()] = widget

    def get_widget(self, name):
        widget = self._widgets.get(name)
        if widget:
            return widget
        raise NodeWidgetError('node has no widget "{}"'.format(name))

    def has_widget(self, name):
        return name in self._widgets.keys()

    def from_dict(self, node_dict):
        super(NodeItem, self).from_dict(node_dict)
        custom_prop = node_dict.get('custom') or {}
        for prop_name, value in custom_prop.items():
            prop_widget = self._widgets.get(prop_name)
            if prop_widget:
                prop_widget.set_value(value)

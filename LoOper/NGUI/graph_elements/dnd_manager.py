import logging
from PyQt5.QtCore import QEvent, QTimer
from PyQt5.QtGui import QCursor

logger = logging.getLogger(__name__)

class DragDropManager:
    def __init__(self, parent_widget):
        self.parent = parent_widget

    def install(self):
        try:
            target = getattr(self.parent, 'node_graph_widget', None) or getattr(self.parent, 'view', None)
            viewer = None
            viewport = None
            scene = None
            try:
                ng = getattr(self.parent, 'graph_manager', None)
                node_graph = getattr(ng, 'node_graph', None) if ng else None
                viewer_attr = getattr(node_graph, 'viewer', None) if node_graph else None
                viewer = viewer_attr() if callable(viewer_attr) else viewer_attr
                try:
                    viewport = getattr(viewer, 'viewport', lambda: None)() if viewer else None
                except Exception:
                    viewport = None
                try:
                    scene = getattr(viewer, 'scene', lambda: None)() if viewer else None
                except Exception:
                    scene = None
            except Exception:
                viewer = None
                viewport = None
                scene = None
            if target:
                try:
                    target.setAcceptDrops(True)
                except Exception:
                    pass
                try:
                    target.installEventFilter(self.parent)
                except Exception:
                    pass
            if viewer:
                try:
                    viewer.setAcceptDrops(True)
                except Exception:
                    pass
                try:
                    viewer.installEventFilter(self.parent)
                except Exception:
                    pass
            if viewport:
                try:
                    viewport.setAcceptDrops(True)
                except Exception:
                    pass
                try:
                    viewport.installEventFilter(self.parent)
                except Exception:
                    pass
            if scene:
                try:
                    scene.installEventFilter(self.parent)
                except Exception:
                    pass
            if not (target or viewer):
                return
        except Exception as e:
            logger.error(f"Drag-drop install error: {e}")

    def handle_event(self, obj, event):
        try:
            if event.type() == QEvent.DragEnter:
                mime = event.mimeData()
                if mime and mime.hasFormat('application/x-looper-node-type'):
                    try:
                        view = getattr(self.parent, 'view', None)
                        if view and hasattr(view, 'mapToScene') and hasattr(view, 'mapFromGlobal'):
                            lp = view.mapFromGlobal(QCursor.pos())
                            sp = view.mapToScene(lp)
                            self.parent._pending_drop_scene_pos = [int(sp.x()), int(sp.y())]
                    except Exception:
                        pass
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.DragMove:
                mime = event.mimeData()
                if mime and mime.hasFormat('application/x-looper-node-type'):
                    try:
                        ng = getattr(self.parent, 'graph_manager', None)
                        node_graph = getattr(ng, 'node_graph', None) if ng else None
                        viewer_attr = getattr(node_graph, 'viewer', None) if node_graph else None
                        viewer = viewer_attr() if callable(viewer_attr) else viewer_attr
                        view = getattr(self.parent, 'view', None)
                        if hasattr(obj, 'mapToScene'):
                            sp = obj.mapToScene(event.pos())
                            self.parent._pending_drop_scene_pos = [int(sp.x()), int(sp.y())]
                        elif viewer and hasattr(viewer, 'mapToScene') and getattr(viewer, 'viewport', None) and obj is viewer.viewport():
                            sp = viewer.mapToScene(event.pos())
                            self.parent._pending_drop_scene_pos = [int(sp.x()), int(sp.y())]
                        elif view and hasattr(view, 'mapToScene') and hasattr(view, 'mapFromGlobal'):
                            lp = view.mapFromGlobal(QCursor.pos())
                            sp = view.mapToScene(lp)
                            self.parent._pending_drop_scene_pos = [int(sp.x()), int(sp.y())]
                        elif viewer and hasattr(viewer, 'mapToScene'):
                            lp = viewer.mapFromGlobal(QCursor.pos())
                            sp = viewer.mapToScene(lp)
                            self.parent._pending_drop_scene_pos = [int(sp.x()), int(sp.y())]
                        else:
                            self.parent._pending_drop_scene_pos = None
                    except Exception:
                        self.parent._pending_drop_scene_pos = None
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.GraphicsSceneDragMove:
                mime = event.mimeData()
                if mime and mime.hasFormat('application/x-looper-node-type'):
                    try:
                        sp = event.scenePos()
                        self.parent._pending_drop_scene_pos = [int(sp.x()), int(sp.y())]
                    except Exception:
                        self.parent._pending_drop_scene_pos = None
                    try:
                        event.accept()
                    except Exception:
                        pass
                    return True
            elif event.type() == QEvent.Drop:
                mime = event.mimeData()
                if not (mime and mime.hasFormat('application/x-looper-node-type')):
                    return False
                try:
                    node_type = bytes(mime.data('application/x-looper-node-type')).decode('utf-8')
                    ng = getattr(self.parent, 'graph_manager', None)
                    node_graph = getattr(ng, 'node_graph', None) if ng else None
                    viewer_attr = getattr(node_graph, 'viewer', None) if node_graph else None
                    viewer = viewer_attr() if callable(viewer_attr) else viewer_attr
                    view = getattr(self.parent, 'view', None)
                    drop_pos = getattr(self.parent, '_pending_drop_scene_pos', None)
                    if not drop_pos:
                        try:
                            if hasattr(obj, 'mapToScene'):
                                sp = obj.mapToScene(event.pos())
                                drop_pos = [int(sp.x()), int(sp.y())]
                            elif viewer and hasattr(viewer, 'mapToScene') and getattr(viewer, 'viewport', None) and obj is viewer.viewport():
                                sp = viewer.mapToScene(event.pos())
                                drop_pos = [int(sp.x()), int(sp.y())]
                            elif view and hasattr(view, 'mapToScene') and hasattr(view, 'mapFromGlobal'):
                                lp = view.mapFromGlobal(QCursor.pos())
                                sp = view.mapToScene(lp)
                                drop_pos = [int(sp.x()), int(sp.y())]
                            elif viewer and hasattr(viewer, 'mapToScene'):
                                lp = viewer.mapFromGlobal(QCursor.pos())
                                sp = viewer.mapToScene(lp)
                                drop_pos = [int(sp.x()), int(sp.y())]
                            else:
                                sp_ctx = getattr(self.parent, '_last_context_scene_pos', None)
                                if sp_ctx is not None:
                                    drop_pos = [int(sp_ctx.x()), int(sp_ctx.y())]
                                else:
                                    try:
                                        if view and hasattr(view, 'mapToScene'):
                                            sp = view.mapToScene(view.rect().center())
                                            drop_pos = [int(sp.x()), int(sp.y())]
                                        else:
                                            drop_pos = None
                                    except Exception:
                                        drop_pos = None
                        except Exception:
                            drop_pos = getattr(self.parent, '_pending_drop_scene_pos', None)
                    if not drop_pos:
                        drop_pos = getattr(self.parent, '_pending_drop_scene_pos', None) or [0, 0]
                    try:
                        logger.debug(f"DND computed drop_pos: {drop_pos}")
                    except Exception:
                        pass

                    new_node = None
                    if node_type == 'llm':
                        # Check if this is a shared LLM node with configuration
                        if mime.hasFormat('application/x-looper-llm-config'):
                            import json
                            try:
                                config_data = bytes(mime.data('application/x-looper-llm-config')).decode('utf-8')
                                config = json.loads(config_data)
                                chain_file = config.get('chain_file', '')
                                llm_node_data = config.get('llm_node_data', {})
                                new_node = self.parent.node_operations.add_llm_node_with_shared_file(
                                    chain_file, llm_node_data
                                )
                            except Exception as e:
                                logger.error(f"Shared LLM node drop error: {e}")
                                new_node = self.parent.node_operations.add_llm_node(pos=drop_pos)
                        else:
                            new_node = self.parent.node_operations.add_llm_node(pos=drop_pos)
                    elif node_type == 'conditional':
                        new_node = self.parent.node_operations.add_conditional_node(pos=drop_pos)
                    elif node_type == 'chain_import':
                        if mime.hasFormat('application/x-looper-chain-file'):
                            fp = bytes(mime.data('application/x-looper-chain-file')).decode('utf-8')
                            new_node = self.parent.node_operations.add_chain_import_from_file(fp, pos=drop_pos)
                        else:
                            new_node = self.parent.node_operations.add_chain_import_node(pos=drop_pos)
                    elif node_type == 'code':
                        # Check if this is a shared code node with configuration
                        if mime.hasFormat('application/x-looper-code-config'):
                            import json
                            try:
                                config_data = bytes(mime.data('application/x-looper-code-config')).decode('utf-8')
                                config = json.loads(config_data)
                                chain_file = config.get('chain_file', '')
                                code_node_data = config.get('code_node_data', {})
                                new_node = self.parent.node_operations.add_code_node_with_shared_file(
                                    chain_file, code_node_data
                                )
                            except Exception as e:
                                logger.error(f"Shared code node drop error: {e}")
                                new_node = self.parent.node_operations.add_code_node(pos=drop_pos)
                        else:
                            new_node = self.parent.node_operations.add_code_node(pos=drop_pos)
                    elif node_type == 'context':
                        # Check if this is a shared context node with configuration
                        if mime.hasFormat('application/x-looper-context-config'):
                            import json
                            try:
                                config_data = bytes(mime.data('application/x-looper-context-config')).decode('utf-8')
                                config = json.loads(config_data)
                                chain_file = config.get('chain_file', '')
                                context_node_data = config.get('context_node_data', {})
                                new_node = self.parent.node_operations.add_context_node_with_shared_file(
                                    chain_file, context_node_data
                                )
                            except Exception as e:
                                logger.error(f"Shared context node drop error: {e}")
                                new_node = self.parent.node_operations.add_context_node(pos=drop_pos)
                        else:
                            new_node = self.parent.node_operations.add_context_node(pos=drop_pos)
                    elif node_type == 'input':
                        new_node = self.parent.node_operations.add_input_node(pos=drop_pos)
                    elif node_type == 'handle':
                        new_node = self.parent.node_operations.add_handle_node(pos=drop_pos)
                    elif node_type == 'form_filler':
                        new_node = self.parent.node_operations.add_form_filler_node(pos=drop_pos)
                    elif node_type == 'mcp':
                        new_node = self.parent.node_operations.add_mcp_node(pos=drop_pos)
                    elif node_type == 'output':
                        new_node = self.parent.node_operations.add_output_node(pos=drop_pos)
                    elif node_type == 'web_sequence':
                        try:
                            if mime.hasFormat('application/x-looper-web-session-file'):
                                fp = bytes(mime.data('application/x-looper-web-session-file')).decode('utf-8')
                                new_node = self.parent.node_operations.add_web_sequence_from_file(fp, pos=drop_pos)
                            else:
                                new_node = self.parent.node_operations.add_blank_web_sequence(pos=drop_pos)
                        except Exception as e:
                            logger.error(f"Web sequence drop error: {e}")
                            new_node = self.parent.node_operations.add_blank_web_sequence(pos=drop_pos)
                    elif node_type == 'sequence':
                        try:
                            if mime.hasFormat('application/x-looper-sequence-file'):
                                fp = bytes(mime.data('application/x-looper-sequence-file')).decode('utf-8')
                                new_node = self.parent.node_operations.add_sequence_from_file(fp, pos=drop_pos)
                            else:
                                self.parent.record_new_sequence()
                        except Exception as e:
                            logger.error(f"Sequence drop error: {e}")
                    else:
                        logger.info(f"Unknown node type: {node_type}")

                    try:
                        if new_node and hasattr(new_node, 'set_pos'):
                            sx, sy = self.parent.graph_manager._snap_point(drop_pos[0], drop_pos[1])
                            def _apply_pos():
                                try:
                                    r = getattr(new_node, 'rect', None)
                                    if r:
                                        w = int(getattr(r, 'width', lambda: r.width())()) if callable(getattr(r, 'width', None)) else int(r.width())
                                        h = int(getattr(r, 'height', lambda: r.height())()) if callable(getattr(r, 'height', None)) else int(r.height())
                                    else:
                                        w, h = 140, 60
                                    new_node.set_pos(int(sx - w * 0.5), int(sy - h * 0.5))
                                    try:
                                        fx, fy = new_node.pos()
                                        logger.debug(f"DND final pos applied: target=({sx},{sy}), center_adjusted=({int(sx - w*0.5)},{int(sy - h*0.5)}), actual=({fx},{fy})")
                                    except Exception:
                                        pass
                                except Exception:
                                    try:
                                        new_node.set_pos(sx, sy)
                                        try:
                                            fx, fy = new_node.pos()
                                            logger.debug(f"DND fallback pos applied: target=({sx},{sy}), actual=({fx},{fy})")
                                        except Exception:
                                            pass
                                    except Exception:
                                        pass
                                try:
                                    self.parent._pending_drop_scene_pos = None
                                except Exception:
                                    pass
                            try:
                                QTimer.singleShot(0, _apply_pos)
                                QTimer.singleShot(25, _apply_pos)
                            except Exception:
                                _apply_pos()
                    except Exception:
                        pass

                except Exception as e:
                    logger.error(f"Drop handling error: {e}")
                event.acceptProposedAction()
                return True
            elif event.type() == QEvent.GraphicsSceneDrop:
                mime = event.mimeData()
                if not (mime and mime.hasFormat('application/x-looper-node-type')):
                    return False
                try:
                    node_type = bytes(mime.data('application/x-looper-node-type')).decode('utf-8')
                    drop_pos = getattr(self.parent, '_pending_drop_scene_pos', None)
                    if not drop_pos:
                        try:
                            sp = event.scenePos()
                            drop_pos = [int(sp.x()), int(sp.y())]
                        except Exception:
                            drop_pos = [0, 0]

                    new_node = None
                    if node_type == 'llm':
                        if mime.hasFormat('application/x-looper-llm-config'):
                            import json
                            try:
                                config_data = bytes(mime.data('application/x-looper-llm-config')).decode('utf-8')
                                config = json.loads(config_data)
                                chain_file = config.get('chain_file', '')
                                llm_node_data = config.get('llm_node_data', {})
                                new_node = self.parent.node_operations.add_llm_node_with_shared_file(chain_file, llm_node_data)
                            except Exception as e:
                                logger.error(f"Shared LLM node drop error: {e}")
                                new_node = self.parent.node_operations.add_llm_node(pos=drop_pos)
                        else:
                            new_node = self.parent.node_operations.add_llm_node(pos=drop_pos)
                    elif node_type == 'conditional':
                        new_node = self.parent.node_operations.add_conditional_node(pos=drop_pos)
                    elif node_type == 'chain_import':
                        if mime.hasFormat('application/x-looper-chain-file'):
                            fp = bytes(mime.data('application/x-looper-chain-file')).decode('utf-8')
                            new_node = self.parent.node_operations.add_chain_import_from_file(fp, pos=drop_pos)
                        else:
                            new_node = self.parent.node_operations.add_chain_import_node(pos=drop_pos)
                    elif node_type == 'code':
                        if mime.hasFormat('application/x-looper-code-config'):
                            import json
                            try:
                                config_data = bytes(mime.data('application/x-looper-code-config')).decode('utf-8')
                                config = json.loads(config_data)
                                chain_file = config.get('chain_file', '')
                                code_node_data = config.get('code_node_data', {})
                                new_node = self.parent.node_operations.add_code_node_with_shared_file(chain_file, code_node_data)
                            except Exception as e:
                                logger.error(f"Shared code node drop error: {e}")
                                new_node = self.parent.node_operations.add_code_node(pos=drop_pos)
                        else:
                            new_node = self.parent.node_operations.add_code_node(pos=drop_pos)
                    elif node_type == 'context':
                        if mime.hasFormat('application/x-looper-context-config'):
                            import json
                            try:
                                config_data = bytes(mime.data('application/x-looper-context-config')).decode('utf-8')
                                config = json.loads(config_data)
                                chain_file = config.get('chain_file', '')
                                context_node_data = config.get('context_node_data', {})
                                new_node = self.parent.node_operations.add_context_node_with_shared_file(chain_file, context_node_data)
                            except Exception as e:
                                logger.error(f"Shared context node drop error: {e}")
                                new_node = self.parent.node_operations.add_context_node(pos=drop_pos)
                        else:
                            new_node = self.parent.node_operations.add_context_node(pos=drop_pos)
                    elif node_type == 'input':
                        new_node = self.parent.node_operations.add_input_node(pos=drop_pos)
                    elif node_type == 'handle':
                        new_node = self.parent.node_operations.add_handle_node(pos=drop_pos)
                    elif node_type == 'form_filler':
                        new_node = self.parent.node_operations.add_form_filler_node(pos=drop_pos)
                    elif node_type == 'mcp':
                        new_node = self.parent.node_operations.add_mcp_node(pos=drop_pos)
                    elif node_type == 'output':
                        new_node = self.parent.node_operations.add_output_node(pos=drop_pos)
                    elif node_type == 'web_sequence':
                        try:
                            if mime.hasFormat('application/x-looper-web-session-file'):
                                fp = bytes(mime.data('application/x-looper-web-session-file')).decode('utf-8')
                                new_node = self.parent.node_operations.add_web_sequence_from_file(fp, pos=drop_pos)
                            else:
                                new_node = self.parent.node_operations.add_blank_web_sequence(pos=drop_pos)
                        except Exception as e:
                            logger.error(f"Web sequence drop error: {e}")
                            new_node = self.parent.node_operations.add_blank_web_sequence(pos=drop_pos)
                    elif node_type == 'sequence':
                        try:
                            if mime.hasFormat('application/x-looper-sequence-file'):
                                fp = bytes(mime.data('application/x-looper-sequence-file')).decode('utf-8')
                                new_node = self.parent.node_operations.add_sequence_from_file(fp, pos=drop_pos)
                            else:
                                self.parent.record_new_sequence()
                        except Exception as e:
                            logger.error(f"Sequence drop error: {e}")
                    else:
                        logger.info(f"Unknown node type: {node_type}")

                    try:
                        if new_node and hasattr(new_node, 'set_pos'):
                            sx, sy = self.parent.graph_manager._snap_point(drop_pos[0], drop_pos[1])
                            def _apply_pos():
                                try:
                                    r = getattr(new_node, 'rect', None)
                                    if r:
                                        w = int(getattr(r, 'width', lambda: r.width())()) if callable(getattr(r, 'width', None)) else int(r.width())
                                        h = int(getattr(r, 'height', lambda: r.height())()) if callable(getattr(r, 'height', None)) else int(r.height())
                                    else:
                                        w, h = 140, 60
                                    new_node.set_pos(int(sx - w * 0.5), int(sy - h * 0.5))
                                except Exception:
                                    try:
                                        new_node.set_pos(sx, sy)
                                    except Exception:
                                        pass
                                try:
                                    self.parent._pending_drop_scene_pos = None
                                except Exception:
                                    pass
                            try:
                                QTimer.singleShot(0, _apply_pos)
                                QTimer.singleShot(25, _apply_pos)
                            except Exception:
                                _apply_pos()
                    except Exception:
                        pass

                except Exception as e:
                    logger.error(f"GraphicsScene drop handling error: {e}")
                try:
                    event.accept()
                except Exception:
                    pass
                return True
        except Exception as e:
            logger.debug(f"DND handle_event error: {e}")
        return False

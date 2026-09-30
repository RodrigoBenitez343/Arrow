import json
import os
import re
import threading
import time
from PyQt5.QtWidgets import QMessageBox, QFileDialog, QInputDialog
from PyQt5.QtCore import QObject, pyqtSignal
from ...dialogs.web_sequence_dialogs import WebSequencePropertiesDialog
from .utils import get_logger

logger = get_logger(__name__)


class _WebRecordingBridge(QObject):
    """Main-thread signal bridge for background web recording completion.

    QTimer.singleShot from a plain worker thread never fires (the worker has
    no Qt event loop), so the worker emits a Qt signal instead: queued
    delivery runs the slot in the main thread where the bridge was created.
    """

    done = pyqtSignal(object, str)
    failed = pyqtSignal(str)


class WebSequenceOperationsMixin:
    def add_web_sequence(self, pos=None):
        """Add a previously recorded web session node to the graph."""
        logger.info(f"Adding web sequence node at position: {pos}")
        try:
            file_path, selected_filter = QFileDialog.getOpenFileName(
                self.parent_widget,
                "Select Web Session File",
                getattr(self.parent_widget, 'web_sequences_folder', os.path.join(os.getcwd(), 'web_sequences')),
                "JSON Files (*.json)"
            )

            if file_path:
                logger.debug(f"Selected web session file: {file_path}")
                session_filename = os.path.basename(file_path)
                node = self.parent_widget.graph_manager.create_node(
                    'web_sequence.WebSequenceNode',
                    name=session_filename,
                    pos=pos
                )

                if node:
                    node.set_property('session_file', session_filename)
                    node.set_web_sequence_data(0, {
                        'session_file': session_filename,
                        'loop_count': 1,
                        'extra_delay': 1.0,
                    })
                    logger.info(f"Successfully added web sequence: {session_filename} (from {file_path})")
                    return node
                else:
                    logger.error("Failed to create web sequence node")
            else:
                logger.debug("No file selected for web sequence")

        except Exception as e:
            logger.error(f"Error adding web sequence: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to add web sequence: {str(e)}"
            )

        return None

    def add_blank_web_sequence(self, pos=None):
        """Create an unassigned web sequence node (no session file yet).

        Used by drag-and-drop from the node toolbar: the node appears on the
        graph immediately; the user assigns a session via the Record button,
        the edit dialog, or the file picker.
        """
        logger.info(f"Adding blank web sequence node at position: {pos}")
        try:
            node = self.parent_widget.graph_manager.create_node(
                'web_sequence.WebSequenceNode',
                name='web_sequence',
                pos=pos
            )
            if node:
                node.set_property('session_file', '')
                node.set_web_sequence_data(0, {'session_file': ''})
                return node
            return None
        except Exception as e:
            logger.error(f"Error adding blank web sequence: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to add web sequence: {str(e)}"
            )
            return None

    def add_new_web_sequence(self, pos=None):
        """Add a new web sequence node and immediately record a session for it."""
        logger.info(f"Adding new web sequence node at position: {pos}")
        try:
            node = self.parent_widget.graph_manager.create_node(
                'web_sequence.WebSequenceNode',
                name='web_sequence',
                pos=pos
            )
            if node:
                self.record_web_session(node)
                return node
        except Exception as e:
            logger.error(f"Error adding new web sequence: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to add new web sequence: {str(e)}"
            )
        return None

    def add_web_sequence_from_file(self, file_path: str, pos=None):
        """Create a web sequence node from an explicit session file path."""
        try:
            if not file_path:
                return None
            session_filename = os.path.basename(file_path)
            node = self.parent_widget.graph_manager.create_node(
                'web_sequence.WebSequenceNode',
                name=session_filename,
                pos=pos
            )
            if node:
                node.set_property('session_file', session_filename)
                node.set_web_sequence_data(0, {
                    'session_file': session_filename,
                    'loop_count': 1,
                    'extra_delay': 1.0,
                })
                return node
            return None
        except Exception as e:
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to add web sequence: {str(e)}"
            )
            return None

    def edit_web_sequence(self, node):
        """Edit a Web Sequence node's properties via dialog."""
        try:
            logger.info(f"Editing Web Sequence node: {node.id}")
            current_config = {}
            if hasattr(node, 'get_web_sequence_config'):
                current_config = node.get_web_sequence_config() or {}
            else:
                current_config = {
                    'session_file': node.get_property('session_file') or '',
                    'headless': False,
                    'speed': 1.0,
                    'native_actions': True,
                    'loop_count': int(node.get_property('loop_count') or 1),
                    'extra_delay': float(node.get_property('extra_delay') or 1.0),
                }

            dialog = WebSequencePropertiesDialog(current_config, self.parent_widget)
            if dialog.exec_() == dialog.Accepted:
                if getattr(dialog, '_record_requested', False):
                    # User pressed "Record Web Session…" - start recording and
                    # attach the resulting session file to the node.
                    logger.info("Web sequence dialog requested recording")
                    self.record_web_session(node)
                    return
                props = dialog.get_properties()
                logger.debug(f"Web sequence dialog accepted with properties: {props}")
                node.set_property('headless', 'true' if props.get('headless', False) else 'false')
                node.set_property('speed', str(props.get('speed', 1.0)))
                node.set_property('native_actions', 'true' if props.get('native_actions', True) else 'false')
                node.set_property('loop_count', str(props.get('loop_count', 1)))
                node.set_property('extra_delay', str(props.get('extra_delay', 1.0)))
                node.set_property('extract_items', json.dumps(props.get('extract_items', [])))
                node.set_property('description', str(props.get('description', '') or ''))
                logger.info(f"Successfully edited Web Sequence node: {node.id}")
            else:
                logger.debug("Web sequence dialog cancelled")
        except Exception as e:
            logger.error(f"Error editing Web Sequence node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to edit web sequence node: {str(e)}"
            )

    @staticmethod
    def _is_default_web_sequence_name(name):
        """True when the node label is still auto-generated: blank, the
        'web_sequence' placeholder, or a 'web_session_YYYYMMDD-HHMMSS.json'
        timestamp name.  Auto-generated labels gate the once-only naming
        prompt and the legacy timestamp-file migration."""
        name = (name or '').strip()
        if not name or name == 'web_sequence':
            return True
        return bool(re.match(r'^web_session_\d{8}-\d{6}\.json$', name))

    @staticmethod
    def _sanitize_session_base(label):
        """Label text -> a safe filename stem: no .json, no Windows-invalid
        characters, no trailing dots/spaces."""
        base = re.sub(r'\.json$', '', label or '', flags=re.IGNORECASE).strip()
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', base).strip(' .')
        return base or 'web_sequence'

    def _session_filename_for_label(self, label, target_dir, exclude=''):
        """A unique session filename derived from a node label.

        'Gmail login' -> 'Gmail login.json'.  When the derived name already
        exists in the folder (another node's file), a numeric suffix is
        appended so two nodes NEVER share a session file.  ``exclude`` is
        the node's own current file - it is always accepted as-is.
        """
        base = self._sanitize_session_base(label)
        candidate = base + '.json'
        if candidate == exclude:
            return candidate
        if not os.path.exists(os.path.join(target_dir, candidate)):
            return candidate
        n = 1
        while True:
            alt = f"{base}_{n}.json"
            if alt == exclude:
                return alt
            if not os.path.exists(os.path.join(target_dir, alt)):
                return alt
            n += 1

    def _session_file_referenced(self, filename):
        """True when any node in the current graph still binds *filename*."""
        try:
            nodes = self.parent_widget.graph_manager.get_all_nodes()
        except Exception:
            return True  # cannot verify - keep the file
        for n in nodes or []:
            try:
                if os.path.basename(n.get_property('session_file') or '') == filename:
                    return True
            except Exception:
                continue
        return False


    def _chain_scope_key(self):
        """The app-wide web browser scope for recordings.

        Delegates to the ConfigManager, which resolves EVERY chain - saved or
        not - to the single shared scope ("default"), so recordings land in
        the one durable profile whose cookies/logins survive app restarts.
        The fallback below mirrors that for the (rare) case no ConfigManager
        is reachable.
        """
        try:
            cfg_mgr = getattr(self.parent_widget, 'config_manager', None)
            if cfg_mgr is not None:
                key = getattr(cfg_mgr, '_chain_scope_key', None)
                if key:
                    return key
        except Exception:
            pass
        key = getattr(self, '_fallback_web_chain_scope', None)
        if not key:
            key = 'default'
            self._fallback_web_chain_scope = key
        return key

    def record_web_session(self, node=None):
        """Record a web session and attach it to the given node (or create one).

        A session file is named after the node's label ("Gmail login" ->
        "Gmail login.json"), each node owns its own file, and re-recording
        OVERWRITES that same file in place: no new session, no shared
        session, no rename prompt.  The name prompt fires only when the node
        has never been named (first recording on a blank node, or a legacy
        node still carrying a timestamp label + timestamp file); a legacy
        timestamp file on an already-named node is silently migrated to the
        label-derived name.

        The browser opens blank (no start URL - only user interactions are
        recorded: clicks, typing, keys.  URL changes are never captured, so
        dynamic pages replay without hard navigations).  Recording runs in a
        background thread and stops
        on ESC (in the page or the terminal) or when the browser window closes.
        The browser is scoped to the CURRENT CHAIN: its own durable profile
        and tab snapshot, so closing it and re-recording for this chain later
        resumes the chain's exact browser story.  Completion is marshalled
        back to the main thread via a Qt signal bridge (never
        QTimer.singleShot from the worker - it would never fire).
        """
        if getattr(self, '_web_recording_active', False):
            logger.warning("A web recording is already in progress - ignoring duplicate request")
            return
        self._web_recording_active = True

        logger.info(f"Starting web session recording for node: {node.id if node else 'new'}")
        try:
            chain_key = self._chain_scope_key()
            logger.info(f"Web recording chain scope: {chain_key}")

            target_dir = getattr(
                self.parent_widget, 'web_sequences_folder',
                os.path.join(os.getcwd(), 'web_sequences')
            )
            os.makedirs(target_dir, exist_ok=True)

            main_window = self._find_main_window()
            if main_window is not None:
                try:
                    main_window.showMinimized()
                except Exception:
                    pass

            # Session files are named after the node's label ("Gmail login" ->
            # "Gmail login.json") - never timestamps - and each node owns its
            # own file.  Re-recording OVERWRITES that same file in place: no
            # new session, no shared session, no rename prompt.
            existing_session = ''
            node_label = ''
            if node is not None:
                existing_session = (node.get_property('session_file') or '').strip()
                existing_session = os.path.basename(existing_session)
                node_label = (node.name() or '').strip()

            # Ask for a label only when the node has NEVER been named: a first
            # recording on a blank node, or a legacy node still carrying a
            # timestamp label + timestamp file.  Re-recording an already-named
            # node never re-prompts.  The field starts EMPTY (a filename
            # default invites users to type "name.json") and a trailing
            # ".json" is stripped so a filename-style answer never leaks the
            # extension into the label.
            pending_name = None
            needs_name = node is not None and (
                not existing_session
                or (
                    self._is_default_web_sequence_name(existing_session)
                    and self._is_default_web_sequence_name(node_label)
                )
            )
            if needs_name:
                name, ok = QInputDialog.getText(
                    self.parent_widget,
                    "Name Web Sequence",
                    "Give this web sequence a name:\n"
                    "(e.g. \"Gmail login\" - no .json needed)",
                )
                if not ok or not name.strip():
                    logger.info("Web recording cancelled - no node name given")
                    self._web_recording_active = False
                    return
                pending_name = re.sub(r'\.json$', '', name.strip(), flags=re.IGNORECASE).strip()
                if not pending_name:
                    logger.info("Web recording cancelled - no node name given")
                    self._web_recording_active = False
                    return
            self._pending_web_recording_name = pending_name

            # Choose the target file:
            # - a freshly chosen label -> its own label-derived file (deduped
            #   so two nodes never share a session file);
            # - an existing session -> overwrite it in place; a legacy
            #   timestamp file on an already-named node is migrated to the
            #   label-derived name (the orphan is removed after success);
            # - no node at all -> timestamp fallback.
            if pending_name:
                output_path = os.path.join(
                    target_dir,
                    self._session_filename_for_label(pending_name, target_dir),
                )
                logger.info(f"Recording new web session as: {output_path}")
            elif existing_session:
                if (
                    self._is_default_web_sequence_name(existing_session)
                    and node_label
                    and not self._is_default_web_sequence_name(node_label)
                ):
                    output_path = os.path.join(
                        target_dir,
                        self._session_filename_for_label(
                            node_label, target_dir, exclude=existing_session
                        ),
                    )
                    logger.info(
                        f"Re-recording web session (migrating from {existing_session}): {output_path}"
                    )
                else:
                    output_path = os.path.join(target_dir, existing_session)
                    logger.info(f"Re-recording web session in place: {output_path}")
            else:
                output_path = os.path.join(
                    target_dir,
                    time.strftime('web_session_%Y%m%d-%H%M%S.json')
                )

            if node is not None:
                # Bind the node to the new file NOW: a chain save/playback that
                # races an in-flight recording must not silently replay the old
                # session.  If the recording later fails, the old binding is
                # restored in _show_web_recording_error.
                self._pending_web_recording_node = node
                self._pending_web_recording_old_session = node.get_property('session_file')
                node.set_property('session_file', os.path.basename(output_path))

            # Bridge is created and connected on the main thread, so its slots
            # run there via queued delivery.  Keep a reference until delivery.
            bridge = _WebRecordingBridge()
            bridge.done.connect(self._on_web_recording_done)
            bridge.failed.connect(self._show_web_recording_error)
            self._web_recording_bridge = bridge

            def recording_thread():
                try:
                    # Absolute top-level import: `player` is on sys.path both in
                    # source mode (LoOper/ is sys.path[0]) and in frozen builds.
                    # Relative imports cannot cross the top-level NGUI package.
                    from player.web import record_web_session as _record
                    # Stamp the user-facing name into the session metadata: the
                    # prompt's answer wins; re-recording an already-named node
                    # reuses its custom label so the library keeps showing it.
                    session_name = pending_name or (
                        node.name()
                        if node is not None
                        and not self._is_default_web_sequence_name(node.name())
                        else None
                    )
                    saved = _record(
                        output=output_path,
                        chain_key=chain_key,
                        name=session_name,
                    )
                    bridge.done.emit(node, str(saved))
                except Exception as exc:
                    logger.error(f"Web recording failed: {exc}")
                    bridge.failed.emit(str(exc))

            threading.Thread(target=recording_thread, daemon=True).start()
        except Exception as e:
            self._web_recording_active = False
            logger.error(f"Error starting web recording: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to start web recording: {str(e)}"
            )

    def _on_web_recording_done(self, node, session_path):
        """Attach the recorded session to the node and refresh the UI (main thread)."""
        chosen = getattr(self, '_pending_web_recording_name', None)
        old_session = getattr(self, '_pending_web_recording_old_session', None)
        self._web_recording_active = False
        self._pending_web_recording_node = None
        self._pending_web_recording_old_session = None
        self._pending_web_recording_name = None
        try:
            # Absolute top-level import - same reasoning as the recording thread.
            from player.json_cache import clear_cache
            clear_cache()
        except Exception:
            pass

        filename = os.path.basename(session_path)
        logger.info(f"Web recording finished: {filename}")
        # Restore the main window FIRST: the empty-session warning below is a
        # blocking modal, and recording minimized the window - a dialog on a
        # hidden, frozen window reads as "nothing happened".
        main_window = self._find_main_window()
        if main_window is not None:
            try:
                main_window.showNormal()
            except Exception:
                pass
            try:
                if hasattr(main_window, '_restore_window'):
                    main_window._restore_window()
            except Exception:
                pass
        # A recording still ends with 0 replayable actions only when the user
        # neither navigated to a real site nor interacted with one (a pure
        # cold-start navigation is saved as one 'Open page' action).  Say so
        # NOW, while the browser is still open, instead of letting the node
        # silently replay an empty session later.
        try:
            from player.web.events import load_session
            recorded = load_session(session_path)
            action_count = len(recorded.get("actions") or [])
        except Exception:
            action_count = -1
        if action_count == 0:
            QMessageBox.warning(
                self.parent_widget,
                "Empty Web Sequence",
                "The recording saved no replayable actions.\n\n"
                "Navigate the browser to a real website and interact with it "
                "before pressing ESC. Typing in the address bar / new-tab "
                "search box is browser chrome: it is not replayed as keys - "
                "only the page you land on is saved (as one 'Open page' "
                "action), and this recording did not even land on a site.",
            )
        elif action_count > 0:
            logger.info(
                "Web recording saved %d actions -> %s", action_count, filename
            )
        if node is not None:
            try:
                node.set_property('session_file', filename)
                # A name chosen in the pre-record prompt wins; otherwise keep
                # the node's current label (set_web_sequence_data only fills
                # blank/default labels with the session filename).  Never
                # clobber a custom label with a timestamp filename, or the
                # next re-record would re-prompt for a name.
                if chosen:
                    node.set_name(chosen)
                node.set_web_sequence_data(0, {'session_file': filename})
            except Exception as exc:
                logger.error(f"Failed to attach web session to node: {exc}")

        # A re-record that migrated a node away from a legacy timestamp file
        # leaves that file orphaned - drop it unless another node still binds
        # it (existing references must not break).
        if (
            old_session
            and old_session != filename
            and re.match(r'^web_session_\d{8}-\d{6}\.json$', old_session)
            and not self._session_file_referenced(old_session)
        ):
            try:
                target_dir = getattr(
                    self.parent_widget, 'web_sequences_folder',
                    os.path.join(os.getcwd(), 'web_sequences')
                )
                os.remove(os.path.join(target_dir, old_session))
                logger.info(f"Removed orphaned web session file: {old_session}")
            except Exception as exc:
                logger.debug(
                    f"Could not remove orphaned web session file {old_session}: {exc}"
                )

        # Refresh sequences toolbar on the main thread.
        try:
            self.parent_widget.refresh_sequences_toolbar()
        except Exception:
            pass

    def _show_web_recording_error(self, error):
        """Show a web recording error and restore the window (main thread)."""
        self._web_recording_active = False
        # The recording never produced a file - put the node's previous session
        # binding back so playback keeps working instead of pointing at nothing.
        node = getattr(self, '_pending_web_recording_node', None)
        old = getattr(self, '_pending_web_recording_old_session', None)
        if node is not None and old is not None:
            try:
                node.set_property('session_file', old)
            except Exception:
                pass
        self._pending_web_recording_node = None
        self._pending_web_recording_old_session = None
        self._pending_web_recording_name = None
        logger.error(f"Web recording error: {error}")
        main_window = self._find_main_window()
        if main_window is not None:
            try:
                main_window.showNormal()
            except Exception:
                pass
            try:
                if hasattr(main_window, '_restore_window'):
                    main_window._restore_window()
            except Exception:
                pass
        QMessageBox.critical(
            self.parent_widget,
            "Web Recording Error",
            f"Failed to record web session: {error}"
        )

    def _find_main_window(self):
        """Traverse up the widget hierarchy to find the MainWindow."""
        curr = self.parent_widget
        while curr is not None:
            if hasattr(curr, 'execute_chain_config') and hasattr(curr, 'start_recording'):
                return curr
            next_p = None
            if hasattr(curr, 'parentWidget') and callable(curr.parentWidget):
                try:
                    next_p = curr.parentWidget()
                except Exception:
                    next_p = None
            if next_p is None and hasattr(curr, 'parent') and callable(curr.parent):
                try:
                    next_p = curr.parent()
                except Exception:
                    next_p = None
            if next_p is None or next_p == curr:
                break
            curr = next_p
        return None

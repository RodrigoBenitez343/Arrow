# graphui/main_window.py

import sys
import json
import os
import time
import threading
import logging
import traceback
import subprocess
from datetime import datetime
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QMenuBar, QAction, QFileDialog, QInputDialog, QMessageBox
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QIcon
from .constants import DARK_GREY, MEDIUM_GREY, TEXT_COLOR
from pynput import mouse, keyboard
from .graph_view import GraphViewWidget
from recorder.element_recorder import ElementRecorder
from .scheduler_service import SchedulerService
from .dialogs.scheduler_dialogs import SchedulerManagerDialog
from .dialogs import AISettingsDialog

# Configure logging to output to both file and stdout
# Use a specific logger name to avoid conflicts with recorder module
logger = logging.getLogger('graphui.main_window')
logger.setLevel(logging.DEBUG)

# Only add handlers if they don't already exist
if not logger.handlers:
    file_handler = logging.FileHandler('graphui_debug.log')
    console_handler = logging.StreamHandler(sys.stdout)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # Prevent propagation to avoid duplicate logs
    logger.propagate = False

class MainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        logger.info("Initializing MainWindow")
        try:
            super().__init__()
            self.setWindowTitle("LoOperV2")
            self.setGeometry(100, 100, 1200, 800)
            
            # Set window icon
            icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "LoOper.ico")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
                logger.debug(f"Window icon set from: {icon_path}")
            else:
                logger.warning(f"Icon file not found at: {icon_path}")
            
            logger.debug("MainWindow basic setup complete, calling setup_ui")
            self.setup_ui()
            
            # Initialize Scheduler Service
            try:
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
                self.scheduler_service = SchedulerService(project_root=project_root)
                self.scheduler_service.start()
                logger.info("SchedulerService started successfully")
            except Exception as se:
                logger.error(f"Failed to start SchedulerService: {se}")
            
            logger.info("MainWindow initialization completed successfully")
        except Exception as e:
            logger.error(f"Error initializing MainWindow: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
    
    def setup_ui(self):
        """Setup the UI for the main window"""
        logger.debug("Setting up UI components")
        try:
            self.central_widget = QWidget()
            self.central_widget.setStyleSheet(f"""
                QWidget {{
                    background-color: {DARK_GREY};
                    color: {TEXT_COLOR};
                    border: none;
                }}
            """)
            self.setCentralWidget(self.central_widget)
            
            self.layout = QVBoxLayout(self.central_widget)
            self.layout.setContentsMargins(0, 0, 0, 0)
            self.layout.setSpacing(0)
            
            logger.debug("Creating GraphViewWidget")
            self.graph_view = GraphViewWidget(self)
            self.layout.addWidget(self.graph_view)
            
            logger.debug("Creating menu bar")
            self.create_menu_bar()
            logger.debug("UI setup completed successfully")
        except Exception as e:
            logger.error(f"Error setting up UI: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
    
    def create_menu_bar(self):
        """Create the application menu bar"""
        menu_bar = self.menuBar()
        
        # File menu
        file_menu = menu_bar.addMenu("&File")
        
        new_action = QAction("&New Chain", self)
        new_action.triggered.connect(self.new_chain)
        file_menu.addAction(new_action)
        
        open_action = QAction("&Open Chain", self)
        open_action.triggered.connect(self.open_chain)
        file_menu.addAction(open_action)
        
        save_action = QAction("&Save Chain", self)
        save_action.triggered.connect(self.save_chain)
        file_menu.addAction(save_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("&Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        #settings menu
        settings_menu = menu_bar.addMenu("&Settings")
        
        ai_settings_action = QAction("&AI Configuration", self)
        ai_settings_action.triggered.connect(self.open_ai_settings)
        settings_menu.addAction(ai_settings_action)
        
        '''# Edit menu
        edit_menu = menu_bar.addMenu("&Edit")
        # Add edit actions here
        
        # View menu
        view_menu = menu_bar.addMenu("&View")
        # Add view actions here''' #deprecated items no need for this buttons
        
        # Run menu
        run_menu = menu_bar.addMenu("&Run")
        
        run_chain_action = QAction("&Run Chain", self)
        run_chain_action.triggered.connect(self.run_chain)
        run_menu.addAction(run_chain_action)
        
        record_sequence_action = QAction("&Record Sequence", self)
        record_sequence_action.triggered.connect(self.record_sequence)
        run_menu.addAction(record_sequence_action)
        
        # Scheduler menu
        scheduler_menu = menu_bar.addMenu("&Scheduler")
        manage_sched_action = QAction("&Manage Schedules", self)
        manage_sched_action.triggered.connect(self.open_scheduler_manager)
        scheduler_menu.addAction(manage_sched_action)
    
    def new_chain(self):
        """Clears the graph to start a new chain."""
        self.graph_view.clear_graph()

    def record_sequence(self):
        """Starts the recording process with user input for sequence name"""
        logger.info("Record sequence requested")
        try:
            # Get sequence name from user
            logger.debug("Showing sequence name input dialog")
            sequence_name, ok = QInputDialog.getText(
                self,
                "Record New Sequence",
                "Enter sequence name:",
                text="new_sequence"
            )
            
            if ok and sequence_name.strip():
                logger.info(f"Starting recording for sequence: {sequence_name.strip()}")
                self.start_recording(sequence_name.strip())
            elif ok:
                logger.warning("User provided empty sequence name")
                QMessageBox.warning(self, "Invalid Input", "Please enter a valid sequence name.")
            else:
                logger.info("User cancelled sequence recording")
        except Exception as e:
            logger.error(f"Error in record_sequence: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            QMessageBox.critical(self, "Recording Error", f"Failed to start recording: {str(e)}")

    def run_chain(self):
        """Runs the current automation chain by saving it to a temporary file and passing it to the player."""
        logger.info("Run chain requested")
        try:
            # Clear the JSON cache to ensure we have the most updated version of all chains
            logger.info("Clearing JSON cache before chain execution")
            try:
                from player.json_cache import clear_cache
                clear_cache()
                logger.info("JSON cache cleared successfully")
            except ImportError as e:
                logger.warning(f"Failed to import cache clearing function: {e}")
            except Exception as e:
                logger.warning(f"Failed to clear cache: {e}")
            
            logger.debug("Getting chain configuration from graph view")
            chain_config = self.graph_view.get_chain_config()
            if not chain_config:
                logger.warning("No chain configuration available")
                QMessageBox.warning(self, "No Chain", "No chain configuration available to run.")
                return

            # Check if there are any nodes to run (sequences, conditionals, LLM, TTS, or chain imports)
            sequences = chain_config.get('sequences', [])
            conditional_nodes = chain_config.get('conditional_nodes', [])
            llm_nodes = chain_config.get('llm_nodes', [])
            tts_nodes = chain_config.get('tts_nodes', [])
            chain_import_nodes = chain_config.get('chain_import_nodes', [])
            
            total_nodes = len(sequences) + len(conditional_nodes) + len(llm_nodes) + len(tts_nodes) + len(chain_import_nodes)
            if total_nodes == 0:
                logger.warning("No nodes in chain configuration")
                QMessageBox.warning(self, "Empty Chain", "No nodes found in the current chain.")
                return

            # Create a temporary file for the chain configuration
            import tempfile
            temp_dir = tempfile.gettempdir()
            temp_filename = os.path.join(temp_dir, f"temp_chain_{int(time.time())}.json")
            
            logger.info(f"Saving chain configuration to temporary file: {temp_filename}")
            with open(temp_filename, 'w') as f:
                json.dump(chain_config, f, indent=4)
            logger.debug("Chain configuration saved successfully")
            
            # Minimize window BEFORE starting playback (similar to recording)
            logger.debug("Minimizing main window for playback")
            self.showMinimized()
            
            # Use QTimer to delay chain execution start, allowing window to minimize properly
            def start_playback_delayed():
                # Execute the chain directly using MultiSequencePlayer
                logger.info(f"Starting direct chain execution with config: {temp_filename}")
                try:
                    # Import MultiSequencePlayer for direct execution
                    from player.multi_sequence_player import MultiSequencePlayer
                    
                    # Load the chain configuration from the temporary file
                    with open(temp_filename, 'r') as f:
                        chain_config = json.load(f)
                    
                    # Create and execute the chain directly
                    player = MultiSequencePlayer(chain_config, temp_filename)
                    
                    # Execute the chain in a separate thread to avoid blocking the UI
                    import threading
                    
                    def execute_chain():
                        try:
                            logger.info("Starting chain execution in background thread")
                            
                            # Import and start keyboard monitoring for ESC key abort
                            from player.keyboard_monitor import start_global_monitoring, stop_global_monitoring, create_stop_flag
                            
                            # Start ESC key monitoring
                            start_global_monitoring()
                            stop_flag = create_stop_flag()
                            
                            try:
                                # Execute the chain with stop flag for ESC key abort
                                result = player.play_chain(stop_flag=stop_flag)
                                logger.info(f"Chain execution completed with result: {result}")
                            finally:
                                # Always stop monitoring when done
                                stop_global_monitoring()
                            
                            # Clean up temporary file
                            try:
                                os.remove(temp_filename)
                                logger.debug(f"Temporary file {temp_filename} cleaned up")
                            except OSError as cleanup_error:
                                logger.warning(f"Failed to clean up temporary file: {cleanup_error}")
                            
                            # Restore window after playback completes
                            QTimer.singleShot(0, lambda: self._restore_window())
                            
                        except Exception as e:
                            logger.error(f"Error during chain execution: {e}")
                            import traceback
                            logger.error(f"Chain execution traceback: {traceback.format_exc()}")
                            
                            # Stop monitoring on error
                            try:
                                from player.keyboard_monitor import stop_global_monitoring
                                stop_global_monitoring()
                            except:
                                pass
                            
                            # Restore window and show error
                            error_msg = str(e)
                            QTimer.singleShot(0, lambda: self._handle_playback_error(error_msg))
                            
                            # Clean up temporary file even on error
                            try:
                                os.remove(temp_filename)
                            except OSError:
                                pass
                    
                    # Start execution in background thread
                    execution_thread = threading.Thread(target=execute_chain, daemon=True)
                    execution_thread.start()
                    logger.info("Chain execution thread started")
                        
                except Exception as e:
                    logger.error(f"Error initializing chain execution: {e}")
                    import traceback
                    logger.error(f"Chain initialization traceback: {traceback.format_exc()}")
                    
                    # Restore window and show error
                    error_msg = str(e)
                    QTimer.singleShot(0, lambda: self._handle_playback_error(error_msg))
                    
                    # Clean up temporary file even on error
                    try:
                        os.remove(temp_filename)
                    except OSError:
                        pass
            
            # Use QTimer to start playback after window minimizes
            QTimer.singleShot(500, start_playback_delayed)  # 500ms delay
                    
        except Exception as e:
            logger.error(f"Error in run_chain: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            QMessageBox.critical(self, "Chain Error", f"Failed to run chain: {str(e)}")

    def open_chain(self):
        """Open a chain configuration file."""
        fileName, _ = QFileDialog.getOpenFileName(
            self, "Open Chain", "", "JSON Files (*.json);;All Files (*)"
        )
        if fileName:
            try:
                # Check if graph view is ready
                if not self.graph_view.is_ready():
                    QMessageBox.warning(
                        self, 
                        "Graph Not Ready", 
                        "The graph view is not fully initialized yet. Please wait a moment and try again."
                    )
                    return
                
                self.graph_view.load_chain(fileName)
            except RuntimeError as e:
                QMessageBox.critical(self, "Error", f"Graph not ready: {str(e)}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load chain: {str(e)}")

    def save_chain(self):
        """Saves the current chain to a file"""
        options = QFileDialog.Options()
        fileName, _ = QFileDialog.getSaveFileName(self, "Save Chain File", "", "JSON Files (*.json)", options=options)
        if fileName:
            try:
                self.graph_view.save_chain(fileName)
                QMessageBox.information(self, "Success", "Chain saved successfully!")
            except Exception as e:
                QMessageBox.critical(self, "Save Error", f"Failed to save chain: {str(e)}")
    
    def start_recording(self, sequence_name):
        """Start recording a sequence using ElementRecorder with full capabilities"""
        logger.info(f"Starting recording process for sequence: {sequence_name}")
        
        # Minimize window BEFORE starting the thread (on main thread)
        logger.debug("Minimizing main window on main thread")
        self.showMinimized()
        
        # Use QTimer to delay thread start, allowing window to minimize properly
        def start_recording_delayed():
            def record_thread():
                thread_logger = logging.getLogger(f"{__name__}.record_thread")
                thread_logger.info(f"Recording thread started for sequence: {sequence_name}")
                
                try:
                    # Ensure sequences directory exists
                    sequences_dir = os.path.join(os.getcwd(), "sequences")
                    thread_logger.debug(f"Creating sequences directory: {sequences_dir}")
                    os.makedirs(sequences_dir, exist_ok=True)
                    
                    # Test pynput import first
                    thread_logger.info("Testing pynput import before ElementRecorder initialization")
                    try:
                        from pynput import mouse, keyboard
                        thread_logger.info("pynput imported successfully")
                    except Exception as e:
                        thread_logger.error(f"Failed to import pynput: {str(e)}")
                        thread_logger.error(f"pynput import traceback: {traceback.format_exc()}")
                        raise
                    
                    # Initialize ElementRecorder with sequence name
                    thread_logger.info(f"Initializing ElementRecorder with sequence name: {sequence_name}")
                    recorder = ElementRecorder(sequence_name)
                    thread_logger.info("ElementRecorder initialized successfully")
                    
                    print(f"\n🟢 Recording '{sequence_name}'. Try all actions now!")
                    print("🖱 Drag >10px to trigger drag, else click")
                    print("🡅 Scroll actions are grouped for accurate replay")
                    print("📋 Ctrl+C/V/X/A work reliably")
                    print("⏹ ESC to save and exit")
                    
                    # Set up event listeners with proper callbacks
                    thread_logger.debug("Setting up event listener callbacks")
                    def on_click(x, y, button, pressed):
                        try:
                            if pressed:
                                recorder.on_mouse_press(x, y, button, pressed)
                            else:
                                recorder.on_mouse_release(x, y, button)
                        except Exception as e:
                            thread_logger.error(f"Error in on_click callback: {str(e)}")
                    
                    def on_scroll(x, y, dx, dy):
                        try:
                            recorder.record_scroll(x, y, dx, dy)
                        except Exception as e:
                            thread_logger.error(f"Error in on_scroll callback: {str(e)}")
                    
                    def on_press(key):
                        try:
                            if key == keyboard.Key.esc:
                                thread_logger.info("ESC key pressed, saving sequence")
                                recorder.save_sequence()
                                return False  # Stop listener
                            recorder.handle_keypress(key)
                        except Exception as e:
                            thread_logger.error(f"Error in on_press callback: {str(e)}")
                    
                    def on_release(key):
                        try:
                            recorder.handle_keyrelease(key)
                        except Exception as e:
                            thread_logger.error(f"Error in on_release callback: {str(e)}")
                    
                    # Start listeners
                    thread_logger.debug("Creating mouse and keyboard listeners")
                    try:
                        mouse_listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
                        keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
                        thread_logger.info("Listeners created successfully")
                    except Exception as e:
                        thread_logger.error(f"Failed to create listeners: {str(e)}")
                        thread_logger.error(f"Listener creation traceback: {traceback.format_exc()}")
                        raise
                    
                    thread_logger.info("Starting mouse and keyboard listeners")
                    try:
                        mouse_listener.start()
                        keyboard_listener.start()
                        thread_logger.info("Listeners started, waiting for ESC key")
                        keyboard_listener.join()  # Will block until ESC
                        mouse_listener.stop()
                        thread_logger.info("Listeners stopped successfully")
                    except Exception as e:
                        thread_logger.error(f"Error during listener operation: {str(e)}")
                        thread_logger.error(f"Listener operation traceback: {traceback.format_exc()}")
                        raise
                    
                    print(f"\n✅ Recording '{sequence_name}' saved successfully!")
                    thread_logger.info(f"Recording completed successfully for sequence: {sequence_name}")
                    
                    # Restore window using Qt's thread-safe mechanism
                    thread_logger.debug("Requesting window restoration on main thread")
                    QTimer.singleShot(0, lambda: self._restore_window())
                except Exception as e:
                    thread_logger.error(f"Failed to initialize recording: {str(e)}")
                    thread_logger.error(f"Recording initialization traceback: {traceback.format_exc()}")
                    # Show error dialog and restore window using Qt's thread-safe mechanism
                    QTimer.singleShot(0, lambda: self._handle_recording_error(str(e)))
                    raise
            
            # Start recording in a separate thread
            logger.debug("Creating recording thread")
            try:
                recording_thread = threading.Thread(target=record_thread, daemon=True)
                recording_thread.start()
                logger.info("Recording thread started successfully")
            except Exception as e:
                logger.error(f"Failed to start recording thread: {str(e)}")
                logger.error(f"Thread creation traceback: {traceback.format_exc()}")
        
        # Use QTimer to start recording after window minimizes
        QTimer.singleShot(500, start_recording_delayed)  # 500ms delay
    
    def _restore_window(self):
        """Restore window from minimized state (thread-safe)"""
        self.showNormal()
        self.raise_()
        self.activateWindow()
    
    def _handle_recording_error(self, error_msg):
        """Handle recording error and restore window (thread-safe)"""
        self._restore_window()
        QMessageBox.critical(self, "Recording Error", f"Recording failed: {error_msg}")
    
    def _handle_playback_error(self, error_msg):
        """Handle playback error and restore window (thread-safe)"""
        self._restore_window()
        QMessageBox.critical(self, "Playback Error", f"Failed to run chain: {error_msg}")

    def open_scheduler_manager(self):
        """Open the Scheduler Manager dialog"""
        try:
            dialog = SchedulerManagerDialog(self, self.scheduler_service)
            dialog.exec_()
        except Exception as e:
            logger.error(f"Failed to open SchedulerManagerDialog: {e}")
            QMessageBox.critical(self, "Scheduler Error", f"Failed to open scheduler: {e}")
    
    def open_ai_settings(self):
        """Open the AI Settings dialog"""
        try:
            dialog = AISettingsDialog(self)
            dialog.exec_()
        except Exception as e:
            logger.error(f"Failed to open AISettingsDialog: {e}")
            QMessageBox.critical(self, "AI Settings Error", f"Failed to open AI settings: {e}")

    def closeEvent(self, event):
        """Ensure background services stop on app close"""
        try:
            if hasattr(self, 'scheduler_service') and self.scheduler_service:
                self.scheduler_service.stop()
        except Exception as e:
            logger.error(f"Error stopping SchedulerService: {e}")
        super().closeEvent(event)
import os
import logging
from PyQt5.QtWidgets import QMessageBox, QFileDialog
try:
    from ...AI.model_cache import get_real_cached_models
except Exception:
    def get_real_cached_models(timeout: float = 5.0):
        return []

logger = logging.getLogger(__name__)


class ExportUtils:
    """Handles exporting chains as executable files."""
    
    def __init__(self, parent_widget):
        logger.info("Initializing ExportUtils")
        try:
            self.parent_widget = parent_widget
            logger.debug("ExportUtils initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing ExportUtils: {e}")
            raise
        
    def export_as_executable(self, file_path=None):
        """Export the current chain as an executable batch file."""
        logger.info(f"Starting export as executable, file_path: {file_path}")
        try:
            if not file_path:
                logger.debug("No file path provided, opening file dialog")
                file_path, _ = QFileDialog.getSaveFileName(
                    self.parent_widget,
                    "Export as Executable",
                    "",
                    "Batch files (*.bat);;All files (*.*)"
                )
                logger.debug(f"File dialog result: {file_path}")
                
            if file_path:
                logger.info(f"Exporting to file: {file_path}")
                # Save current state to get latest config
                logger.debug("Saving current state before export")
                self.parent_widget.config_manager.save_current_state()
                
                # Generate batch file content
                logger.debug("Generating batch file content")
                batch_content = self._generate_batch_content()
                
                # Write to file
                logger.debug(f"Writing batch content to file: {file_path}")
                with open(file_path, 'w') as f:
                    f.write(batch_content)
                    
                logger.info(f"Export successful: {file_path}")
                QMessageBox.information(
                    self.parent_widget,
                    "Export Successful",
                    f"Chain exported successfully to {file_path}"
                )
                
                return True
            else:
                logger.warning("Export cancelled - no file path selected")
                
        except Exception as e:
            logger.error(f"Error exporting as executable: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Export Error",
                f"Failed to export chain: {str(e)}"
            )
            
        return False
        
    def export_chain(self, chain_config, output_path):
        """Export a specific chain configuration."""
        logger.info(f"Exporting chain to: {output_path}")
        try:
            logger.debug(f"Chain config contains {len(chain_config)} items")
            # Generate batch content from config
            logger.debug("Generating batch content from configuration")
            batch_content = self._generate_batch_content_from_config(chain_config)
            
            # Write to file
            logger.debug(f"Writing batch content to: {output_path}")
            with open(output_path, 'w') as f:
                f.write(batch_content)
                
            logger.info(f"Chain exported successfully to: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to export chain: {e}")
            return False
            
    def _generate_batch_content(self):
        """Generate batch file content from current configuration."""
        logger.debug("Generating batch content from current configuration")
        try:
            chain_config = self.parent_widget.config_manager.get_chain_config()
            logger.debug(f"Retrieved chain config with {len(chain_config)} items")
            return self._generate_batch_content_from_config(chain_config)
        except Exception as e:
            logger.error(f"Error generating batch content: {e}")
            raise
        
    def _generate_batch_content_from_config(self, chain_config):
        """Generate batch file content from a chain configuration."""
        logger.debug("Generating batch content from chain configuration")
        try:
            lines = []
            
            # Add header
            logger.debug("Adding batch file header")
            lines.append("@echo off")
            lines.append("REM Auto-generated LoOper chain execution script")
            lines.append("REM Generated from graph configuration")
            lines.append("")
            
            # Add Python path setup
            logger.debug("Adding Python environment setup")
            lines.append("REM Set up Python environment")
            lines.append("set PYTHON_PATH=%~dp0")
            lines.append("")
            
            # Add main execution command
            logger.debug("Adding main execution command")
            lines.append("REM Execute the chain")
            lines.append(f'python "{self._get_main_script_path()}" --execute-chain')
            
            # Add sequence execution commands
            sequences = chain_config.get("sequences", [])
            logger.debug(f"Processing {len(sequences)} sequences")
            if sequences:
                lines.append("")
                lines.append("REM Execute sequences in order")
                
                for i, sequence in enumerate(sequences):
                    sequence_name = sequence.get("name", f"sequence_{i}")
                    logger.debug(f"Adding sequence command for: {sequence_name}")
                    lines.append(f'echo Executing sequence: {sequence_name}')
                    lines.append(f'python "{self._get_main_script_path()}" --sequence "{sequence_name}"')
                
                # Add error checking
                lines.append("if %errorlevel% neq 0 (")
                lines.append(f"    echo Error executing sequence: {sequence_name}")
                lines.append("    pause")
                lines.append("    exit /b %errorlevel%")
                lines.append(")")
                lines.append("")
                
            # Add conditional nodes execution
            conditional_nodes = chain_config.get("conditional_nodes", [])
            logger.debug(f"Processing {len(conditional_nodes)} conditional nodes")
            if conditional_nodes:
                lines.append("REM Execute conditional nodes")
                
                for i, cond_node in enumerate(conditional_nodes):
                    condition_type = cond_node.get("condition_type") or cond_node.get("trigger_type", "presence")  # backward compatibility
                    logger.debug(f"Adding conditional node {i}: {condition_type}")
                    lines.append(f'echo Executing conditional: {condition_type}')
                    
                    if condition_type == "presence":
                        image_path = cond_node.get("image_path", "")
                        threshold = cond_node.get("threshold", 0.8)
                        logger.debug(f"Presence condition: {image_path}, threshold: {threshold}")
                        lines.append(f'python "{self._get_main_script_path()}" --wait-for-image "{image_path}" --threshold {threshold}')
                        
                    elif condition_type == "absence":
                        image_path = cond_node.get("image_path", "")
                        threshold = cond_node.get("threshold", 0.8)
                        logger.debug(f"Absence condition: {image_path}, threshold: {threshold}")
                        lines.append(f'python "{self._get_main_script_path()}" --wait-for-absence "{image_path}" --threshold {threshold}')
                        
                    elif condition_type == "ocr":
                        ocr_text = cond_node.get("ocr_text", "")
                        logger.debug(f"OCR condition: {ocr_text}")
                        lines.append(f'python "{self._get_main_script_path()}" --wait-for-text "{ocr_text}"')
                        
                    elif condition_type == "wait":
                        wait_time = cond_node.get("wait_time", 5)
                        logger.debug(f"Wait condition: {wait_time} seconds")
                        lines.append(f'timeout /t {wait_time} /nobreak')
                        
                    elif condition_type == "loop":
                        max_loops = cond_node.get("max_loops", 10)
                        logger.debug(f"Loop condition: {max_loops} iterations")
                        lines.append(f'python "{self._get_main_script_path()}" --loop {max_loops}')
                        
                    lines.append("")
                
            # Add LLM nodes execution
            llm_nodes = chain_config.get("llm_nodes", [])
            logger.debug(f"Processing {len(llm_nodes)} LLM nodes")
            if llm_nodes:
                lines.append("REM Execute LLM nodes")
                
                for i, llm_node in enumerate(llm_nodes):
                    prompt = llm_node.get("prompt", "")
                    # Prefer the model stored in the node; if missing, pick first real cached model
                    try:
                        cached = get_real_cached_models(timeout=2.0)
                    except Exception:
                        cached = []
                    model = llm_node.get("model") or (cached[0] if cached else "")
                    logger.debug(f"Adding LLM node {i}: {model}, prompt length: {len(prompt)}")
                    lines.append(f'echo Executing LLM: {model}')
                    lines.append(f'python "{self._get_main_script_path()}" --llm-prompt "{prompt}" --model "{model}"')
                    lines.append("")
                    
            # Add footer
            logger.debug("Adding batch file footer")
            lines.append("echo Chain execution completed")
            lines.append("pause")
            
            batch_content = "\n".join(lines)
            logger.debug(f"Generated batch content with {len(lines)} lines")
            return batch_content
        except Exception as e:
            logger.error(f"Error generating batch content from config: {e}")
            raise
        
    def _get_main_script_path(self):
        """Get the path to the main LoOper script."""
        logger.debug("Getting main script path")
        try:
            # Assuming the main script is in the parent directory
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            main_script_path = os.path.join(current_dir, "main.py")
            logger.debug(f"Main script path: {main_script_path}")
            return main_script_path
        except Exception as e:
            logger.error(f"Error getting main script path: {e}")
            raise
        
    def generate_python_script(self, chain_config, output_path):
        """Generate a Python script instead of batch file."""
        logger.info(f"Generating Python script to: {output_path}")
        try:
            logger.debug(f"Chain config contains {len(chain_config)} items")
            python_content = self._generate_python_content(chain_config)
            
            logger.debug(f"Writing Python content to: {output_path}")
            with open(output_path, 'w') as f:
                f.write(python_content)
                
            logger.info(f"Python script generated successfully: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to generate Python script: {e}")
            return False
            
    def _generate_python_content(self, chain_config):
        """Generate Python script content from chain configuration."""
        logger.debug("Generating Python script content from chain configuration")
        try:
            lines = []
            
            # Add header and imports
            logger.debug("Adding Python script header and imports")
            lines.append("#!/usr/bin/env python3")
            lines.append('"""Auto-generated LoOper chain execution script"""')
            lines.append("")
            lines.append("import sys")
            lines.append("import os")
            lines.append("import time")
            lines.append("import json")
            lines.append("")
            
            # Add main function
            logger.debug("Adding main function")
            lines.append("def main():")
            lines.append('    """Execute the chain."""')
            lines.append("    print('Starting chain execution...')")
            lines.append("")
            
            # Add sequence execution
            sequences = chain_config.get("sequences", [])
            logger.debug(f"Adding {len(sequences)} sequence executions")
            for sequence in sequences:
                sequence_name = sequence.get("name", "")
                logger.debug(f"Adding sequence execution for: {sequence_name}")
                lines.append(f"    # Execute sequence: {sequence_name}")
                lines.append(f'    print("Executing sequence: {sequence_name}")')
                lines.append(f"    execute_sequence('{sequence_name}')")
                lines.append("")
                
            # Add helper functions
            logger.debug("Adding helper functions")
            lines.append("")
            lines.append("def execute_sequence(sequence_name):")
            lines.append('    """Execute a specific sequence."""')
            lines.append("    # Implementation depends on your LoOper setup")
            lines.append("    pass")
            lines.append("")
            
            # Add entry point
            logger.debug("Adding entry point")
            lines.append("if __name__ == '__main__':")
            lines.append("    main()")
            
            python_content = "\n".join(lines)
            logger.debug(f"Generated Python content with {len(lines)} lines")
            return python_content
        except Exception as e:
            logger.error(f"Error generating Python content: {e}")
            raise
        
    def get_export_formats(self):
        """Get available export formats."""
        logger.debug("Getting available export formats")
        formats = {
            "batch": "Windows Batch File (*.bat)",
            "python": "Python Script (*.py)",
            "shell": "Shell Script (*.sh)"
        }
        logger.debug(f"Available formats: {list(formats.keys())}")
        return formats
        
    def export_with_format(self, format_type, file_path=None):
        """Export chain in specified format."""
        logger.info(f"Exporting with format: {format_type}, file_path: {file_path}")
        try:
            if format_type == "batch":
                logger.debug("Exporting as batch file")
                return self.export_as_executable(file_path)
            elif format_type == "python":
                logger.debug("Exporting as Python script")
                if not file_path:
                    logger.debug("No file path provided, opening file dialog")
                    file_path, _ = QFileDialog.getSaveFileName(
                        self.parent_widget,
                        "Export as Python Script",
                        "",
                        "Python files (*.py);;All files (*.*)"
                    )
                    logger.debug(f"File dialog result: {file_path}")
                if file_path:
                    logger.debug("Getting chain config for Python export")
                    chain_config = self.parent_widget.config_manager.get_chain_config()
                    return self.generate_python_script(chain_config, file_path)
                else:
                    logger.warning("Python export cancelled - no file path selected")
            else:
                logger.warning(f"Unsupported export format: {format_type}")
            
            return False
        except Exception as e:
            logger.error(f"Error exporting with format {format_type}: {e}")
            return False
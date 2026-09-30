import os
import subprocess
import threading
from ..common import chain_logger
import logging
logger = logging.getLogger(__name__)

class ContainerMixin:
    """Provides VM/Container execution capabilities for the workflow executor using QEMU"""
    
    def _execute_container_node(self, node, stop_flag=None):
        """Execute a container/VM node"""
        container_data = node.get('data', {}) if isinstance(node, dict) else {}
        node_id = node.get('id') or node.get('node_id') or container_data.get('node_id') or container_data.get('id')
        
        chain_logger.info(f"Executing VM/Container node {node_id}")
        
        iso_path = container_data.get('iso_path', '')
        memory_mb = int(container_data.get('memory_mb', 1024))
        cpu_cores = int(container_data.get('cpu_cores', 1))
        hide_window = container_data.get('hide_window', True)
        output_var = container_data.get('output_variable', 'container_result')
        timeout = int(container_data.get('timeout', 300))
        
        if not iso_path or not os.path.exists(iso_path):
            error_msg = f"ISO file not found: {iso_path}"
            logger.error(error_msg)
            self.llm_executor.set_variable(output_var, {"error": error_msg})
            return self._get_container_next_node(node, 'output')
            
        # Prepare the QEMU execution
        try:
            # Look for qemu-system-x86_64
            qemu_cmd = 'qemu-system-x86_64'
            
            # Construct the run command
            run_cmd = [
                qemu_cmd,
                '-m', str(memory_mb),
                '-smp', str(cpu_cores),
                '-cdrom', iso_path,
                '-boot', 'd'
            ]
            
            # Add network if needed (default user network)
            run_cmd.extend(['-net', 'nic', '-net', 'user'])
            
            if hide_window:
                # Run headless, potentially expose VNC for the agent to connect to internally later
                # We can expose it to a local port like 5900
                run_cmd.extend(['-display', 'vnc=127.0.0.1:0'])
            else:
                # Let it open its own window
                pass
                
            logger.info(f"Booting VM: {' '.join(run_cmd)}")
            
            # For a VM, we usually want it to keep running in the background. 
            # We start it as a subprocess and don't block.
            # We can store the PID or process object to kill it later if needed.
            process = subprocess.Popen(
                run_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Store result info so the agent knows how to interact with it
            output_data = {
                "status": "running",
                "pid": process.pid,
                "iso": iso_path,
                "display": "vnc://127.0.0.1:5900" if hide_window else "local_window"
            }
            
            self.llm_executor.set_variable(output_var, output_data)
            logger.info(f"VM started successfully with PID {process.pid}")
            
            # Optional: Start a thread to monitor if it crashes immediately
            def monitor_vm():
                try:
                    process.wait(timeout=timeout)
                    if process.returncode != 0:
                        logger.warning(f"VM exited with code {process.returncode}")
                except subprocess.TimeoutExpired:
                    # Normal, it's still running
                    pass
                    
            t = threading.Thread(target=monitor_vm, daemon=True)
            t.start()
                
        except FileNotFoundError:
            error_msg = "QEMU not found in PATH. Please install qemu-system-x86_64."
            logger.error(error_msg)
            self.llm_executor.set_variable(output_var, {"error": error_msg})
        except Exception as e:
            error_msg = f"Error starting VM: {str(e)}"
            logger.error(error_msg)
            self.llm_executor.set_variable(output_var, {"error": error_msg})
            
        return self._get_container_next_node(node, 'output')

    def _get_container_next_node(self, node, port):
        """Get the next node connected to the specified port."""
        connections = node.get('connections', {})
        if port in connections and connections[port]:
             return connections[port][0]['node_id']
        if port == 'output':
            return "__done__"
        return None

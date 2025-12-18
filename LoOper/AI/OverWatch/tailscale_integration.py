#!/usr/bin/env python3
"""
Tailscale Integration for OverWatch

Handles Tailscale networking configuration and management for secure remote access.
"""

import os
import json
import logging
import subprocess
import socket
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TailscaleDevice:
    """Represents a Tailscale device"""
    name: str
    ip: str
    online: bool
    os: str
    user: str
    last_seen: str


@dataclass
class TailscaleStatus:
    """Tailscale connection status"""
    connected: bool
    ip: Optional[str]
    hostname: Optional[str]
    devices: List[TailscaleDevice]
    backend_state: str
    health: List[str]


class TailscaleManager:
    """Manages Tailscale integration for OverWatch"""
    
    def __init__(self, settings=None):
        self.settings = settings
        self.tailscale_binary = self._find_tailscale_binary()
        self.is_available = self.tailscale_binary is not None
        
        if not self.is_available:
            logger.warning("Tailscale binary not found. Tailscale features will be disabled.")
        else:
            logger.info(f"Tailscale binary found at: {self.tailscale_binary}")
    
    def _find_tailscale_binary(self) -> Optional[str]:
        """Find Tailscale binary on the system"""
        # Common Tailscale binary locations
        possible_paths = [
            # Windows
            r"C:\Program Files\Tailscale\tailscale.exe",
            r"C:\Program Files (x86)\Tailscale\tailscale.exe",
            # Linux/macOS
            "/usr/bin/tailscale",
            "/usr/local/bin/tailscale",
            "/opt/tailscale/bin/tailscale",
            # PATH
            "tailscale",
            "tailscale.exe"
        ]
        
        for path in possible_paths:
            try:
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    return path
                elif path in ["tailscale", "tailscale.exe"]:
                    # Check if it's in PATH
                    result = subprocess.run(
                        ["where" if os.name == "nt" else "which", path],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        return result.stdout.strip().split('\n')[0]
            except Exception:
                continue
        
        return None
    
    def _run_tailscale_command(self, args: List[str], timeout: int = 30) -> Tuple[bool, str, str]:
        """Run a Tailscale command and return success, stdout, stderr"""
        if not self.is_available:
            return False, "", "Tailscale binary not available"
        
        try:
            cmd = [self.tailscale_binary] + args
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            return result.returncode == 0, result.stdout, result.stderr
            
        except subprocess.TimeoutExpired:
            logger.error(f"Tailscale command timed out: {args}")
            return False, "", "Command timed out"
        except Exception as e:
            logger.error(f"Failed to run Tailscale command {args}: {e}")
            return False, "", str(e)
    
    def get_status(self) -> TailscaleStatus:
        """Get current Tailscale status"""
        if not self.is_available:
            return TailscaleStatus(
                connected=False,
                ip=None,
                hostname=None,
                devices=[],
                backend_state="unavailable",
                health=["Tailscale not available"]
            )
        
        success, stdout, stderr = self._run_tailscale_command(["status", "--json"])
        
        if not success:
            logger.error(f"Failed to get Tailscale status: {stderr}")
            return TailscaleStatus(
                connected=False,
                ip=None,
                hostname=None,
                devices=[],
                backend_state="error",
                health=[f"Status check failed: {stderr}"]
            )
        
        try:
            status_data = json.loads(stdout)
            
            # Parse devices
            devices = []
            for device_data in status_data.get("Peer", {}).values():
                devices.append(TailscaleDevice(
                    name=device_data.get("HostName", "Unknown"),
                    ip=device_data.get("TailscaleIPs", [""])[0] if device_data.get("TailscaleIPs") else "",
                    online=device_data.get("Online", False),
                    os=device_data.get("OS", "Unknown"),
                    user=device_data.get("UserID", "Unknown"),
                    last_seen=device_data.get("LastSeen", "Unknown")
                ))
            
            # Get self info
            self_data = status_data.get("Self", {})
            my_ip = self_data.get("TailscaleIPs", [""])[0] if self_data.get("TailscaleIPs") else None
            my_hostname = self_data.get("HostName")
            
            return TailscaleStatus(
                connected=status_data.get("BackendState") == "Running",
                ip=my_ip,
                hostname=my_hostname,
                devices=devices,
                backend_state=status_data.get("BackendState", "Unknown"),
                health=status_data.get("Health", [])
            )
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Tailscale status JSON: {e}")
            return TailscaleStatus(
                connected=False,
                ip=None,
                hostname=None,
                devices=[],
                backend_state="parse_error",
                health=[f"JSON parse error: {str(e)}"]
            )
    
    def is_connected(self) -> bool:
        """Check if Tailscale is connected"""
        status = self.get_status()
        return status.connected
    
    def get_my_ip(self) -> Optional[str]:
        """Get this device's Tailscale IP"""
        status = self.get_status()
        return status.ip
    
    def connect(self, auth_key: Optional[str] = None) -> bool:
        """Connect to Tailscale network"""
        if not self.is_available:
            logger.error("Cannot connect: Tailscale not available")
            return False
        
        # Check if already connected
        if self.is_connected():
            logger.info("Already connected to Tailscale")
            return True
        
        # Try to start Tailscale
        args = ["up"]
        
        # Add auth key if provided
        if auth_key:
            args.extend(["--authkey", auth_key])
        elif self.settings and hasattr(self.settings.tailscale, 'auth_key') and self.settings.tailscale.auth_key:
            args.extend(["--authkey", self.settings.tailscale.auth_key])
        
        # Add hostname if configured
        if self.settings and hasattr(self.settings.tailscale, 'hostname') and self.settings.tailscale.hostname:
            args.extend(["--hostname", self.settings.tailscale.hostname])
        
        success, stdout, stderr = self._run_tailscale_command(args, timeout=60)
        
        if success:
            logger.info("Successfully connected to Tailscale")
            return True
        else:
            logger.error(f"Failed to connect to Tailscale: {stderr}")
            return False
    
    def disconnect(self) -> bool:
        """Disconnect from Tailscale network"""
        if not self.is_available:
            logger.error("Cannot disconnect: Tailscale not available")
            return False
        
        success, stdout, stderr = self._run_tailscale_command(["down"])
        
        if success:
            logger.info("Successfully disconnected from Tailscale")
            return True
        else:
            logger.error(f"Failed to disconnect from Tailscale: {stderr}")
            return False
    
    def ping_device(self, ip: str, timeout: int = 5) -> bool:
        """Ping a Tailscale device"""
        if not self.is_available:
            return False
        
        success, stdout, stderr = self._run_tailscale_command(["ping", ip, "--c", "1", "--timeout", f"{timeout}s"])
        return success
    
    def get_logs(self, lines: int = 100) -> List[str]:
        """Get Tailscale logs"""
        if not self.is_available:
            return ["Tailscale not available"]
        
        success, stdout, stderr = self._run_tailscale_command(["debug", "logs", "--lines", str(lines)])
        
        if success:
            return stdout.strip().split('\n')
        else:
            return [f"Failed to get logs: {stderr}"]
    
    def configure_for_overwatch(self) -> Dict[str, any]:
        """Configure Tailscale for OverWatch usage"""
        result = {
            "success": False,
            "message": "",
            "ip": None,
            "hostname": None
        }
        
        if not self.is_available:
            result["message"] = "Tailscale not available"
            return result
        
        # Try to connect if not already connected
        if not self.is_connected():
            logger.info("Tailscale not connected, attempting to connect...")
            if not self.connect():
                result["message"] = "Failed to connect to Tailscale"
                return result
        
        # Get status after connection
        status = self.get_status()
        
        if status.connected:
            result["success"] = True
            result["message"] = "Tailscale configured successfully"
            result["ip"] = status.ip
            result["hostname"] = status.hostname
            
            # Update settings if available
            if self.settings and hasattr(self.settings, 'tailscale'):
                self.settings.tailscale.server_ip = status.ip
                logger.info(f"Updated Tailscale server IP to: {status.ip}")
        else:
            result["message"] = f"Tailscale connection failed: {status.backend_state}"
        
        return result
    
    def test_api_connectivity(self, port: int = 8000) -> bool:
        """Test if the OverWatch API is accessible via Tailscale"""
        my_ip = self.get_my_ip()
        if not my_ip:
            return False
        
        try:
            # Test TCP connection to the API port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((my_ip, port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.error(f"Failed to test API connectivity: {e}")
            return False
    
    def get_network_info(self) -> Dict[str, any]:
        """Get comprehensive Tailscale network information"""
        status = self.get_status()
        
        return {
            "available": self.is_available,
            "connected": status.connected,
            "my_ip": status.ip,
            "my_hostname": status.hostname,
            "backend_state": status.backend_state,
            "health_issues": status.health,
            "device_count": len(status.devices),
            "online_devices": len([d for d in status.devices if d.online]),
            "devices": [
                {
                    "name": d.name,
                    "ip": d.ip,
                    "online": d.online,
                    "os": d.os
                }
                for d in status.devices
            ]
        }
    
    def auto_configure(self) -> Dict[str, any]:
        """Automatically configure Tailscale for optimal OverWatch usage"""
        logger.info("Starting Tailscale auto-configuration...")
        
        result = {
            "success": False,
            "steps": [],
            "final_config": {}
        }
        
        # Step 1: Check availability
        if not self.is_available:
            result["steps"].append("❌ Tailscale binary not found")
            return result
        
        result["steps"].append("✅ Tailscale binary found")
        
        # Step 2: Check/establish connection
        if not self.is_connected():
            result["steps"].append("🔄 Connecting to Tailscale...")
            if self.connect():
                result["steps"].append("✅ Connected to Tailscale")
            else:
                result["steps"].append("❌ Failed to connect to Tailscale")
                return result
        else:
            result["steps"].append("✅ Already connected to Tailscale")
        
        # Step 3: Get network info
        status = self.get_status()
        if status.ip:
            result["steps"].append(f"✅ Tailscale IP: {status.ip}")
            result["final_config"]["server_ip"] = status.ip
        else:
            result["steps"].append("❌ Could not determine Tailscale IP")
            return result
        
        # Step 4: Test API connectivity (if API is running)
        api_port = 8000
        if self.settings and hasattr(self.settings.api, 'port'):
            api_port = self.settings.api.port
        
        if self.test_api_connectivity(api_port):
            result["steps"].append(f"✅ API accessible on port {api_port}")
        else:
            result["steps"].append(f"⚠️  API not currently accessible on port {api_port} (may not be running)")
        
        result["final_config"]["api_port"] = api_port
        result["final_config"]["hostname"] = status.hostname
        result["success"] = True
        
        logger.info("Tailscale auto-configuration completed successfully")
        return result


def create_tailscale_manager(settings=None) -> TailscaleManager:
    """Create a Tailscale manager instance"""
    return TailscaleManager(settings)


def check_tailscale_requirements() -> Dict[str, any]:
    """Check if Tailscale requirements are met"""
    manager = TailscaleManager()
    
    return {
        "binary_available": manager.is_available,
        "binary_path": manager.tailscale_binary,
        "connected": manager.is_connected() if manager.is_available else False,
        "network_info": manager.get_network_info() if manager.is_available else None
    }


if __name__ == "__main__":
    # Test Tailscale integration
    logging.basicConfig(level=logging.INFO)
    
    print("🔍 Testing Tailscale Integration")
    print("=" * 40)
    
    # Check requirements
    requirements = check_tailscale_requirements()
    print(f"Binary available: {requirements['binary_available']}")
    if requirements['binary_path']:
        print(f"Binary path: {requirements['binary_path']}")
    print(f"Connected: {requirements['connected']}")
    
    if requirements['binary_available']:
        manager = TailscaleManager()
        
        # Get status
        status = manager.get_status()
        print(f"\nStatus: {status.backend_state}")
        if status.ip:
            print(f"My IP: {status.ip}")
        if status.hostname:
            print(f"Hostname: {status.hostname}")
        
        print(f"Devices: {len(status.devices)}")
        for device in status.devices:
            print(f"  - {device.name} ({device.ip}) - {'Online' if device.online else 'Offline'}")
        
        # Auto-configure
        print("\n🔧 Auto-configuration:")
        config_result = manager.auto_configure()
        for step in config_result["steps"]:
            print(f"  {step}")
        
        if config_result["success"]:
            print(f"\n✅ Final configuration: {config_result['final_config']}")
    
    print("\n✅ Tailscale integration test completed")
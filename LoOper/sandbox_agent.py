import json
import socket
import pyautogui
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import base64
import io
import sys

# Disable failsafe since the agent might move the mouse to the corner
pyautogui.FAILSAFE = False

class AgentHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Always return 200 for action to ensure the client doesn't get 404s
        if not self.path.startswith('/action') and self.path != '/':
            # Just log it but try to process it anyway to be ultra-forgiving
            print(f"Warning: Received POST to unexpected path: {self.path}")
            
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self.send_response(400)
            self.end_headers()
            return
            
        post_data = self.rfile.read(content_length)
        
        try:
            req = json.loads(post_data.decode('utf-8'))
            action = req.get('action')
            result = {"status": "success"}
            
            if action == 'click':
                x, y = req.get('x'), req.get('y')
                button = req.get('button', 'left')
                if x is not None and y is not None:
                    pyautogui.moveTo(x, y)
                    pyautogui.click(x, y, button=button)
                else:
                    pyautogui.click(button=button)
                    
            elif action == 'type':
                text = req.get('text')
                if text:
                    pyautogui.write(text, interval=0.01)
                    
            elif action == 'hotkey':
                keys = req.get('keys', [])
                if keys:
                    for k in keys:
                        pyautogui.keyDown(k)
                    for k in reversed(keys):
                        pyautogui.keyUp(k)
                        
            elif action == 'screenshot':
                import uuid
                img = pyautogui.screenshot()
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                img_str = base64.b64encode(buffered.getvalue()).decode()
                # Adding a timestamp/UUID prevents aggressive caching proxies or 
                # OS-level network stack caching from returning stale responses
                result["image"] = img_str
                result["timestamp"] = time.time()
                result["id"] = str(uuid.uuid4())
                
            elif action == 'scroll':
                amount = req.get('amount', 0)
                if amount != 0:
                    pyautogui.scroll(amount)
                    
            elif action == 'drag_start':
                x, y = req.get('x'), req.get('y')
                pyautogui.moveTo(x, y)
                pyautogui.mouseDown()
                
            elif action == 'drag_end':
                x, y = req.get('x'), req.get('y')
                pyautogui.moveTo(x, y)
                pyautogui.mouseUp()
                
            elif action == 'drag_drop':
                start_x, start_y = req.get('start_x'), req.get('start_y')
                end_x, end_y = req.get('end_x'), req.get('end_y')
                pyautogui.moveTo(start_x, start_y)
                pyautogui.mouseDown()
                pyautogui.moveTo(end_x, end_y, duration=0.2)
                pyautogui.mouseUp()
                
            else:
                result = {"status": "error", "message": f"Unknown action: {action}"}
                
        except Exception as e:
            result = {"status": "error", "message": str(e)}
            
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode('utf-8'))

    def log_message(self, format, *args):
        # Suppress default HTTP logging to keep stdout clean
        pass

def run():
    # Write a quick status file to show we're starting
    shared_dir = "C:\\TempShared"
    if not os.path.exists(shared_dir):
        os.makedirs(shared_dir, exist_ok=True)
    with open(os.path.join(shared_dir, "agent_status.txt"), "w") as f:
        f.write("starting")

    try:
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
    except Exception as e:
        with open(os.path.join(shared_dir, "agent_error.txt"), "w") as f:
            f.write(str(e))
        ip_address = "127.0.0.1"
        
    port = None
    try:
        env_port = os.environ.get("LOOPER_AGENT_PORT")
        if env_port:
            port = int(env_port)
    except Exception:
        port = None

    if port is None:
        try:
            if "--port" in sys.argv:
                idx = sys.argv.index("--port")
                if idx + 1 < len(sys.argv):
                    port = int(sys.argv[idx + 1])
        except Exception:
            port = None

    if port is None:
        port = 8000
    
    info_path = os.path.join(shared_dir, "agent_info.json")
    with open(info_path, "w") as f:
        json.dump({"ip": ip_address, "port": port}, f)
        
    print(f"Agent listening on {ip_address}:{port}")
    try:
        server_address = ('0.0.0.0', port)
        httpd = HTTPServer(server_address, AgentHandler)
        httpd.serve_forever()
    except Exception as e:
        with open(os.path.join(shared_dir, "agent_error.txt"), "w") as f:
            f.write(str(e))

if __name__ == '__main__':
    run()

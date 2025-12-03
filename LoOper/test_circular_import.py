
import sys
import os

# Add the project root to the path so imports work
current_dir = os.getcwd()
project_root = os.path.abspath(os.path.join(current_dir, '..', '..'))
sys.path.append(project_root)

print(f"Project root added to path: {project_root}")

try:
    print("Attempting to import NGUI.app...")
    # We simulate the import chain that caused the error
    from LoOper.NGUI import app
    print("Successfully imported NGUI.app")
except ImportError as e:
    print(f"ImportError caught: {e}")
except Exception as e:
    print(f"An error occurred: {e}")

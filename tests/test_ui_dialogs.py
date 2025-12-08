
import sys
import os
from PyQt5.QtWidgets import QApplication

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../LoOper')))

# Import ModernDialog and a subclass
from NGUI.dialogs.base_dialog import ModernDialog
from NGUI.dialogs.tts_dialogs import TTSPropertiesDialog

def test_dialog_instantiation():
    app = QApplication(sys.argv)
    
    # Test base class
    try:
        dialog = ModernDialog(title="Test Dialog")
        print("ModernDialog instantiated successfully")
    except Exception as e:
        print(f"Failed to instantiate ModernDialog: {e}")
        return

    # Test subclass
    try:
        tts_dialog = TTSPropertiesDialog()
        print("TTSPropertiesDialog instantiated successfully")
    except Exception as e:
        print(f"Failed to instantiate TTSPropertiesDialog: {e}")
        return

if __name__ == "__main__":
    test_dialog_instantiation()

from PyQt5.QtWidgets import QMessageBox

class FormFillerOperationsMixin:
    def add_form_filler_node(self, pos=None):
        from PyQt5.QtWidgets import QMessageBox
        try:
            QMessageBox.information(self.parent_widget, "Deprecated", "Form Filler node is deprecated and cannot be added.")
        except Exception:
            pass
        return None

    def edit_form_filler_node(self, node):
        from PyQt5.QtWidgets import QMessageBox
        try:
            QMessageBox.information(self.parent_widget, "Deprecated", "Form Filler node is deprecated and cannot be edited.")
        except Exception:
            pass
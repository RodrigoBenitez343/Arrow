import sys
import os
import subprocess
import signal
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QProgressBar
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap

class Splash(QWidget):
    def __init__(self, logo_path: str):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.logo_label = QLabel()
        self.logo_label.setStyleSheet("background: transparent;")
        pix = QPixmap(logo_path) if os.path.exists(logo_path) else QPixmap()
        if not pix.isNull():
            pix = pix.scaled(360, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.logo_label.setPixmap(pix)
            self._logo_w = pix.width()
            self._logo_h = pix.height()
        self.logo_label.setAlignment(Qt.AlignCenter)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("QProgressBar{background:rgba(0,0,0,0); border:0px;} QProgressBar::chunk{background:#14c714; border-radius:0px;}")
        self.progress.setFixedHeight(8)

        try:
            w = getattr(self, "_logo_w", 360)
            h = getattr(self, "_logo_h", 360)
            self.logo_label.setFixedSize(w, h)
            self.progress.setFixedWidth(w)
            self.setFixedSize(w, h + self.progress.sizeHint().height())
        except Exception:
            pass

        layout.addWidget(self.logo_label)
        layout.addWidget(self.progress)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(180)
        try:
            # Ensure explicit sizing to avoid clipping
            w = getattr(self, "_logo_w", 360)
            h = getattr(self, "_logo_h", 360)
            self.logo_label.setFixedSize(w, h)
            self.progress.setFixedWidth(w)
            self.setFixedSize(w, h + self.progress.height())
        except Exception:
            pass
        try:
            g = QApplication.primaryScreen().availableGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)
        except Exception:
            pass

    def _advance(self):
        v = self.progress.value()
        if v < 95:
            self.progress.setValue(min(95, v + 2))

    def set_status(self, text: str):
        pass

def _find_main_window():
    try:
        import win32gui
    except Exception:
        return False
    try:
        hwnd = win32gui.FindWindow(None, "LoOper BETA:1.8.0")
        return bool(hwnd)
    except Exception:
        return False

def _find_main_window_for_pid(pid: int):
    try:
        import win32gui
        import win32process
    except Exception:
        return False
    found = False
    def _enum(hwnd, _):
        nonlocal found
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            if title != "LoOper BETA:1.8.0":
                return
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if int(wpid) != int(pid):
                return
            try:
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w < 100 or h < 100:
                    return
            except Exception:
                pass
            found = True
        except Exception:
            pass
    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        return False
    return found

def launch():
    app = QApplication(sys.argv)
    base = os.path.dirname(os.path.abspath(__file__))
    logo_png = os.path.join(base, "LoOper", "LoOper.png")
    logo_ico = os.path.join(base, "LoOper", "LoOper.ico")
    logo = logo_png if os.path.exists(logo_png) else logo_ico
    splash = Splash(logo)
    splash.show()

    main_path = os.path.join(base, "LoOper", "main.py")
    if not os.path.exists(main_path):
        QTimer.singleShot(1500, app.quit)
        return app.exec_()

    creationflags = 0
    if os.name == "nt":
        try:
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        except Exception:
            creationflags = 0

    proc = subprocess.Popen(
        [sys.executable, main_path],
        cwd=os.path.dirname(main_path),
        stdout=None,
        stderr=None,
        creationflags=creationflags,
    )

    ready_seen = False
    def _kill_process_tree():
        try:
            import psutil
            try:
                p = psutil.Process(proc.pid)
                for child in p.children(recursive=True):
                    try:
                        child.terminate()
                    except Exception:
                        pass
                _, _ = psutil.wait_procs(p.children(recursive=True), timeout=3)
            except Exception:
                pass
        except Exception:
            pass
        try:
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], check=False, capture_output=True)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if os.name == "nt":
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    pass
            proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def check_state():
        nonlocal ready_seen
        try:
            has_window_pid = _find_main_window_for_pid(proc.pid)
        except Exception:
            has_window_pid = False
        try:
            has_window_any = _find_main_window()
        except Exception:
            has_window_any = False

        if not ready_seen and (has_window_pid or has_window_any):
            ready_seen = True
            try:
                splash.progress.setValue(100)
            except Exception:
                pass
            try:
                poll.stop()
            except Exception:
                pass
            QTimer.singleShot(50, splash.close)
        elif ready_seen and not (has_window_pid or has_window_any):
            if proc.poll() is None:
                _kill_process_tree()
            try:
                poll.stop()
            except Exception:
                pass
            QTimer.singleShot(100, app.quit)

    poll = QTimer(splash)
    poll.timeout.connect(check_state)
    poll.start(250)

    QTimer.singleShot(60000, lambda: app.quit())
    return app.exec_()

if __name__ == "__main__":
    launch()

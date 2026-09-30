"""
validate_license.py -- Standalone license validator for the arrow installer.

CLI modes:
  --install <INSTALLFOLDER>           Show GUI dialog, validate key, create license.bin
  --install <INSTALLFOLDER> --key K   Non-interactive activation (for testing)
  --check <INSTALLFOLDER>             Silent check of existing license.bin
  --reinstall <INSTALLFOLDER>         Force re-activation dialog

Exit codes:
  0  =  valid / success
  1  =  invalid / error
"""

import base64
import hashlib
import hmac
import json
import os
import random
import struct
import subprocess
import sys
import tkinter as tk
from tkinter import font as tkfont
from typing import Optional

# ---------------------------------------------------------------------------
# Inline core (duplicates license_core.py for self-contained compilation)
# ---------------------------------------------------------------------------

SALT = b"aRr0w_L1c3nS3_S4lT_2026#!K3yG3n"
HMAC_SECRET = hashlib.sha256(SALT + b"::aRr0w_Hm4c_S3cr3t_2026::").digest()
_SEED = int.from_bytes(hashlib.sha256(SALT).digest(), "big")
_rng = random.Random(_SEED)
SUBSTITUTION_BOX = bytes(_rng.sample(range(256), 256))
INVERSE_SUB_BOX = bytes([SUBSTITUTION_BOX.index(i) for i in range(256)])
LICENSE_MAGIC = b"ARWLIC\x00\x01"


def _run_wmic(query: str) -> str:
    try:
        result = subprocess.run(
            ["wmic", *query.split()],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and line.lower() not in ("serialnumber", ""):
                return line
    except Exception:
        pass
    return ""


def get_motherboard_serial() -> str:
    serial = _run_wmic("baseboard get serialnumber")
    if serial and serial not in ("To be filled by O.E.M.", "Default string", "0"):
        return serial
    uuid_val = _run_wmic("csproduct get uuid")
    if uuid_val:
        return uuid_val
    try:
        result = subprocess.run(
            ["vol", "C:"], capture_output=True, text=True, timeout=5, shell=True
        )
        for line in result.stdout.splitlines():
            if "Serial Number" in line or "serial" in line.lower():
                parts = line.split()
                for p in parts:
                    if "-" in p and len(p) >= 8:
                        return p
    except Exception:
        pass
    import uuid as _uuid_mod
    return "FALLBACK-" + str(_uuid_mod.uuid4())


def _verify_inner_key(inner_key: str) -> Optional[dict]:
    """HMAC-SHA256 verification of inner key. Returns payload or None."""
    try:
        parts = inner_key.strip().split("|")
        if len(parts) != 2:
            return None
        payload_b64, hmac_b64 = parts
        payload_bytes = base64.b64decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))

        expected = hashlib.pbkdf2_hmac(
            "sha256", payload_bytes, HMAC_SECRET, iterations=1000, dklen=32
        )
        provided = base64.b64decode(hmac_b64)

        if not hmac.compare_digest(expected, provided):
            return None

        expires = payload.get("expires")
        if expires:
            from datetime import datetime as _dt
            exp_date = _dt.strptime(expires, "%Y-%m-%d")
            if _dt.now() > exp_date:
                return None

        return payload
    except Exception:
        return None


def _derive_key(mobo_serial: str, seed: str) -> bytes:
    return hashlib.sha256(
        mobo_serial.encode("utf-8") + SALT + seed.encode("utf-8")
    ).digest()


def _xor_cipher(data: bytes, key: bytes) -> bytes:
    result = bytearray()
    for i, b in enumerate(data):
        result.append(b ^ key[i % len(key)])
    return bytes(result)


def _substitute(data: bytes, box: bytes) -> bytes:
    return bytes(box[b] for b in data)


def _inverse_substitute(data: bytes) -> bytes:
    return bytes(INVERSE_SUB_BOX[b] for b in data)


def _shuffle(data: bytes) -> bytes:
    h = hashlib.sha256(data).digest()
    seed = struct.unpack("<Q", h[:8])[0]
    rng_local = random.Random(seed)
    indices = list(range(len(data)))
    rng_local.shuffle(indices)
    shuffled = bytearray(len(data))
    for new_pos, old_pos in enumerate(indices):
        shuffled[new_pos] = data[old_pos]
    return bytes(shuffled)


def _unshuffle(data: bytes) -> bytes:
    h = hashlib.sha256(data).digest()
    seed = struct.unpack("<Q", h[:8])[0]
    rng_local = random.Random(seed)
    indices = list(range(len(data)))
    rng_local.shuffle(indices)
    unshuffled = bytearray(len(data))
    for new_pos, old_pos in enumerate(indices):
        unshuffled[old_pos] = data[new_pos]
    return bytes(unshuffled)


def create_hardware_bound_license(mobo_serial: str, license_key: str) -> bytes:
    seed = license_key.split("|")[0] if "|" in license_key else license_key
    key = _derive_key(mobo_serial, seed)

    layer0 = hashlib.sha256(
        mobo_serial.encode("utf-8") + SALT + seed.encode("utf-8")
    ).digest()

    layer1 = _xor_cipher(layer0, key)
    layer2 = _substitute(layer1, SUBSTITUTION_BOX)
    layer3 = base64.b64encode(layer2)
    layer4 = _shuffle(layer3)
    layer5 = base64.b64encode(layer4)

    payload = struct.pack("<I", len(layer5)) + layer5
    return LICENSE_MAGIC + struct.pack("<B", 1) + payload


def verify_hardware_bound_license(license_data: bytes, license_key: str) -> bool:
    try:
        if not license_data.startswith(LICENSE_MAGIC):
            return False
        offset = len(LICENSE_MAGIC) + 1
        payload_len = struct.unpack("<I", license_data[offset : offset + 4])[0]
        offset += 4
        layer5 = license_data[offset : offset + payload_len]

        layer4 = base64.b64decode(layer5)
        layer3 = _unshuffle(layer4)
        layer2 = base64.b64decode(layer3)
        layer1 = _inverse_substitute(layer2)

        mobo_serial = get_motherboard_serial()
        seed = license_key.split("|")[0] if "|" in license_key else license_key
        key = _derive_key(mobo_serial, seed)

        layer0 = _xor_cipher(layer1, key)
        expected = hashlib.sha256(
            mobo_serial.encode("utf-8") + SALT + seed.encode("utf-8")
        ).digest()

        return layer0 == expected
    except Exception:
        return False


def parse_formatted_key(formatted: str) -> Optional[str]:
    """Reverse ARW-XXXXX-... -> inner key string."""
    try:
        stripped = formatted.strip()
        if stripped.startswith("ARW-"):
            stripped = stripped[4:]
        b32 = stripped.replace("-", "").replace(" ", "")
        remainder = len(b32) % 8
        if remainder:
            b32 += "=" * (8 - remainder)
        raw = base64.b32decode(b32).decode("ascii")
        return raw
    except Exception:
        return None


# ---------------------------------------------------------------------------
# GUI (tkinter)
# ---------------------------------------------------------------------------


class LicenseDialog:
    def __init__(self, install_folder: str):
        self.install_folder = install_folder
        self.result = False

        self.root = tk.Tk()
        self.root.title("arrow License Activation")
        self.root.resizable(False, False)
        self.root.configure(bg="#1e1e1e")

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w, h = 520, 360
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        self.root.attributes("-topmost", True)
        self.root.after(500, lambda: self.root.attributes("-topmost", False))
        self._build_ui()

    def _build_ui(self):
        title_font = tkfont.Font(family="Segoe UI", size=16, weight="bold")
        tk.Label(
            self.root, text="arrow License Activation", font=title_font,
            fg="#ffffff", bg="#1e1e1e",
        ).pack(pady=(30, 5))

        sub_font = tkfont.Font(family="Segoe UI", size=10)
        tk.Label(
            self.root, text="Enter your license key to activate arrow on this machine.",
            font=sub_font, fg="#aaaaaa", bg="#1e1e1e",
        ).pack(pady=(0, 20))

        entry_font = tkfont.Font(family="Consolas", size=12)
        self.key_var = tk.StringVar()

        entry_frame = tk.Frame(self.root, bg="#1e1e1e")
        entry_frame.pack(pady=5)

        self.entry = tk.Entry(
            entry_frame, textvariable=self.key_var, font=entry_font,
            width=35, bd=0, fg="#d4d4d4", bg="#2d2d2d",
            insertbackground="#d4d4d4", justify="center",
        )
        self.entry.pack(ipady=8, ipadx=5)
        self.entry.focus_set()
        self.entry.bind("<Return>", lambda e: self._activate())

        hint_font = tkfont.Font(family="Segoe UI", size=9)
        tk.Label(
            self.root, text="Format: ARW-XXXXX-XXXXX-XXXXX-XXXXX",
            font=hint_font, fg="#666666", bg="#1e1e1e",
        ).pack(pady=(2, 15))

        self.status_var = tk.StringVar()
        self.status_label = tk.Label(
            self.root, textvariable=self.status_var,
            font=hint_font, fg="#ff6b6b", bg="#1e1e1e", wraplength=460,
        )
        self.status_label.pack(pady=(0, 10))

        btn_frame = tk.Frame(self.root, bg="#1e1e1e")
        btn_frame.pack(pady=10)

        btn_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")

        tk.Button(
            btn_frame, text="Activate", font=btn_font,
            bg="#0d7377", fg="#ffffff", activebackground="#0a5c5f",
            activeforeground="#ffffff", bd=0, padx=25, pady=6,
            cursor="hand2", command=self._activate,
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame, text="Exit", font=btn_font,
            bg="#444444", fg="#ffffff", activebackground="#555555",
            activeforeground="#ffffff", bd=0, padx=25, pady=6,
            cursor="hand2", command=self._exit,
        ).pack(side="left", padx=5)

    def _activate(self):
        raw_key = self.key_var.get().strip()
        if not raw_key:
            self.status_var.set("Please enter a license key.")
            self.status_label.config(fg="#ff6b6b")
            return

        inner = parse_formatted_key(raw_key)
        if not inner:
            self.status_var.set("Invalid key format. Check and try again.")
            self.status_label.config(fg="#ff6b6b")
            return

        try:
            result = _activate_with_key(self.install_folder, inner)
        except Exception as e:
            self.status_var.set(f"Error: {e}")
            self.status_label.config(fg="#ff6b6b")
            self.root.update_idletasks()
            return

        if result == 0:
            self.status_var.set("License activated successfully!\nBound to this machine.")
            self.status_label.config(fg="#4ec9b0")
            self.root.after(1500, self._accept)
        else:
            self.status_var.set("License activation failed. Check the key or contact support.")
            self.status_label.config(fg="#ff6b6b")

    def _accept(self):
        self.result = True
        self.root.destroy()

    def _exit(self):
        self.result = False
        self.root.destroy()

    def run(self) -> bool:
        self.root.mainloop()
        return self.result


# ---------------------------------------------------------------------------
# Core activation logic
# ---------------------------------------------------------------------------


def _log(msg: str):
    """Write to stderr if available (frozen --noconsole may have None)."""
    try:
        if sys.stderr is not None:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
    except Exception:
        pass


def _activate_with_key(install_folder: str, inner_key: str) -> int:
    """Validate the inner key and create license.bin. Returns 0 on success."""
    payload = _verify_inner_key(inner_key)
    if payload is None:
        _log("ERROR: Invalid or expired license key.")
        return 1

    mobo = get_motherboard_serial()
    blob = create_hardware_bound_license(mobo, inner_key)
    license_path = os.path.join(install_folder, "license.bin")
    try:
        os.makedirs(install_folder, exist_ok=True)
    except Exception as e:
        _log(f"ERROR: Cannot create {install_folder}: {e}")
        raise
    try:
        with open(license_path, "wb") as f:
            f.write(blob)
    except Exception as e:
        _log(f"ERROR: Cannot write {license_path}: {e}")
        raise
    _log(f"License activated for {payload.get('user')} bound to this machine.")
    return 0


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def cmd_install(install_folder: str) -> int:
    """GUI dialog. Returns 0 on success."""
    # Normalize path: Windows command-line parsing can mangle trailing backslash + quote
    install_folder = os.path.normpath(install_folder.strip().strip('\"'))
    license_path = os.path.join(install_folder, "license.bin")
    if os.path.exists(license_path):
        try:
            with open(license_path, "rb") as f:
                if f.read().startswith(LICENSE_MAGIC):
                    return 0
        except Exception:
            pass

    dialog = LicenseDialog(install_folder)
    return 0 if dialog.run() else 1


def cmd_check(install_folder: str) -> int:
    """Silent check of license.bin. Returns 0 if valid."""
    install_folder = os.path.normpath(install_folder.strip().strip('\"'))
    license_path = os.path.join(install_folder, "license.bin")
    if not os.path.exists(license_path):
        print("license.bin not found.")
        return 1
    try:
        with open(license_path, "rb") as f:
            data = f.read()
        if data.startswith(LICENSE_MAGIC):
            return 0
        print("license.bin has invalid format.")
    except Exception as e:
        print(f"Cannot read license.bin: {e}")
    return 1


def cmd_reinstall(install_folder: str) -> int:
    install_folder = os.path.normpath(install_folder.strip().strip('\"'))
    dialog = LicenseDialog(install_folder)
    return 0 if dialog.run() else 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  validate_license --install <INSTALLFOLDER>")
        print("  validate_license --install <INSTALLFOLDER> --key <LICENSE_KEY>")
        print("  validate_license --check <INSTALLFOLDER>")
        print("  validate_license --reinstall <INSTALLFOLDER>")
        sys.exit(1)

    mode = sys.argv[1]
    # Normalize path: strip trailing backslash + quote from command-line parsing
    folder = os.path.normpath(sys.argv[2].strip().strip('\"'))

    key_arg = None
    for i, arg in enumerate(sys.argv):
        if arg == "--key" and i + 1 < len(sys.argv):
            key_arg = sys.argv[i + 1]
            break

    if mode == "--install":
        if key_arg:
            inner = parse_formatted_key(key_arg)
            if inner is None:
                print("ERROR: Invalid key format.")
                sys.exit(1)
            sys.exit(_activate_with_key(folder, inner))
        sys.exit(cmd_install(folder))
    elif mode == "--check":
        sys.exit(cmd_check(folder))
    elif mode == "--reinstall":
        sys.exit(cmd_reinstall(folder))
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()

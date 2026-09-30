"""
license_core.py -- Shared cryptographic core for the arrow licensing system.

Provides key generation, license signing/verification, hardware fingerprint
extraction (Windows), and multi-layer obfuscation for motherboard-bound
license files.

The license key is an HMAC-SHA256 signed payload (compact enough to type).
The hardware binding uses 6 layers of obfuscation.
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
from datetime import datetime
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SALT = b"aRr0w_L1c3nS3_S4lT_2026#!K3yG3n"
HMAC_SECRET = hashlib.sha256(SALT + b"::aRr0w_Hm4c_S3cr3t_2026::").digest()

# Seeded 256-byte substitution box (generated deterministically from SALT)
_SEED = int.from_bytes(hashlib.sha256(SALT).digest(), "big")
rng = random.Random(_SEED)
SUBSTITUTION_BOX = bytes(rng.sample(range(256), 256))
INVERSE_SUB_BOX = bytes([SUBSTITUTION_BOX.index(i) for i in range(256)])

LICENSE_MAGIC = b"ARWLIC\x00\x01"
LICENSE_VERSION = 1


# ---------------------------------------------------------------------------
# License key creation and verification (HMAC-SHA256 based)
# ---------------------------------------------------------------------------


def create_license_payload(
    user: str, issue: str, expires: Optional[str] = None, hw_limit: int = 1
) -> dict:
    """Build a license payload dict."""
    payload = {"user": user, "issue": issue, "hw_limit": hw_limit}
    if expires:
        payload["expires"] = expires
    return payload


def sign_license_payload(payload: dict) -> str:
    """Sign a payload dict with HMAC-SHA256.

    Returns the raw key string (payload_b64 + HMAC).
    This is the inner format before formatting for display.
    """
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.b64encode(payload_bytes).decode("ascii")
    hmac_digest = hashlib.pbkdf2_hmac(
        "sha256", payload_bytes, HMAC_SECRET, iterations=1000, dklen=32
    )
    hmac_b64 = base64.b64encode(hmac_digest).decode("ascii")
    return payload_b64 + "|" + hmac_b64


def verify_license_key(inner_key: str) -> Optional[dict]:
    """Verify an inner license key (payload_b64|HMAC_b64).

    Returns the payload dict on success, None on failure.
    """
    try:
        parts = inner_key.strip().split("|")
        if len(parts) != 2:
            return None
        payload_b64, hmac_b64 = parts
        payload_bytes = base64.b64decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))

        expected_hmac = hashlib.pbkdf2_hmac(
            "sha256", payload_bytes, HMAC_SECRET, iterations=1000, dklen=32
        )
        provided_hmac = base64.b64decode(hmac_b64)

        if not hmac.compare_digest(expected_hmac, provided_hmac):
            return None

        # Check expiration
        expires = payload.get("expires")
        if expires:
            exp_date = datetime.strptime(expires, "%Y-%m-%d")
            if datetime.now() > exp_date:
                return None

        return payload
    except Exception:
        return None


def format_license_key(inner_key: str) -> str:
    """Format the inner key into a human-friendly display string.

    Returns: ARW-XXXXX-XXXXX-XXXXX-XXXXX
    """
    raw_bytes = inner_key.encode("ascii")
    b32 = base64.b32encode(raw_bytes).decode("ascii").rstrip("=")
    groups = [b32[i : i + 5] for i in range(0, len(b32), 5)]
    return "ARW-" + "-".join(groups)


def parse_formatted_key(formatted: str) -> Optional[str]:
    """Reverse *format_license_key* -> the inner key string."""
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
# Hardware fingerprint
# ---------------------------------------------------------------------------


def _run_wmic(query: str) -> str:
    """Run a WMIC query and return the first non-empty result."""
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
    """Get motherboard serial number via WMIC, with fallbacks."""
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


def get_hardware_fingerprint() -> str:
    """Combined hardware fingerprint."""
    mobo = get_motherboard_serial()
    try:
        cpu = _run_wmic("cpu get processorid")
    except Exception:
        cpu = ""
    return f"{mobo}|{cpu}"


# ---------------------------------------------------------------------------
# Multi-layer hardware-bound license obfuscation
# ---------------------------------------------------------------------------


def _derive_key(mobo_serial: str, license_key_seed: str) -> bytes:
    return hashlib.sha256(
        mobo_serial.encode("utf-8") + SALT + license_key_seed.encode("utf-8")
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
    """Create a 6-layer obfuscated hardware-bound license blob.

    Layers: SHA-256 -> XOR -> Substitution -> B64 -> Shuffle -> B64
    Returns binary blob: MAGIC + VERSION + LENGTH(4) + PAYLOAD
    """
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
    return LICENSE_MAGIC + struct.pack("<B", LICENSE_VERSION) + payload


def verify_hardware_bound_license(license_data: bytes, license_key: str) -> bool:
    """Verify a hardware-bound license blob against the current machine."""
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


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def generate_license_blob_path(install_folder: str) -> str:
    return os.path.join(install_folder, "license.bin")


def write_license_blob(install_folder: str, blob: bytes):
    path = generate_license_blob_path(install_folder)
    with open(path, "wb") as f:
        f.write(blob)


def read_license_blob(install_folder: str) -> Optional[bytes]:
    path = generate_license_blob_path(install_folder)
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None

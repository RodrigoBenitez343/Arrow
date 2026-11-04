"""Platform-gated packaging for Arrow.

`pip install .` resolves the dependency set for the host OS:

  * common  - everything both platforms use (pynput, pyautogui, opencv, ...)
  * Windows - Win32 bindings (pywin32, comtypes) for window/UIA control
  * Linux   - python-xlib for X11 window/input control; the element tree
              needs AT-SPI2, which is a DISTRO package, not a pip one
              (at-spi2-core + python3-pyatspi, or python3-gi with
              gir1.2-atspi-2.0). `LoOper/platform_gate.py` refuses to start
              without it instead of degrading to vision-only clicks.

The runtime half of the gate lives in `LoOper/platform_gate.py`: it selects the
matching backend at import time, so the code needs no per-end-user setup.
"""
import pathlib
import sys

from packaging.requirements import Requirement
from setuptools import find_packages, setup

ROOT = pathlib.Path(__file__).parent

# Extra requirements per platform (beyond what pyproject.toml declares).
PLATFORM_REQUIRES = {
    "win32": [
        "pywin32==311",
        "pywin32-ctypes==0.2.3",
        "pypiwin32==223",
        "comtypes==1.4.13",
    ],
    # AT-SPI2 is installed by the distro (at-spi2-core + python3-pyatspi), so
    # only the X11 binding is a pip dependency here.
    "linux": [
        "python-xlib==0.33",
    ],
}

# Packages that only make sense on Windows, even when pyproject.toml leaves
# them unmarked (they install fine on Linux but import nothing useful).
WINDOWS_ONLY = frozenset({
    "pypiwin32", "pywin32", "pywin32-ctypes", "comtypes", "pyreadline3",
    "win32-setctime", "polars-runtime-32", "pymsgbox",
})


def _platform_key() -> str:
    if sys.platform == "win32":
        return "win32"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def _project_table() -> dict:
    """Read the [project] table from pyproject.toml (stdlib only)."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    table = {}
    try:
        import tomllib

        table = tomllib.loads(text)["project"]
    except Exception:
        # Python 3.10 has no tomllib: the few fields setup() needs here are
        # simple scalars, so a line scan is enough.
        for line in text.splitlines():
            for key in ("name", "version", "description", "requires-python"):
                if line.startswith(f"{key} =") or line.startswith(f'{key} ='):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    table[key] = value
        table["dependencies"] = _dependencies_block(text)
    return table


def _dependencies_block(text: str):
    try:
        block = text.split("dependencies = [", 1)[1].split("\n]", 1)[0]
    except IndexError:
        return []
    out = []
    for line in block.splitlines():
        line = line.strip().rstrip(",").strip()
        if line:
            out.append(line.strip('"').strip("'"))
    return out


def install_requires():
    """Dependencies valid for THIS host, markers already evaluated."""
    key = _platform_key()
    deps: dict[str, str] = {}
    for raw in _project_table().get("dependencies") or []:
        req = Requirement(raw)
        name = req.name.lower()
        if req.marker is not None and not req.marker.evaluate():
            continue
        if name in WINDOWS_ONLY and key != "win32":
            continue
        deps.setdefault(name, raw)
    for extra in PLATFORM_REQUIRES.get(key, []):
        deps.setdefault(Requirement(extra).name.lower(), extra)
    return list(deps.values())


if __name__ == "__main__":
    table = _project_table()
    chosen = install_requires()
    print(
        f"[setup] platform={_platform_key()} dependencies={len(chosen)}",
        file=sys.stderr,
    )
    setup(
        name=table.get("name", "arrow"),
        version=table.get("version", "0.0.0"),
        description=table.get("description", "Arrow desktop automation"),
        python_requires=table.get("requires-python", ">=3.10"),
        license=table.get("license", "AGPL-3.0-or-later"),
        packages=find_packages(include=["LoOper*", "OverWatch*"]),
        include_package_data=True,
        install_requires=chosen,
    )

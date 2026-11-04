"""Apply vendored third-party library patches into the active environment.

Each file under this folder mirrors a path inside ``site-packages`` (for
example ``NodeGraphQt/qgraphics/node_base.py``) and is copied over the same
relative path in the running interpreter's site-packages.

Why this exists: ``pip install`` always drops the *stock* wheel.  The vendored
NodeGraphQt copies carry the LoOper canvas look and its crash guards: uniform
rounded-square icon-only node cards, port stacks centred on the card edge, and
no ``viewer()``/proxy-mode paint crash during node teardown.  Without them the
stock wheel draws title bars, inward port labels and ports that hang off the
card ("floating things").  Any reinstall restores the buggy wheel, so the
fixed files must be re-copied after every install.  Three callers:

* ``Arrow_setup.bat`` - after provisioning the venv
* ``LoOper/main.py``   - on every launch (dev mode, before NodeGraphQt loads)
* ``build_pipeline/build_msi.ps1`` - before PyInstaller, so the frozen build
  (which compiles site-packages into its PYZ archive) ships the patched code

Idempotent and safe to re-run.  Only an already-installed target is
overwritten; a package that is missing is skipped, never created.

Usage (normally invoked by Arrow_setup.bat):
    python LoOper/patches/apply_patches.py            # apply in place
    python LoOper/patches/apply_patches.py --check    # verify only (exit 1 if stale)
    python LoOper/patches/apply_patches.py <path>     # apply to a given site-packages
"""
from __future__ import annotations

import filecmp
import shutil
import sys
import sysconfig
from pathlib import Path

HERE = Path(__file__).resolve().parent
SELF = Path(__file__).resolve()


def site_packages(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    return Path(sysconfig.get_paths()["purelib"])


def patch_map() -> dict:
    """{relative_path: source_file} for every vendored patch file."""
    out: dict = {}
    for src in sorted(HERE.rglob("*")):
        if not src.is_file() or src.resolve() == SELF:
            continue
        if "__pycache__" in src.parts or src.suffix == ".pyc":
            continue
        out[src.relative_to(HERE)] = src
    return out


def scan(dest: Path) -> tuple:
    """(stale, current, missing) - each a list of (relative_path, source_file)."""
    stale, current, missing = [], [], []
    for rel, src in patch_map().items():
        dst = dest / rel
        if not dst.parent.is_dir():          # owning package not installed
            missing.append((rel, src))
        elif dst.is_file() and filecmp.cmp(src, dst, shallow=False):
            current.append((rel, src))
        else:
            stale.append((rel, src))
    return stale, current, missing


def apply_patches(dest: Path) -> list:
    """Copy every stale patch over ``dest``; return the rewritten rel paths.

    Silent variant used by ``LoOper/main.py`` at launch (and by the MSI build),
    so a fresh ``pip install`` never leaves the UI on stock wheels.
    """
    stale, _current, _missing = scan(dest)
    for rel, src in stale:
        shutil.copyfile(src, dest / rel)
    return [rel for rel, _src in stale]


def run(check: bool, dest: Path) -> int:
    stale, current, missing = scan(dest)
    if stale and not check:
        apply_patches(dest)

    verb = "stale" if check else "patched"
    for rel, _src in stale:
        print(f"  [{verb}] {rel}")
    for rel, _src in current:
        print(f"  [ok]    {rel}")
    for rel, _src in missing:
        print(f"  [skip]  {rel}  (package not installed)")
    print(f"[patches] {len(stale)} {verb}, {len(current)} already ok, "
          f"{len(missing)} skipped -> {dest}")
    return 1 if (check and stale) else 0


def main() -> int:
    args = sys.argv[1:]
    check = "--check" in args
    rest = [a for a in args if not a.startswith("--")]
    return run(check, site_packages(rest[0] if rest else None))


if __name__ == "__main__":
    raise SystemExit(main())

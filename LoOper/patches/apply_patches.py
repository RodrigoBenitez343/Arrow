"""Apply vendored third-party library patches into the active environment.

Each file under this folder mirrors a path inside ``site-packages`` (for
example ``NodeGraphQt/qgraphics/node_base.py``) and is copied over the same
relative path in the running interpreter's site-packages.

Why this exists: ``pip install`` always drops the *stock* wheel.  NodeGraphQt
0.6.43 lays port labels out inward so they overlap the node body; the vendored
copies fix the label X positions (``_align_ports_horizontal``).  Because a
fresh setup (or any reinstall) restores the buggy wheel, the fixed files must
be re-copied after every install -- Arrow_setup.bat does that automatically.

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


def run(check: bool, dest: Path) -> int:
    applied, current, missing = [], [], []
    for rel, src in patch_map().items():
        dst = dest / rel
        if not dst.parent.is_dir():          # owning package not installed
            missing.append(rel)
            continue
        if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
            current.append(rel)
            continue
        if not check:
            shutil.copyfile(src, dst)
        applied.append(rel)

    verb = "stale" if check else "patched"
    for rel in applied:
        print(f"  [{verb}] {rel}")
    for rel in current:
        print(f"  [ok]    {rel}")
    for rel in missing:
        print(f"  [skip]  {rel}  (package not installed)")
    print(f"[patches] {len(applied)} {verb}, {len(current)} already ok, "
          f"{len(missing)} skipped -> {dest}")
    return 1 if (check and applied) else 0


def main() -> int:
    args = sys.argv[1:]
    check = "--check" in args
    rest = [a for a in args if not a.startswith("--")]
    return run(check, site_packages(rest[0] if rest else None))


if __name__ == "__main__":
    raise SystemExit(main())

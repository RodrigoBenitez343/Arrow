"""
ChainRegistry — Scans and describes all available chain tools.
Each chain JSON file is treated as a callable tool with a description.
"""

import json
import os
from typing import Any, Dict, List, Optional


def _make_id(base: str, used: set) -> str:
    """Generate a unique id from a filename, deduplicating against used set."""
    tid = "".join(ch if (ch.isalnum() or ch in ("_", "-")) else "_" for ch in base)
    if not tid:
        tid = "chain"
    orig = tid
    idx = 2
    while tid in used:
        tid = f"{orig}_{idx}"
        idx += 1
    used.add(tid)
    return tid


class ChainRegistry:
    """Scans a chains directory and provides tool descriptions for the agent."""

    def __init__(self, chains_folder: str):
        self._folder = chains_folder
        self._chains: List[Dict[str, Any]] = []
        self._scan()

    # ── public api ───────────────────────────────────────────────

    def list_chains(self) -> List[Dict[str, Any]]:
        """Return all discovered chains as list of dicts."""
        return list(self._chains)

    def find_by_id(self, chain_id: str) -> Optional[Dict[str, Any]]:
        """Find a chain by its generated id."""
        for c in self._chains:
            if c.get("id") == chain_id:
                return c
        return None

    def find_by_filename(self, filename: str) -> Optional[Dict[str, Any]]:
        """Find a chain by its raw filename (e.g. 'gotolinkedin.json')."""
        for c in self._chains:
            if c.get("filename") == filename:
                return c
        return None

    def count(self) -> int:
        return len(self._chains)

    def format_tools_for_prompt(self) -> str:
        """Format all chains as a tool list for LLM consumption."""
        if not self._chains:
            return "No chains available."

        lines = ["Available chains (tools):"]
        for c in self._chains:
            tid = c.get("id", "?")
            desc = c.get("description", "No description available")
            # Truncate long descriptions
            if len(desc) > 200:
                desc = desc[:197] + "..."
            lines.append(f"  - {tid}: {desc}")
        lines.append("")
        lines.append("Special actions:")
        lines.append('  - sys_type_text: Type arbitrary text at the current cursor position.')
        lines.append('  - sys_wait: Wait for a few seconds (e.g. for page to load).')
        lines.append('  - sys_done: Signal that the task is complete.')
        return "\n".join(lines)

    def refresh(self) -> None:
        """Re-scan the chains folder (e.g. after adding a new chain)."""
        self._scan()

    # ── internal ─────────────────────────────────────────────────

    def _scan(self) -> None:
        if not self._folder or not os.path.isdir(self._folder):
            self._chains = []
            return

        files = sorted(
            [f for f in os.listdir(self._folder) if f.lower().endswith(".json")]
        )
        used_ids: set = set()
        chains = []

        for fname in files:
            fpath = os.path.join(self._folder, fname)
            base = os.path.splitext(fname)[0]
            display_name = base.replace("_", " ").replace("-", " ").title()
            description = ""

            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                desc_raw = (data.get("description") or "").strip()
                if desc_raw:
                    description = desc_raw
                    first_sentence = desc_raw.split(".")[0].strip()
                    if 5 < len(first_sentence) < 80:
                        display_name = first_sentence
            except Exception:
                pass

            chain_id = _make_id(base, used_ids)

            chains.append(
                {
                    "id": chain_id,
                    "name": display_name,
                    "description": description,
                    "path": fpath,
                    "filename": fname,
                }
            )

        self._chains = chains

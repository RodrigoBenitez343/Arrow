"""
ChainBuilder — Creates chain JSON files from recorded action sequences.

Builds the chain structure that the MultiSequencePlayer expects:
- A chain JSON file referencing sequence files (recorded action files)
- Individual sequence JSON files containing the raw actions
- Proper node IDs, positions, empty arrays for unused node types
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from .chain_registry import ChainRegistry

logger = logging.getLogger(__name__)


def _generate_node_id() -> str:
    """Generate a unique node ID like 0x1a47a4dba90."""
    return f"0x{uuid.uuid4().hex[:12]}"


def _generate_sequence_filename(prefix: str = "guided") -> str:
    """Generate a unique sequence filename."""
    ts = int(time.time() * 1000)
    return f"{prefix}_{ts}.json"


class ChainBuilder:
    """Builds chain JSON + sequence JSON files from recorded actions.

    Typical usage:
        builder = ChainBuilder(chains_dir, sequences_dir)
        chain_path = builder.build_chain(
            chain_id="my_new_chain",
            description="what this chain does",
            actions=action_executor.get_recorded_actions(),
        )
    """

    def __init__(
        self,
        chains_dir: str,
        sequences_dir: str,
        registry: Optional[ChainRegistry] = None,
    ):
        """
        Args:
            chains_dir: Absolute path to the chains/ folder.
            sequences_dir: Absolute path to the sequences/ folder.
            registry: Optional ChainRegistry to refresh after creating a chain.
        """
        self._chains_dir = chains_dir
        self._sequences_dir = sequences_dir
        self._registry = registry

    def build_chain(
        self,
        chain_id: str,
        description: str,
        actions: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Create a chain JSON file from recorded actions.

        Args:
            chain_id: Short identifier for the chain (e.g. "open_gmail").
            description: Human-readable description of what the chain does.
            actions: List of recorded action dicts from ActionExecutor.

        Returns:
            Absolute path to the created chain JSON, or None on failure.
        """
        if not actions:
            logger.warning("No actions to build chain '%s'", chain_id)
            return None

        os.makedirs(self._sequences_dir, exist_ok=True)
        os.makedirs(self._chains_dir, exist_ok=True)

        # ── 1. Save sequence file from recorded actions ──────
        seq_filename = _generate_sequence_filename(chain_id)
        seq_path = os.path.join(self._sequences_dir, seq_filename)

        sequence_data = {
            "metadata": {
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_actions": len(actions),
                "duration_sec": round(sum(
                    a.get("duration", 0.0) if a.get("type") == "wait" else 1.0
                    for a in actions
                ), 2),
                "mode": "desktop_only",
                "source": "agent_guided",
            },
            "actions": actions,
        }

        try:
            with open(seq_path, "w", encoding="utf-8") as f:
                json.dump(sequence_data, f, indent=4)
            logger.info("Saved sequence: %s", seq_path)
        except Exception as e:
            logger.error("Failed to save sequence: %s", e)
            return None

        # ── 2. Build chain JSON ─────────────────────────────
        seq_node_id = _generate_node_id()

        chain_data = {
            "sequences": [
                {
                    "name": seq_filename,
                    "sequence_file": seq_filename,
                    "loop_count": 1,
                    "extra_delay": 0.5,
                    "use_app_opened": True,
                    "actions": [],
                    "node_id": seq_node_id,
                    "position": [600.0, 200.0],
                    "connections": [],
                }
            ],
            "conditional_nodes": [],
            "llm_nodes": [],
            "tts_nodes": [],
            "chain_import_nodes": [],
            "form_filler_nodes": [],
            "code_nodes": [],
            "container_nodes": [],
            "context_nodes": [],
            "description": description.strip(),
        }

        # ── 3. Write chain JSON ─────────────────────────────
        chain_filename = f"{chain_id}.json"
        chain_path = os.path.join(self._chains_dir, chain_filename)

        try:
            with open(chain_path, "w", encoding="utf-8") as f:
                json.dump(chain_data, f, indent=4)
            logger.info("Saved chain: %s", chain_path)
        except Exception as e:
            logger.error("Failed to save chain: %s", e)
            # Clean up orphaned sequence file
            try:
                os.remove(seq_path)
            except Exception:
                pass
            return None

        # ── 4. Refresh registry if available ────────────────
        if self._registry:
            try:
                self._registry.refresh()
            except Exception:
                pass

        return chain_path

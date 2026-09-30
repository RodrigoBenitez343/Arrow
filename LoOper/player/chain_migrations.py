"""Load-time migration of legacy chain configurations.

The standalone TTS node type is removed.  ``migrate_legacy_tts`` converts
every legacy ``tts_nodes`` entry (top-level, or inlined inside a
``chain_import_nodes`` entry) into an Output node with ``render_mode='audio'``
so old chains keep behaving identically.  Migration is in-memory only —
files are rewritten when the user resaves from the editor.
"""

# Keys that mark a dict as an embedded chain schema (chain-import entries
# inline the imported chain's config under these keys).
_CHAIN_SCHEMA_KEYS = (
    "sequences",
    "tts_nodes",
    "conditional_nodes",
    "llm_nodes",
    "chain_import_nodes",
    "code_nodes",
    "container_nodes",
    "context_nodes",
    "input_nodes",
    "handle_nodes",
    "mcp_nodes",
    "output_nodes",
    "web_sequences",
)


def migrate_legacy_tts(chain_config):
    """Convert legacy ``tts_nodes`` entries into audio Output nodes in place.

    Recurses into chain-import embedded configs.  Idempotent: the
    ``tts_nodes`` key is deleted after conversion, so a second pass is a
    no-op.  Returns the number of entries converted.
    """
    if not isinstance(chain_config, dict):
        return 0

    converted = 0
    tts_entries = chain_config.get("tts_nodes")
    if isinstance(tts_entries, list) and tts_entries:
        output_nodes = chain_config.setdefault("output_nodes", [])
        for entry in tts_entries:
            if not isinstance(entry, dict):
                continue
            output_nodes.append({
                "type": "output",
                "render_mode": "audio",
                "tts_enabled": True,
                "tts_text": entry.get("text", ""),
                "tts_language": entry.get("language", "en"),
                "tts_voice_model": entry.get("voice_model", ""),
                "tts_speed": entry.get("speed", 1.0),
                "tts_speaker_id": entry.get("speaker_id"),
                "tts_wait": True,  # preserve sequential greeting behavior
                "agent_visible": False,
                "popup_on_finish": False,
                "node_id": entry.get("node_id"),
                "position": entry.get("position", [0, 0]),
                "connections": entry.get("connections", []),
            })
            converted += 1
        del chain_config["tts_nodes"]

    for imp in chain_config.get("chain_import_nodes", []) or []:
        if isinstance(imp, dict) and any(k in imp for k in _CHAIN_SCHEMA_KEYS):
            converted += migrate_legacy_tts(imp)

    return converted

import json
import logging
import os
logger = logging.getLogger(__name__)
try:
    from logging_setup import log_block
except Exception:
    def log_block(logger, level, title, content, lang="text"):
        text = content if isinstance(content, str) else repr(content)
        logger.log(level, "=== %s ===\n%s", title, text)


class OutputMixin:
    """Execution logic for Output nodes — collect upstream data and expose as chain result.

    An Output node is a sink that collects data from all connected upstream
    nodes.  The collected content is stored as a variable so that:

    * **Agent mode** — ``chain_executor.py`` can read it and return it as the
      chain's conversational response (shown in the overlay).
    * **Manual mode** — ``main_window.py`` can read it and show a popup after
      the chain finishes.
    * **Nested chains** — the parent chain's Output node can bridge results
      from sub-chains executed via ChainImport.
    """

    def _execute_output_node(self, node, stop_flag):
        """Execute an Output node: collect upstream data and store as result.

        Args:
            node (dict): The Output node from the workflow graph.
            stop_flag (callable): Stop flag function.

        Returns:
            str: Next node ID to execute, or ``__done__``.
        """
        node_data = node.get('data', {})
        node_id = (node.get('id') or node.get('node_id')
                   or node_data.get('node_id') or node_data.get('id'))

        label = node_data.get('label', '') or ''
        agent_visible = bool(node_data.get('agent_visible', True))
        overlay_visible = bool(node_data.get('overlay_visible', True))
        popup_on_finish = bool(node_data.get('popup_on_finish', True))
        show_rating = bool(node_data.get('show_rating', True))
        render_mode = str(node_data.get('render_mode', 'text') or 'text')
        tts_enabled = bool(node_data.get('tts_enabled', render_mode == 'audio'))
        tts_text = node_data.get('tts_text', '') or ''
        tts_language = node_data.get('tts_language', 'en') or 'en'
        tts_voice_model = node_data.get('tts_voice_model', '') or ''
        tts_speed = node_data.get('tts_speed', 1.0)
        tts_speaker_id = node_data.get('tts_speaker_id')
        tts_wait = bool(node_data.get('tts_wait', render_mode == 'audio'))
        image_source = node_data.get('image_source', '') or ''

        logger.info(
            "[OUTPUT] Output node %s (label='%s', agent_visible=%s, overlay_visible=%s, popup_on_finish=%s, show_rating=%s, render_mode=%s) executing",
            node_id, label, agent_visible, overlay_visible, popup_on_finish, show_rating, render_mode,
        )

        # ── Collect data from all upstream inputs ──
        inputs = node.get('inputs', [])
        collected_parts = []
        seen_from_nodes = set()

        logger.info(
            "[OUTPUT] Node %s: collecting data from %d input connection(s)",
            node_id, len(inputs),
        )

        for inp in inputs:
            from_node_id = inp.get('from_node')
            if not from_node_id:
                logger.debug("[OUTPUT] Input connection missing 'from_node': %s", inp)
                continue

            input_port = str(inp.get('input_port', ''))
            output_type = str(inp.get('output_type') or inp.get('output_port') or 'output')

            # Base/branch source ports drive execution only — no data.
            if str(output_type or '') in ('output', '', 'true', 'false', 'error', 'route'):
                logger.debug(
                    "[OUTPUT] Skipping exec-only source port '%s' from %s",
                    output_type, from_node_id,
                )
                continue

            if from_node_id in seen_from_nodes:
                logger.debug(
                    "[OUTPUT] Skipping duplicate source %s (already seen)", from_node_id,
                )
                continue
            seen_from_nodes.add(from_node_id)

            # Retrieve the NAMED upstream data — port store first, then legacy.
            val = self.port_store.get_output(self.chain_id, from_node_id, output_type)
            if val is None or (isinstance(val, str) and not val.strip()):
                if output_type == 'data':
                    val = self.llm_executor.get_variable(f"node_{from_node_id}_data")
                elif output_type in ('context', 'ctx_out'):
                    val = self.llm_executor.get_variable(f"node_{from_node_id}_context")
                else:
                    val = self.llm_executor.get_variable(
                        f"node_{from_node_id}_output_{output_type}"
                    )

            if val is None or (isinstance(val, str) and not val.strip()):
                logger.info(
                    "[OUTPUT] Upstream node %s: no output or context data available — skipping",
                    from_node_id,
                )
                continue

            # Determine upstream type for labelling
            source_type = 'unknown'
            upstream_node = {}
            if hasattr(self, 'workflow_graph') and self.workflow_graph:
                upstream_node = self.workflow_graph.get(from_node_id, {}) or {}
                source_type = upstream_node.get('type', 'unknown')

            upstream_data = upstream_node.get('data', {}) or {}
            source_label = (upstream_data.get('label', '')
                           or upstream_data.get('description', '')
                           or source_type)

            # Format the contribution
            if render_mode == 'image':
                val_str = str(val)  # raw — asset transport handles the payload
            else:
                val_str = str(val)
                cap = 2000 if render_mode == 'text' else 20000
                if len(val_str) > cap:
                    val_str = val_str[:cap] + "..."

            collected_parts.append({
                "source_node": from_node_id,
                "source_type": source_type,
                "source_label": source_label,
                "content": val_str,
                "asset": self._normalize_image_source(val) if render_mode == 'image' else None,
            })

            logger.info(
                "[OUTPUT] Collected from node %s (type=%s, label=%s, port=%s, output_type=%s): "
                "%d chars, content preview: %.200s",
                from_node_id, source_type, source_label,
                input_port, output_type,
                len(val_str), val_str[:200],
            )

        # ── Build final output ──
        if collected_parts:
            # If only one source, use its content directly for cleaner output
            if len(collected_parts) == 1:
                output_value = collected_parts[0]["content"]
                logger.info(
                    "[OUTPUT] Single-source output: using content directly (%d chars from %s)",
                    len(output_value), collected_parts[0]["source_node"],
                )
            else:
                output_value = json.dumps(collected_parts, indent=2, ensure_ascii=False)
                logger.info(
                    "[OUTPUT] Multi-source output: serialized %d sources to JSON (%d chars total)",
                    len(collected_parts), len(output_value),
                )
        else:
            output_value = ""
            logger.info("[OUTPUT] No data collected for output node %s", node_id)

        if output_value and str(output_value).strip():
            log_block(
                logger, logging.INFO,
                f"Output Node Value ({len(str(output_value))} chars)", str(output_value),
            )

        # ── Image asset (explicit image_source wins over collected) ──
        asset = None
        if render_mode == 'image':
            if image_source:
                asset = self._normalize_image_source(image_source)
            else:
                asset = next(
                    (p['asset'] for p in collected_parts if p.get('asset')),
                    None,
                )

        # ── TTS speech (static tts_text fallback, else collected content) ──
        spoken_text = ""
        if tts_enabled or render_mode == 'audio':
            spoken_text = (tts_text or str(output_value)).strip()
            if spoken_text:
                try:
                    from ....tts_service import submit as _tts_submit
                except ImportError:
                    try:
                        from LoOper.player.tts_service import submit as _tts_submit
                    except ImportError:
                        _tts_submit = None
                if _tts_submit:
                    try:
                        logger.info(
                            "[OUTPUT] Speaking %d chars (wait=%s) via tts_service",
                            len(spoken_text), tts_wait,
                        )
                        _tts_submit(
                            spoken_text,
                            voice_model=tts_voice_model,
                            language=tts_language,
                            speed=tts_speed,
                            speaker_id=tts_speaker_id,
                            wait=tts_wait,
                        )
                    except Exception as _e:
                        logger.warning("[OUTPUT] TTS submission failed: %s", _e)
            else:
                # TTS requested but nothing collected and no tts_text default:
                # report it and let the chain continue.
                logger.warning(
                    "[OUTPUT] TTS enabled for node %s but nothing to speak "
                    "(no upstream data/context collected and no tts_text default)",
                    node_id,
                )

        # ── Store for downstream consumption (port_store + legacy) ──
        self.port_store.set_output(self.chain_id, node_id, 'output', output_value)
        self.llm_executor.set_variable(f"node_{node_id}_output", output_value)
        logger.info(
            "[OUTPUT] Stored node_%s_output (%d chars) for downstream consumption",
            node_id, len(output_value),
        )
        logger.info(
            "[OUTPUT] Output value preview (first 500 chars): %.500s",
            output_value,
        )

        # Store metadata so readers know the output config
        output_meta = {
            "label": label,
            "agent_visible": agent_visible,
            "overlay_visible": overlay_visible,
            "popup_on_finish": popup_on_finish,
            "show_rating": show_rating,
            "source_count": len(collected_parts),
            "render_mode": render_mode,
            "tts_enabled": tts_enabled or render_mode == 'audio',
            "static_text": str(tts_text or '').strip(),
            "spoken_text": spoken_text,
            "asset": asset,
        }
        self.port_store.set_output(self.chain_id, node_id, 'output_meta', output_meta)
        self.llm_executor.set_variable(f"node_{node_id}_output_meta", output_meta)
        logger.info(
            "[OUTPUT] Output node %s metadata stored: %s",
            node_id, output_meta,
        )

        logger.info(
            "[OUTPUT] Output node %s done — %d source(s), %d chars collected",
            node_id, len(collected_parts), len(output_value),
        )

        # ── Fire mid-execution popup callback ──
        # In agent mode this enables per-output rating in the overlay.
        # The response is also returned via _build_context_response, so we
        # set a flag to suppress duplicate display there.
        if popup_on_finish and output_value:
            logger.info(
                "[OUTPUT] popup_on_finish triggered for node %s: label=%s, output_value=%d chars, agent_visible=%s, show_rating=%s",
                node_id, label, len(output_value), agent_visible, show_rating,
            )
            try:
                # Agent mode is active when _ask_user_callback is set (chat overlay)
                _agent_active = False
                try:
                    _agent_active = self.llm_executor.get_variable("_ask_user_callback") is not None
                except Exception:
                    _agent_active = False
                logger.info(
                    "[OUTPUT] Agent mode detection for output node %s: _ask_user_callback=%s",
                    node_id, _agent_active,
                )
                if _agent_active and not overlay_visible:
                    # Per-node toggle: keep this output silent in the agent
                    # chat overlay — it must not post mid-execution NOR
                    # resurface as the final reply (chain_executor skips it
                    # via output_meta).  The linear activity memory still
                    # records this node's output (run_memory core hook).
                    logger.info(
                        "[OUTPUT] Node %s silent in agent overlay "
                        "(overlay_visible=False) — not posting; memory still "
                        "records it", node_id,
                    )
                    return self._get_output_next_node(node, 'output')
                cb = getattr(self, '_on_output_ready', None)
                if cb:
                    logger.info("[OUTPUT] Calling on_output_ready callback for node %s", node_id)
                    if _agent_active:
                        # Mark that mid-execution output was already shown in the overlay
                        self.llm_executor.set_variable('_agent_mid_execution_outputs_shown', True)
                        # Attribute this output to the tool chain that just ran so
                        # the overlay can show its per-output rating widget
                        # (Per-Output Agent Rating).  chain_ops records every
                        # executed tool in chain_tool_results = {tool_id: {chain_path,
                        # chain_name, ...}} — the LAST entry is the tool that ran
                        # immediately before this output node.
                        _tool_path = ""
                        _tool_alias = ""
                        try:
                            _tool_results = (
                                self.llm_executor.get_variable("chain_tool_results")
                                or {}
                            )
                            if isinstance(_tool_results, dict) and _tool_results:
                                _last_record = _tool_results[
                                    list(_tool_results.keys())[-1]
                                ]
                                if isinstance(_last_record, dict):
                                    _tool_path = str(
                                        _last_record.get("chain_path") or ""
                                    )
                                    _tool_alias = str(
                                        _last_record.get("chain_name") or ""
                                    )
                                    if not _tool_alias and _tool_path:
                                        _tool_alias = os.path.basename(_tool_path)
                                        if _tool_alias.lower().endswith(".json"):
                                            _tool_alias = os.path.splitext(
                                                _tool_alias
                                            )[0]
                        except Exception:
                            _tool_path = ""
                            _tool_alias = ""
                        logger.info(
                            "[OUTPUT] Agent mode: marking _agent_mid_execution_outputs_shown=true, "
                            "calling callback with show_rating=%s, tool_path=%s, tool_alias=%s",
                            show_rating, _tool_path, _tool_alias,
                        )
                        cb(label or 'Chain Output', output_value,
                           tool_chain_path=_tool_path, tool_alias=_tool_alias,
                           show_rating=show_rating, mode=render_mode, asset=asset)
                    else:
                        logger.info("[OUTPUT] Manual mode: calling callback without rating")
                        cb(label or 'Chain Output', output_value,
                           mode=render_mode, asset=asset)
                    logger.info("[OUTPUT] on_output_ready callback completed for node %s", node_id)
                else:
                    logger.info(
                        "[OUTPUT] No on_output_ready callback registered for node %s "
                        "— output will be consumed via chain result",
                        node_id,
                    )
            except Exception as _ocb_err:
                logger.warning('[OUTPUT] on_output_ready callback failed: %s', _ocb_err)

        return self._get_output_next_node(node, 'output')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize_image_source(self, val):
        """Normalize an upstream image source into a displayable asset.

        ``data:image/...`` URIs pass through; existing image file paths are
        read to base64 data URIs (4 MB cap, otherwise the path is kept and
        the UI shows a placeholder); PIL images are encoded directly.
        """
        if val is None:
            return None
        if isinstance(val, str):
            if val.startswith("data:image/"):
                return val
            if os.path.isfile(val):
                if os.path.getsize(val) <= 4 * 1024 * 1024:
                    try:
                        from ....image_utils import read_image_to_base64
                        uri = read_image_to_base64(val)
                        return uri or val
                    except Exception:
                        pass
                return val  # oversized: keep path, UI shows placeholder
            return None
        try:
            from PIL import Image
            if isinstance(val, Image.Image):
                from ....image_utils import pil_to_base64
                return pil_to_base64(val)
        except Exception:
            pass
        return None

    def _get_output_next_node(self, node, port):
        connections = node.get('connections', {})
        if port in connections and connections[port]:
            return connections[port][0].get('node_id')
        if port == 'output':
            return "__done__"
        return None

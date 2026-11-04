"""
simple_agent.py — the agent mode dispatcher.

A **workspace** is one agent plus the chains it may use
(``player/workspace.py``).  That replaced the old "system chain" mechanism:
you no longer design a workflow AND an orchestrator chain.  The agent reads the
workspace catalog (each chain's own ``flow`` metadata), routes the request,
runs the chains in order and learns the plan (``agent.py``).

A workspace whose ``workspace.json`` pins a ``legacy_brain`` still answers with
that one chain, exactly like the old System chain — that is the bridge for
brains already designed by hand.

With neither a workspace nor a brain the agent returns a clear error: no
fallback, no hardcoded intent classification.
"""

import logging
import os
import uuid
from typing import Any, Dict, List

from player import workspace as ws

from .system_chains import read_chains

# Records propagate to the unified telemetry dispatch (see logging_setup.py):
# colorized console + logs/automation.log + per-session md log.
logger = logging.getLogger(__name__)


# ── Debug diagnostics ────────────────────────────────────────
_DEBUG = False


def _dbg(msg: str) -> None:
    if _DEBUG:
        import threading as _thr
        print(f"[D][{_thr.current_thread().name}] {msg}", flush=True)


# ── Router class ────────────────────────────────────────────────


class SimpleChainRouter:
    """Agent dispatcher that routes every query through a user-defined system chain.

    The system chain is the agent's brain — it receives the user query,
    reasons through its LLM nodes, executes tools via chain imports, and
    stores its reasoning in context nodes.  The agent's response *is* the
    system chain's output.

    Without a system chain, the agent cannot respond.
    """

    def __init__(self, model_path: str = "", chains_dir: str = "",
                 workspaces_root: str = "", workspace_slug: str = ""):
        self._chains_dir = chains_dir
        self._chains: List[Dict[str, Any]] = []
        self._ask_user_callback = None  # Callable for asking user input mid-execution
        self._ask_user_v2_callback = None  # Rich ask (choices/attachments), optional
        self._output_display_callback = None  # Callable for displaying output node results mid-execution
        self._memory_only = False  # memory-chat mode: answer from recorded activity only
        # The workspace = the agent + the chains it routes over.  Its
        # ``workspace.json`` path is what the sidebar compares, so an agent
        # with a pinned brain still has a stable identity here.
        try:
            self._workspaces_root = (workspaces_root or
                                     ws.root_for_chains(chains_dir))
        except Exception:
            self._workspaces_root = ""
        self._workspace_slug = ""
        self._workspace_file = ""
        self._brain_path = ""
        self._system_chain_path = ""  # the agent's identity (workspace.json)
        self._system_chain_id = ""    # its slug
        if workspace_slug:
            self.set_agent(workspace_slug)
        else:
            try:
                slug = ws.active_slug(self._workspaces_root)
                if slug:
                    self.set_agent(slug)
            except Exception:
                logger.debug("SimpleChainRouter: no active workspace yet")
        self._refresh_chains()

    def _refresh_chains(self) -> None:
        """Scan the chains directory for available chain JSON files."""
        self._chains = read_chains(self._chains_dir)

    # ------------------------------------------------------------- agents

    def list_agents(self) -> List[Dict[str, Any]]:
        """Every workspace as a selectable agent (one agent per workspace).

        ``path`` is the workspace's own ``workspace.json``: it exists whether
        the agent composes chains or pins one brain, so the sidebar has a
        stable identity to compare.
        """
        if not self._workspaces_root:
            return []
        out = []
        try:
            manifests = ws.list_workspaces(self._workspaces_root)
        except Exception:
            logger.warning("SimpleChainRouter: workspaces unreadable",
                           exc_info=True)
            return []
        for manifest in manifests:
            agent = manifest.get("agent") or {}
            brain = str(agent.get("legacy_brain") or "")
            chains = len(ws.chain_files(self._workspaces_root,
                                       manifest["slug"]))
            if brain:
                description = "fixed brain: %s" % os.path.basename(brain)
            else:
                description = "routes over %d chain(s)" % chains
            out.append({
                "id": manifest["slug"],
                "slug": manifest["slug"],
                "name": "%s · %s" % (manifest.get("name"),
                                      agent.get("name")
                                      or manifest.get("name")),
                "description": description,
                "path": os.path.join(
                    ws.workspace_dir(self._workspaces_root, manifest["slug"]),
                    ws.WORKSPACE_FILE),
                "brain": brain,
                "chains": chains,
                # The model the WORKSPACE chose ('' until the user picks one in
                # agent mode): no implicit default, so the sidebar shows the
                # real choice instead of a model nobody selected.
                "model": str(agent.get("model") or "").strip(),
                "model_own": bool(str(agent.get("model") or "").strip()),
            })
        return out

    def list_system_chains(self) -> List[Dict[str, Any]]:
        """Compatibility alias: the sidebar used to list System chains."""
        return self.list_agents()

    def active_agent(self) -> str:
        """The workspace slug the agent mode is bound to ('' when none)."""
        return self._workspace_slug

    def set_agent(self, slug: str) -> bool:
        """Bind agent mode to one workspace (its agent answers from now on)."""
        if not slug or not self._workspaces_root:
            return False
        try:
            manifest = ws.load(self._workspaces_root, slug)
        except ws.WorkspaceError as exc:
            logger.warning("SimpleChainRouter: agent %r not usable: %s", slug,
                           exc)
            return False
        self._workspace_slug = slug
        self._workspace_file = os.path.join(
            ws.workspace_dir(self._workspaces_root, slug), ws.WORKSPACE_FILE)
        self._brain_path = str((manifest.get("agent") or {})
                               .get("legacy_brain") or "")
        self._system_chain_path = self._workspace_file
        self._system_chain_id = slug
        self._chains_dir = ws.chains_dir(self._workspaces_root, slug)
        logger.info("SimpleChainRouter: agent '%s' (%s), %s",
                    (manifest.get("agent") or {}).get("name"), slug,
                    "brain=%s" % os.path.basename(self._brain_path)
                    if self._brain_path else "composed")
        # Binding to a workspace is a reload of agent mode: a chain edited since
        # it was described gets its per-node lines re-derived now, so routing
        # never reads a sentence older than the chain.
        self.refresh_descriptions()
        return True

    def refresh_descriptions(self) -> Dict[str, int]:
        """Bring every chain's description up to date with its own file.

        No model, no canvas, no user request: ``player/chain_refresh`` rewrites
        only the chains whose ``flow.stamp`` no longer matches their nodes
        (an edit since the last describe, or a file written before stamps
        existed).  Called on every agent-mode reload — a fresh window, entering
        agent mode, switching the agent, the reload button.
        """
        empty = {"checked": 0, "updated": 0, "current": 0, "failed": 0,
                 "skipped": 0}
        if not self._workspaces_root:
            return empty
        from player import chain_refresh
        try:
            if self._workspace_slug:
                return chain_refresh.refresh_workspace(self._workspaces_root,
                                                       self._workspace_slug)
            return chain_refresh.refresh_all(self._workspaces_root)
        except Exception:
            logger.warning("SimpleChainRouter: chain descriptions not "
                           "refreshed", exc_info=True)
            return empty

    def set_system_chain(self, chain_path: str) -> bool:
        """Select an agent by its workspace file, or pin a brain chain.

        Kept because the sidebar is written against it: a ``workspace.json``
        path selects that agent; a chain file path still works as the old
        "this chain is the brain" pin.
        """
        path = str(chain_path or "")
        if path and os.path.basename(os.path.normpath(path)) == ws.WORKSPACE_FILE:
            slug = os.path.basename(os.path.dirname(os.path.normpath(path)))
            return self.set_agent(slug)
        if not path or not os.path.exists(path):
            logger.warning("SimpleChainRouter: chain not found at %s", path)
            self._workspace_slug = ""
            self._brain_path = ""
            self._system_chain_path = ""
            self._system_chain_id = ""
            return False
        self._workspace_slug = ""
        self._brain_path = os.path.normpath(path)
        self._system_chain_path = self._brain_path
        self._system_chain_id = os.path.basename(path).replace(".json", "")
        logger.info("SimpleChainRouter: brain chain pinned to '%s'",
                    self._system_chain_id)
        return True

    def handle_request(self, query: str, stop_flag=None) -> str:
        """Route a request through the active agent.

        An agent with a pinned ``legacy_brain`` answers with that chain exactly
        like the old System chain; otherwise it reads its workspace catalog,
        routes, runs the chains it picked and learns the plan (agent.py).
        Without a workspace or a brain the request is refused with a reason.
        """
        # Memory-chat mode: answer strictly from the recorded activity
        # timeline (player/agentic_ops/run_memory.py) — no chain execution.
        if self._memory_only:
            if stop_flag and stop_flag():
                return "Cancelled."
            try:
                from player.agentic_ops import run_memory
                return run_memory.answer_question(query, stop_flag=stop_flag)
            except Exception as e:
                logger.exception("SimpleChainRouter: memory chat failed")
                return f"Memory chat error: {e}"

        self._refresh_chains()

        if stop_flag and stop_flag():
            return "Cancelled."

        if self._workspace_slug:
            return self._run_workspace(query, stop_flag)
        if self._brain_path:
            return self._run_system_chain(query, stop_flag)

        # Nothing selected yet: the first workspace is the default agent.
        agents = self.list_agents()
        if agents and self.set_agent(agents[0]["slug"]):
            return self._run_workspace(query, stop_flag)

        return (
            "No agent available. Create a workspace on the canvas "
            "(its own chains folder) or pin a brain chain in the workspace's "
            "workspace.json."
        )

    def _run_workspace(self, query: str, stop_flag=None) -> str:
        """One request through the workspace agent (composed or pinned)."""
        if self._brain_path:
            return self._run_system_chain(query, stop_flag)
        from player.agentic_ops.agent import AgentError, WorkspaceAgent
        try:
            agent = WorkspaceAgent(self._workspaces_root, self._workspace_slug)
            result = agent.run(query, stop_flag=stop_flag,
                               ask_user=self._ask_user_callback,
                               ask_user_v2=self._ask_user_v2_callback,
                               on_output_ready=self._output_display_callback)
        except AgentError as exc:
            logger.warning("SimpleChainRouter: %s", exc)
            return f"Agent error: {exc}"
        except Exception as exc:
            logger.exception("SimpleChainRouter: the agent run failed")
            return f"Agent error: {exc}"
        try:
            from player.agentic_ops import run_memory
            run_memory.record_chat(query, result or "")
        except Exception:
            pass
        return result or ""

    def _run_system_chain(self, query: str, stop_flag=None) -> str:
        """Execute the pinned brain chain and return its output directly."""
        chain_path = self._brain_path or self._system_chain_path
        req_id = uuid.uuid4().hex[:8]
        logger.info(
            "[AGENT-SYS] [%s] ===== Brain chain execution =====", req_id
        )
        logger.info("[AGENT-SYS] [%s] Chain: '%s'", req_id, self._system_chain_id)
        logger.info("[AGENT-SYS] [%s] Query: %s", req_id, query[:200])

        if stop_flag and stop_flag():
            logger.info("[AGENT-SYS] [%s] Stop requested before execution", req_id)
            return "Cancelled."

        # Execute the system chain — the chain executor handles its own
        # infrastructure (llama.cpp server, API gateway, etc.)
        try:
            from player.agentic_ops.chain_executor import ChainExecutor
            executor = ChainExecutor()
            result = executor.run_chain(
                chain_path,
                agent_query=query,
                llamacpp_server_url=None,
                ask_user_callback=self._ask_user_callback,
                ask_user_v2_callback=self._ask_user_v2_callback,
                on_output_ready=self._output_display_callback,
                stop_flag=stop_flag,
            )
            logger.info(
                "[AGENT-SYS] [%s] Brain chain finished: %s",
                req_id, (result[:200] if result else "None"),
            )
        except Exception as e:
            logger.exception("[AGENT-SYS] [%s] Brain execution failed: %s",
                             req_id, e)
            try:
                from player.agentic_ops import run_memory
                run_memory.record_chat(query, f"error: {e}")
            except Exception:
                pass
            return f"Agent error: {e}"

        # Linear activity timeline: pair the ask with its answer (the chain
        # run recorded its own events; this is the chat-turn record).
        try:
            from player.agentic_ops import run_memory
            run_memory.record_chat(query, result or "")
        except Exception:
            pass

        # Return the execution result directly — this contains context node
        # content from the brain chain's own LLM nodes, not a separate chat
        # response.  An empty result means the chain's outputs were already
        # shown mid-run (the output-node callback path); never fabricate a
        # success line here — the overlay already rendered what happened.
        return result or ""

    def set_ask_user_callback(self, callback):
        """Set a callable that asks the user for input in the chat and returns the response.

        Used by Input nodes with a ``user_prompt`` during agent-mode chain execution.
        The callable receives a question string and should return the user's typed response.
        """
        self._ask_user_callback = callback

    def set_ask_user_v2_callback(self, callback):
        """Set a rich ask-user callable (v2 protocol: dict request -> dict response).

        When present, Input nodes prefer it over the legacy text callback:
        yes/no and choice questions render as buttons on surfaces that support
        them, and text inputs may carry file attachments.
        """
        self._ask_user_v2_callback = callback

    def set_output_display_callback(self, callback):
        """Set a callable that displays output node results in the chat overlay.

        Unlike ask_user_callback, this does NOT block — it posts the output
        text and returns immediately so the chain continues executing.
        Receives (label, content) strings.
        """
        self._output_display_callback = callback

    def set_memory_only(self, enabled: bool) -> None:
        """Toggle memory-chat mode: requests answer from recorded activity only.

        With it on, ``handle_request`` never dispatches chains — it answers
        from the linear activity timeline (player/agentic_ops/run_memory.py),
        strictly from recorded events with their timestamps.
        """
        self._memory_only = bool(enabled)
        logger.info(
            "SimpleChainRouter: memory-only chat %s", "ON" if enabled else "OFF",
        )

    def shutdown(self) -> None:
        pass

    def __del__(self) -> None:
        self.shutdown()

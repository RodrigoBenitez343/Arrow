import os
import sys
import json
import uuid
import time
import threading
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SchedulerService:
    """
    Lightweight background scheduler for running LoOper chains on a time schedule.

    Supports schedule types: once, daily, weekly, interval.
    Persists schedules to a JSON file in the project root.

    Chain execution is performed IN-PROCESS using MultiSequencePlayer, matching
    the same execution path as manual playback and chain import nodes. This
    ensures both source and compiled (PyInstaller) builds work identically,
    and sandbox mode correctly injects the agent bridge into the RDP session
    from the host LoOper instance.
    """

    STORAGE_FILE = "schedules.json"

    def __init__(self, project_root: Optional[str] = None, check_interval_seconds: int = 10):
        self.project_root = project_root or os.getcwd()
        self.storage_path = os.path.join(self.project_root, self.STORAGE_FILE)
        self.check_interval_seconds = max(2, check_interval_seconds)
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._schedules: Dict[str, Dict] = {}
        self._running_schedules: set = set()  # Track currently executing schedules
        self._load()

    # ------------------------- Public API -------------------------
    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, name="SchedulerServiceThread", daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def list_schedules(self) -> List[Dict]:
        with self._lock:
            return [self._with_next_run_str(s.copy()) for s in self._schedules.values()]

    def get_schedule(self, schedule_id: str) -> Optional[Dict]:
        with self._lock:
            s = self._schedules.get(schedule_id)
            return self._with_next_run_str(s.copy()) if s else None

    def add_schedule(self, schedule: Dict) -> Dict:
        with self._lock:
            sid = str(uuid.uuid4())
            schedule = schedule.copy()
            schedule['id'] = sid
            schedule.setdefault('enabled', True)
            schedule.setdefault('last_run', None)
            schedule['next_run'] = self._compute_next_run(schedule)
            self._schedules[sid] = schedule
            self._save()
        return self._with_next_run_str(schedule.copy())

    def update_schedule(self, schedule: Dict) -> Dict:
        sid = schedule.get('id')
        if not sid:
            raise ValueError('Schedule missing id')
        with self._lock:
            if sid not in self._schedules:
                raise KeyError(f'Schedule {sid} not found')
            new_sched = self._schedules[sid].copy()
            new_sched.update(schedule)
            # Recompute next_run when relevant fields change or if missing
            new_sched['next_run'] = self._compute_next_run(new_sched)
            self._schedules[sid] = new_sched
            self._save()
        return self._with_next_run_str(new_sched.copy())

    def remove_schedule(self, schedule_id: str):
        with self._lock:
            if schedule_id in self._schedules:
                del self._schedules[schedule_id]
                self._save()

    def set_enabled(self, schedule_id: str, enabled: bool):
        with self._lock:
            if schedule_id not in self._schedules:
                raise KeyError(f'Schedule {schedule_id} not found')
            self._schedules[schedule_id]['enabled'] = enabled
            if enabled:
                self._schedules[schedule_id]['next_run'] = self._compute_next_run(self._schedules[schedule_id])
            self._save()

    def run_now(self, schedule_id: str):
        with self._lock:
            schedule = self._schedules.get(schedule_id)
            if not schedule:
                raise KeyError(f'Schedule {schedule_id} not found')
            # Launch chain asynchronously
            self._launch_chain(schedule)
            schedule['last_run'] = self._now_iso()
            # After manual run, advance next_run for recurring schedules
            schedule['next_run'] = self._compute_next_run(schedule, from_time=datetime.now())
            self._save()

    def run_chain_now(self, chain_path: str, ask_user_callback=None,
                      stop_flag=None, on_complete=None):
        """Execute *chain_path* immediately, in-process (manual / web playback).

        Reuses the schedule launcher so sandbox overrides, stop handling and
        background threading behave identically to a scheduled run.  The
        caller may override three things on the schedule dict:

        ``ask_user_callback`` — where an Input node's question goes.  Without
        it the node falls back to a desktop dialog, which is wrong for a run
        the user started from the phone.
        ``stop_flag`` — the caller's own abort flag (the phone's Stop button),
        instead of the global ESC monitor alone.
        ``on_complete`` — fired once when execution ends, success or failure,
        so the caller can clear its "running" state.
        """
        if not chain_path:
            raise ValueError('chain_path required')
        self._launch_chain({
            'chain_path': chain_path,
            'id': f'manual-{uuid.uuid4().hex[:8]}',
            'ask_user_callback': ask_user_callback,
            'stop_flag': stop_flag,
            'on_complete': on_complete,
        })

    # ----------------------- Internal Methods ----------------------
    def _loop(self):
        while True:
            with self._lock:
                running = self._running
            if not running:
                break

            now = datetime.now()
            due_schedule_ids: List[str] = []
            
            # Find due schedules while holding the lock
            with self._lock:
                for schedule_id, s in self._schedules.items():
                    if not s.get('enabled', True):
                        continue
                    next_run = self._parse_dt(s.get('next_run'))
                    if next_run is None:
                        continue
                    # Use a small tolerance window
                    if next_run <= now:
                        due_schedule_ids.append(schedule_id)

            # Execute due schedules
            for schedule_id in due_schedule_ids:
                try:
                    with self._lock:
                        # Re-check schedule still exists and is enabled
                        if schedule_id not in self._schedules:
                            continue
                        s = self._schedules[schedule_id]
                        if not s.get('enabled', True):
                            continue
                        
                        # Skip if already running
                        if schedule_id in self._running_schedules:
                            continue
                        
                        # Double-check it's still due (prevent duplicate runs)
                        next_run = self._parse_dt(s.get('next_run'))
                        if next_run is None or next_run > now:
                            continue
                        
                        # Mark as running
                        self._running_schedules.add(schedule_id)
                    
                    # Launch chain in-process (starts a background thread)
                    # The thread will remove schedule_id from _running_schedules when done
                    self._launch_chain(s)
                    
                    # Update schedule metadata after launch
                    with self._lock:
                        if schedule_id in self._schedules:
                            s = self._schedules[schedule_id]
                            s['last_run'] = self._now_iso()
                            # For one-time schedules, disable after run
                            if s.get('type') == 'once':
                                s['enabled'] = False
                                s['next_run'] = None
                            else:
                                s['next_run'] = self._compute_next_run(s, from_time=now)
                            self._save()
                        # NOTE: Do NOT discard from _running_schedules here.
                        # The background thread handles cleanup when chain execution finishes.
                except Exception:
                    # Do not crash scheduler loop on single failure
                    # Make sure to remove from running set even on error during launch
                    with self._lock:
                        self._running_schedules.discard(schedule_id)
                    pass

            time.sleep(self.check_interval_seconds)

    def _launch_chain(self, schedule: Dict):
        """
        Execute a scheduled chain IN-PROCESS using MultiSequencePlayer.

        This mirrors the execution path used by manual playback (main_window.py)
        and chain import nodes (chain_ops.py). Running in-process ensures:
        - Works identically in both source and compiled (PyInstaller) builds
        - Sandbox mode correctly injects the agent bridge into the RDP session
          from the host LoOper instance (no second GUI or subprocess needed)
        - The host orchestrates all actions, the RDP session only runs the
          lightweight HTTP agent bridge (sandbox_agent.py)
        """
        chain_path = schedule.get('chain_path')
        if not chain_path:
            raise ValueError('Schedule missing chain_path')

        # Robust path resolution: try multiple locations for the chain file.
        # In compiled mode the bundled schedules.json may contain absolute paths
        # from the SOURCE machine (e.g. D:\\LoOperV2\\LoOper\\Chains\\...).  We
        # also fall back to the exe directory so chains can be installed alongside
        # the compiled LoOper.
        _resolved_chain = None
        _searched = []

        def _try(path):
            _searched.append(path)
            if path and os.path.exists(path):
                return path
            return None

        if os.path.isabs(chain_path):
            _resolved_chain = _try(chain_path)
        else:
            # Relative path: try project_root first, then exe_dir (compiled)
            _resolved_chain = _try(os.path.join(self.project_root, chain_path))
            if getattr(sys, 'frozen', False):
                _exe_based = os.path.join(os.path.dirname(sys.executable), chain_path)
                if not _resolved_chain:
                    _resolved_chain = _try(_exe_based)
            if not _resolved_chain:
                _resolved_chain = _try(chain_path)  # raw relative to cwd

        if _resolved_chain:
            chain_path = _resolved_chain
        elif os.path.isabs(chain_path):
            raise FileNotFoundError(f'Chain file not found: {chain_path}')
        else:
            raise FileNotFoundError(
                f"Chain file not found: '{chain_path}'. "
                f"Searched: {_searched}"
            )

        # Get sandbox configuration from schedule.
        # Coerce to boolean to handle both JSON booleans and string 'true'/'false'.
        run_in_sandbox_raw = schedule.get('run_in_sandbox', False)
        if isinstance(run_in_sandbox_raw, str):
            run_in_sandbox = run_in_sandbox_raw.strip().lower() in ('true', '1', 'yes', 'on')
        else:
            run_in_sandbox = bool(run_in_sandbox_raw)
        show_sandbox_window = schedule.get('show_sandbox_window', True)
        schedule_id = schedule.get('id', 'unknown')

        def _execute():
            """Run the chain in a background thread, matching manual playback flow."""
            try:
                logger.info(f"[Scheduler] Starting in-process chain execution: {chain_path}")
                try:
                    from player.agentic_ops import run_memory
                    run_memory.record_schedule(schedule_id, chain_path, 'fired')
                except Exception:
                    pass

                # Load chain config
                with open(chain_path, 'r', encoding='utf-8') as f:
                    chain_config = json.load(f)

                # Import player modules (same as main_window.py execute_chain_config)
                from player.multi_sequence_player import play_chain_with_tailcalls
                from player.keyboard_monitor import start_global_monitoring, stop_global_monitoring, create_stop_flag

                # Clear JSON cache for fresh execution
                try:
                    from player.json_cache import clear_cache
                    clear_cache()
                except Exception:
                    pass

                # Apply sandbox context to every constructed player (including
                # self-callback handoff rebuilds)
                def _apply_sandbox(player):
                    if not run_in_sandbox:
                        return
                    sandbox_ctx = {
                        "sandboxed": True,
                        "show_window": show_sandbox_window,
                    }
                    player.global_app_context_override = sandbox_ctx
                    # Propagate to sub-executors (same as chain_ops.py does for chain imports)
                    if hasattr(player, 'workflow_executor') and player.workflow_executor:
                        player.workflow_executor.global_app_context_override = sandbox_ctx
                    if hasattr(player, 'sequence_executor') and player.sequence_executor:
                        player.sequence_executor.global_app_context_override = sandbox_ctx
                    logger.info(f"[Scheduler] Sandbox mode enabled for chain. Show window: {show_sandbox_window}")

                # Start ESC key monitoring (scheduled runs: the global flag
                # is the only abort source; a caller-run chain supplies its
                # own stop_flag and/or ask surface instead).
                start_global_monitoring()
                _ask_cb = schedule.get('ask_user_callback')
                stop_flag = schedule.get('stop_flag') or create_stop_flag()
                if _ask_cb is None:
                    # No caller surface: Input nodes fall back to the desktop
                    # prompt, which ask_text marshals to the GUI thread.
                    logger.info(
                        "[Scheduler] No ask_user_callback — Input nodes will "
                        "prompt on the desktop"
                    )

                try:
                    # Self-callback handoffs are followed in a flat loop
                    result, _player = play_chain_with_tailcalls(
                        chain_path, stop_flag=stop_flag,
                        ask_user_callback=_ask_cb,
                        player_setup_callback=_apply_sandbox,
                        run_source="schedule",
                    )
                    logger.info(f"[Scheduler] Chain execution completed with result: {result}")
                finally:
                    stop_global_monitoring()

            except Exception as e:
                logger.error(f"[Scheduler] Chain execution failed: {e}")
                import traceback
                logger.error(f"[Scheduler] Traceback: {traceback.format_exc()}")
            finally:
                # Always remove from running set when done
                with self._lock:
                    self._running_schedules.discard(schedule_id)
                try:
                    from player.agentic_ops import run_memory
                    run_memory.record_schedule(schedule_id, chain_path, 'completed')
                except Exception:
                    pass
                _done = schedule.get('on_complete')
                if _done is not None:
                    try:
                        _done()
                    except Exception:
                        logger.exception("[Scheduler] on_complete callback failed")

        # Launch in a background thread (non-blocking, same as manual playback)
        t = threading.Thread(target=_execute, name=f"SchedulerChain-{schedule_id}", daemon=True)
        t.start()

    def _compute_next_run(self, schedule: Dict, from_time: Optional[datetime] = None) -> Optional[str]:
        now = from_time or datetime.now()
        stype = schedule.get('type')
        if stype == 'once':
            dt = self._parse_dt(schedule.get('once_datetime'))
            if not dt:
                return None
            return dt.isoformat() if dt > now else None
        elif stype == 'daily':
            tstr = schedule.get('daily_time')  # 'HH:MM'
            if not tstr:
                return None
            hour, minute = self._parse_hhmm(tstr)
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate = candidate + timedelta(days=1)
            return candidate.isoformat()
        elif stype == 'weekly':
            tstr = schedule.get('weekly_time')  # 'HH:MM'
            days = schedule.get('days_of_week', [])  # [0..6], Monday=0
            if not tstr or not days:
                return None
            hour, minute = self._parse_hhmm(tstr)
            # Find next day in the set
            for i in range(8):
                candidate = (now + timedelta(days=i)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate <= now:
                    continue
                if candidate.weekday() in days:
                    return candidate.isoformat()
            return None
        elif stype == 'interval':
            minutes = int(schedule.get('interval_minutes') or 0)
            if minutes <= 0:
                return None
            
            delta = timedelta(minutes=minutes)
            last_run = self._parse_dt(schedule.get('last_run'))
            start_dt = self._parse_dt(schedule.get('start_datetime')) or now
            
            # If we have a last_run, next run is simply last_run + interval
            if last_run:
                next_run = last_run + delta
                # Ensure next_run is in the future
                while next_run <= now:
                    next_run += delta
                return next_run.isoformat()
            
            # For first run, use start_datetime if it's in the future, otherwise now + interval
            if start_dt > now:
                return start_dt.isoformat()
            else:
                # Start immediately if start_datetime is in the past
                return now.isoformat()
        else:
            return None

    def _with_next_run_str(self, sched: Dict) -> Dict:
        # presentable fields
        dt = self._parse_dt(sched.get('next_run'))
        sched['next_run_readable'] = dt.strftime('%Y-%m-%d %H:%M') if dt else '—'
        return sched

    def _load(self):
        # project_root fallback: if schedules.json loaded but points to a non-existent
        # location (e.g. bundled file from source machine), try the exe directory.
        if not os.path.exists(self.storage_path):
            _exe_based = os.path.join(os.path.dirname(sys.executable), self.STORAGE_FILE)
            if os.path.exists(_exe_based):
                self.storage_path = _exe_based
                self.project_root = os.path.dirname(_exe_based)
        if not os.path.exists(self.storage_path):
            self._schedules = {}
            return
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Normalize
            if isinstance(data, list):
                # migrate from list to dict
                self._schedules = {s.get('id', str(uuid.uuid4())): s for s in data}
            elif isinstance(data, dict):
                self._schedules = data
            else:
                self._schedules = {}
        except Exception:
            self._schedules = {}

    def _save(self):
        try:
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(self._schedules, f, indent=2)
        except Exception:
            pass

    @staticmethod
    def _parse_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    @staticmethod
    def _parse_hhmm(s: str) -> (int, int):
        parts = s.split(':')
        return int(parts[0]), int(parts[1])

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat()
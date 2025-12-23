import os
import sys
import json
import uuid
import time
import threading
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Optional


class SchedulerService:
    """
    Lightweight background scheduler for running LoOper chains on a time schedule.

    Supports schedule types: once, daily, weekly, interval.
    Persists schedules to a JSON file in the project root.
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
                    
                    # Launch chain outside of lock to prevent blocking
                    self._launch_chain(s)
                    
                    # Update schedule after successful launch
                    with self._lock:
                        if schedule_id in self._schedules:  # Re-check existence
                            s = self._schedules[schedule_id]
                            s['last_run'] = self._now_iso()
                            # For one-time schedules, disable after run
                            if s.get('type') == 'once':
                                s['enabled'] = False
                                s['next_run'] = None
                            else:
                                s['next_run'] = self._compute_next_run(s, from_time=now)
                            self._save()
                        
                        # Remove from running set
                        self._running_schedules.discard(schedule_id)
                except Exception:
                    # Do not crash scheduler loop on single failure
                    # Make sure to remove from running set even on error
                    with self._lock:
                        self._running_schedules.discard(schedule_id)
                    pass

            time.sleep(self.check_interval_seconds)

    def _launch_chain(self, schedule: Dict):
        chain_path = schedule.get('chain_path')
        if not chain_path:
            raise ValueError('Schedule missing chain_path')
        # Resolve to absolute path if relative
        if not os.path.isabs(chain_path):
            chain_path = os.path.join(self.project_root, chain_path)
        if not os.path.exists(chain_path):
            raise FileNotFoundError(f'Chain file not found: {chain_path}')

        # Launch player as a subprocess (non-blocking)
        python_exe = sys.executable or 'python'
        cmd = [python_exe, '-m', 'player.main', chain_path]
        
        # On Linux, use xvfb-run to provide virtual display for GUI automation
        import platform
        import shutil
        if platform.system() == "Linux" and shutil.which("xvfb-run"):
            cmd = ["xvfb-run", "-a"] + cmd
        
        # Use CREATE_NO_WINDOW on Windows to prevent console popup
        creationflags = 0
        if sys.platform == "win32":
            creationflags = 0x08000000
            
        subprocess.Popen(cmd, cwd=self.project_root, creationflags=creationflags)

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
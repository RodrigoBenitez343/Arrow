import os
import json
import time
import subprocess
import requests

def _base_url():
    host = os.getenv("OLLAMA_HOST", "localhost")
    port = os.getenv("OLLAMA_PORT", "11434")
    return f"http://{host}:{port}"

def _list_http():
    try:
        r = requests.get(f"{_base_url()}/api/ps", headers={"Connection": "close"}, timeout=4)
        if r.status_code != 200:
            return []
        data = r.json()
        items = data.get("models") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        out = []
        for m in items if isinstance(items, list) else []:
            name = (m.get("model") or m.get("name")) if isinstance(m, dict) else None
            mid = m.get("id") if isinstance(m, dict) else None
            if name or mid:
                out.append({"name": name, "id": mid})
        return out
    except Exception:
        return []

def _stop_http(name=None, mid=None):
    try:
        payload = {}
        if name:
            payload["model"] = name
        if mid:
            payload["id"] = mid
        if not payload:
            return False
        requests.post(f"{_base_url()}/api/stop", json=payload, headers={"Connection": "close"}, timeout=3)
        return True
    except Exception:
        return False

def _list_cli():
    try:
        env = os.environ.copy()
        p = subprocess.run(["ollama", "ps", "--json"], capture_output=True, text=True, timeout=4, env=env)
        if p.returncode != 0:
            return []
        s = (p.stdout or "").strip()
        if not s:
            return []
        data = json.loads(s)
        items = data.get("models") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        res = []
        if isinstance(items, list):
            for m in items:
                name = (m.get("model") or m.get("name")) if isinstance(m, dict) else None
                if name:
                    res.append({"name": name})
        return res
    except Exception:
        return []

def _stop_cli(name):
    try:
        if not name:
            return False
        env = os.environ.copy()
        subprocess.run(["ollama", "stop", name], capture_output=True, text=True, timeout=4, env=env)
        return True
    except Exception:
        return False

def _list_tags():
    try:
        r = requests.get(f"{_base_url()}/api/tags", headers={"Connection": "close"}, timeout=4)
        if r.status_code != 200:
            return []
        data = r.json()
        models = data.get("models") if isinstance(data, dict) else []
        names = []
        for m in models if isinstance(models, list) else []:
            n = m.get("name") if isinstance(m, dict) else None
            if n:
                names.append(n)
        return names
    except Exception:
        return []

def _candidate_names():
    names = set()
    for x in _list_http():
        if x.get("name"):
            names.add(x.get("name"))
    for x in _list_cli():
        if x.get("name"):
            names.add(x.get("name"))
    for n in _list_tags():
        names.add(n)
    return list(names)

def cleanup_all(max_passes=3, wait_s=0.5):
    ok = False
    for _ in range(max_passes):
        runners = _list_http()
        for r in runners:
            _stop_http(r.get("name"), r.get("id"))
        for r in _list_cli():
            _stop_cli(r.get("name"))
        for name in _candidate_names():
            _stop_http(name, None)
            _stop_cli(name)
        time.sleep(wait_s)
        if not _list_http() and not _list_cli():
            ok = True
            break
    return ok

def cleanup_model(name, max_passes=2, wait_s=0.3):
    ok = False
    for _ in range(max_passes):
        _stop_http(name, None)
        _stop_cli(name)
        time.sleep(wait_s)
        names = [x.get("name") for x in _list_http()] + [x.get("name") for x in _list_cli()]
        if name not in names:
            ok = True
            break
    return ok

def main():
    import argparse
    pa = argparse.ArgumentParser()
    pa.add_argument("--model", default=None)
    pa.add_argument("--all", action="store_true")
    pa.add_argument("--retries", type=int, default=3)
    pa.add_argument("--wait", type=float, default=0.5)
    pa.add_argument("--verbose", action="store_true")
    args = pa.parse_args()
    if args.all or not args.model:
        ok = cleanup_all(max_passes=args.retries, wait_s=args.wait)
        out = {"ok": ok}
        if args.verbose:
            out["http"] = _list_http()
            out["cli"] = _list_cli()
        print(json.dumps(out))
    else:
        ok = cleanup_model(args.model, max_passes=args.retries, wait_s=args.wait)
        out = {"ok": ok, "model": args.model}
        if args.verbose:
            out["http"] = _list_http()
            out["cli"] = _list_cli()
        print(json.dumps(out))

if __name__ == "__main__":
    main()

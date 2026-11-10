"""How big is the KV cache, and what does a window cost to start?

    .venv\\Scripts\\python.exe benchmarks\\bench_kv_scale.py
    .venv\\Scripts\\python.exe benchmarks\\bench_kv_scale.py --ctx 2048 4096 8192
    .venv\\Scripts\\python.exe benchmarks\\bench_kv_scale.py --live

Two halves:

  compute  KV bytes from the GGUF's own geometry (``AI.gguf_model_info``) for
           every window and cache type.  Linear in the window, halved at
           ``q8_0`` - the numbers ``--cache-type-k/-v`` are bought with.
  live     start the real llama-server once per window and report startup ms
           and the resident RSS of the process, then stop it.  Slow, and it
           takes the memory pool: the generation start releases Laya and the
           embedding server on purpose (ONE model per pool), so run this on an
           idle machine.

Prerequisites: a GGUF model (``--model`` or LLAMA_CPP.model_path), plus the
llama-server binary for ``--live``.
"""

import argparse
import glob
import os
import sys
import time

import bench_common as bc

bc.bootstrap()

from AI import engine_metrics as metrics                # noqa: E402
from AI import gguf_model_info as gmi                    # noqa: E402


def _default_model() -> str:
    try:
        from AI.config_loader import get_llamacpp_config
        path = str((get_llamacpp_config() or {}).get("model_path") or "")
        if path and os.path.exists(path):
            return path
    except Exception:
        pass
    models_dir = os.path.join(str(bc.LOOPER), "AI", "models")
    for pattern in ("*.gguf", os.path.join("*", "*.gguf")):
        found = sorted(glob.glob(os.path.join(models_dir, pattern)))
        for candidate in found:
            if os.path.isfile(candidate):
                return candidate
    return ""


def _mb(value: int) -> float:
    return round(float(value) / (1024 * 1024), 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="", help="GGUF to measure")
    parser.add_argument("--ctx", type=int, nargs="+",
                        default=[2048, 4096, 8192, 16384])
    parser.add_argument("--live", action="store_true",
                        help="also start a real server per window (slow)")
    args = parser.parse_args()

    model = args.model or _default_model()
    bc.require(bool(model) and os.path.exists(model),
               "no GGUF found - pass --model <path.gguf>")

    params = gmi.read_model_kv_params(model)
    bc.require(params is not None,
               "cannot read the KV geometry of %s (odd or truncated GGUF)"
               % os.path.basename(model))

    rows = []
    for n_ctx in args.ctx:
        rows.append([n_ctx,
                     _mb(metrics.kv_bytes(model, n_ctx, "f16")),
                     _mb(metrics.kv_bytes(model, n_ctx, "q8_0"))])
    print("model: %s" % os.path.basename(model))
    print("kv rows: %d layer(s), %d kv head(s), head_dim %d"
          % (int(getattr(params, "n_layer", 0) or 0),
             int(getattr(params, "n_kv_heads", 0) or 0),
             int(getattr(params, "head_dim", 0) or 0)))
    print()
    bc.table(("ctx", "KV f16 MB", "KV q8_0 MB"), rows)
    print("\nq8_0 halves the cache: that is the RAM the default KV type buys.")

    if not args.live:
        return 0

    from AI.llama_cpp_engine import LlamaCppEngine
    probe = LlamaCppEngine({"model_path": model})
    exe = probe._find_server_executable()
    bc.require(bool(exe), "llama-server binary not found - skip --live")

    print("\nlive starts (each one owns the memory pool):")
    live_rows = []
    for n_ctx in args.ctx:
        engine = LlamaCppEngine({"model_path": model, "server_port": 0,
                                 "gpu_layers": 0, "context_size": n_ctx})
        engine.server_port = _free_port(engine.server_port)
        engine.server_url = "http://127.0.0.1:%d" % engine.server_port
        rss_before = metrics.rss_mb("llama-server")
        started = time.perf_counter()
        ok = engine.start_server(blocking=True)
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        rss = metrics.rss_mb("llama-server")
        try:
            engine.stop_server()
        except Exception:
            pass
        live_rows.append([n_ctx, "ok" if ok else "FAILED", elapsed,
                          round(max(0.0, rss - rss_before), 1), rss])
        print("ctx %6d: %s in %8.1f ms, llama-server RSS %.1f MB (+%.1f)"
              % (n_ctx, "started" if ok else "FAILED", elapsed, rss,
                 max(0.0, rss - rss_before)))
    print()
    bc.table(("ctx", "start", "startup ms", "RSS delta MB", "RSS MB"),
             live_rows)
    return 0


def _free_port(preferred: int) -> int:
    """A port that binds (the engine re-scans internally as well)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", int(preferred or 0)))
            return sock.getsockname()[1]
    except OSError:
        return 0


if __name__ == "__main__":
    sys.exit(main())

"""embedding_server: the load -> use -> unload burst policy.

The embedding server is a sibling burst resource (one llama.cpp model per
memory pool).  These tests pin the burst plumbing -- release, idle unload,
seq guard, keep-alive -- without spawning the engine.

Run:  python -m pytest LoOper/tests/test_embedding_server_burst.py
"""

import AI.embedding_server as es


class _FakeEngine:
    """Minimal LlamaCppEngine stand-in: records stop + returns vectors."""

    def __init__(self):
        self.stopped = False

    def stop_server(self, quick=False):
        self.stopped = True

    def embed(self, texts):
        return [[0.0] for _ in texts]


def _fresh(monkeypatch):
    monkeypatch.setattr(es, "_emb_engine", None)
    monkeypatch.setattr(es, "_EMB_INFLIGHT", 0)
    monkeypatch.setattr(es, "_EMB_SEQ", 0)
    monkeypatch.setattr(es, "_EMB_KEEP_ALIVE_SECONDS", 0.0)


def test_release_stops_and_clears(monkeypatch):
    _fresh(monkeypatch)
    eng = _FakeEngine()
    monkeypatch.setattr(es, "_emb_engine", eng)

    assert es.release(wait=0.0) is True
    assert eng.stopped is True
    assert es._emb_engine is None
    assert es.release(wait=0.0) is False  # idempotent


def test_release_defers_while_embed_in_flight(monkeypatch):
    _fresh(monkeypatch)
    eng = _FakeEngine()
    monkeypatch.setattr(es, "_emb_engine", eng)
    monkeypatch.setattr(es, "_EMB_INFLIGHT", 1)

    assert es.release(wait=0.0) is False
    assert eng.stopped is False
    assert es._emb_engine is eng  # never killed out from under its caller


def test_negative_keep_alive_pins_the_model_resident(monkeypatch):
    _fresh(monkeypatch)
    spawned = []

    class _NoThread:
        def __init__(self, *a, **k):
            spawned.append(k.get("name"))

        def start(self):
            pass

    monkeypatch.setattr(es.threading, "Thread", _NoThread)

    monkeypatch.setattr(es, "_EMB_KEEP_ALIVE_SECONDS", -1.0)
    es._schedule_unload(1)
    assert spawned == [], "a negative grace opts out of the whole policy"

    monkeypatch.setattr(es, "_EMB_KEEP_ALIVE_SECONDS", 2.0)
    es._schedule_unload(1)
    assert spawned == ["embed-idle-unloader"]


def test_idle_unload_is_seq_guarded(monkeypatch):
    _fresh(monkeypatch)
    eng = _FakeEngine()
    monkeypatch.setattr(es, "_emb_engine", eng)
    monkeypatch.setattr(es, "_EMB_SEQ", 5)

    es._unload_after_idle(4)  # a newer embed claimed the server -> keep it
    assert eng.stopped is False

    es._unload_after_idle(5)  # burst done -> unload
    assert eng.stopped is True


def test_cold_start_can_release_itself(monkeypatch):
    """The cold start must not deadlock on its own lock.

    ``get_embed_client`` holds ``_EMBED_LOCK`` while it runs ``start_server``,
    which releases every sibling burst resource before sizing its model --
    including THIS one. With a plain ``Lock`` the same thread blocked forever and
    ComoRAG froze mid-probe with no log line at all after it (observed: 27
    minutes of silence, no ``Enabled --embeddings`` entry).
    """
    import threading

    import AI.llama_cpp_engine as lce

    _fresh(monkeypatch)
    monkeypatch.setattr(es, "_resolve_embedding_model_path", lambda: __file__)

    class _SelfReleasingEngine:
        server_url = "http://127.0.0.1:1"

        def start_server(self, blocking=False):
            es.release(wait=0.0)      # what llama_cpp_engine does on every start
            return True

        def stop_server(self, quick=False):
            pass

    monkeypatch.setattr(lce, "LlamaCppEngine", lambda cfg: _SelfReleasingEngine())

    done = []
    thread = threading.Thread(
        target=lambda: done.append(es.get_embed_client()), daemon=True)
    thread.start()
    thread.join(10.0)
    assert done, "the embedding cold start self-deadlocked"
    assert done[0] is not None


def test_embed_arms_the_idle_unloader(monkeypatch):
    _fresh(monkeypatch)
    armed = []
    monkeypatch.setattr(es, "_schedule_unload", lambda seq: armed.append(seq))
    monkeypatch.setattr(es, "get_embed_client", lambda: _FakeEngine())

    out = es.embed_texts(["a", "b"])
    assert out == [[0.0], [0.0]]
    assert armed and armed[0] == es._EMB_SEQ

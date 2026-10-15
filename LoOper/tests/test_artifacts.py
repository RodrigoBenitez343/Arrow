"""HTML artifact storage for Output nodes (render_mode='html').

Run:  python -m pytest LoOper/tests/test_artifacts.py
"""

import os

from NGUI.widgets.artifacts import artifacts_dir, save_artifact


def test_fragment_is_wrapped_into_document(tmp_path, monkeypatch):
    monkeypatch.setattr("NGUI.widgets.artifacts.artifacts_dir", lambda: str(tmp_path))
    path = save_artifact("<h1>hi</h1>")
    assert os.path.isfile(path)
    text = open(path, encoding="utf-8").read()
    assert text.startswith("<!DOCTYPE html>")
    assert "<h1>hi</h1>" in text


def test_full_document_passes_through(tmp_path, monkeypatch):
    monkeypatch.setattr("NGUI.widgets.artifacts.artifacts_dir", lambda: str(tmp_path))
    doc = "<!DOCTYPE html><html><body><script>1</script></body></html>"
    path = save_artifact(doc)
    assert open(path, encoding="utf-8").read() == doc


def test_code_fence_is_stripped(tmp_path, monkeypatch):
    monkeypatch.setattr("NGUI.widgets.artifacts.artifacts_dir", lambda: str(tmp_path))
    path = save_artifact("```html\n<!DOCTYPE html><html></html>\n```")
    text = open(path, encoding="utf-8").read()
    assert text == "<!DOCTYPE html><html></html>"
    assert "```" not in text


def test_path_lives_under_artifacts_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("NGUI.widgets.artifacts.artifacts_dir", lambda: str(tmp_path))
    path = save_artifact("<p>x</p>", label="screen content")
    assert os.path.dirname(path) == str(tmp_path)
    assert path.endswith(".html")


def test_artifacts_dir_is_named_artifacts():
    assert os.path.basename(artifacts_dir()) == "artifacts"
    assert os.path.isdir(artifacts_dir())

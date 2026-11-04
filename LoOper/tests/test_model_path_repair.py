"""A configured model path that no longer exists must still resolve.

Field report: ``LoOper/AI/config.json`` in this tree carries
``D:\\LoOperV2\\LoOper\\AI\\models\\SmolLM3-Q4_K_M.gguf`` — a checkout that does
not exist here.  Every consumer of the app-default model (LLM nodes, the Input
decision evaluator, the agent's reasoner) was handed that dead path, so it
either failed to load or the agent had "no model" at all.

The rule: a relative name resolves against ``AI/models``; an ABSOLUTE path that
is gone is looked up there by BASENAME (the model moved with the checkout); an
unknown name is returned untouched so the caller can report it.

Run:  python -m pytest LoOper/tests/test_model_path_repair.py
"""

import os

import pytest  # noqa: E402

from player.multi_sequence.llm_executor_resources import config_utils  # noqa


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    """A stand-in AI/models folder with one model in it."""
    (tmp_path / "SmolLM3-Q4_K_M.gguf").write_bytes(b"gguf")
    monkeypatch.setattr("AI.config_loader.get_models_dir", lambda: str(tmp_path))
    return tmp_path


def test_a_relative_name_resolves_inside_ai_models(models_dir):
    resolved = config_utils.resolve_llamacpp_model_path("SmolLM3-Q4_K_M.gguf")
    assert resolved == os.path.join(str(models_dir), "SmolLM3-Q4_K_M.gguf")


def test_an_absolute_path_that_exists_is_kept(models_dir):
    live = str(models_dir / "SmolLM3-Q4_K_M.gguf")
    assert config_utils.resolve_llamacpp_model_path(live) == live


def test_a_moved_checkout_resolves_by_basename(models_dir):
    stale = r"D:\LoOperV2\LoOper\AI\models\SmolLM3-Q4_K_M.gguf"
    assert not os.path.exists(stale)
    resolved = config_utils.resolve_llamacpp_model_path(stale)
    assert resolved == os.path.join(str(models_dir), "SmolLM3-Q4_K_M.gguf")


def test_an_unknown_model_is_returned_untouched(models_dir):
    """Nothing exists to point at: the caller reports it, no silent substitute."""
    stale = r"D:\nowhere\SomeModel-7B.gguf"
    assert config_utils.resolve_llamacpp_model_path(stale) == stale


def test_the_app_default_model_is_repaired_too(models_dir, monkeypatch):
    monkeypatch.setattr(
        "AI.config_loader.get_llamacpp_config",
        lambda: {"model_path": r"D:\LoOperV2\LoOper\AI\models\SmolLM3-Q4_K_M.gguf"})
    assert config_utils.get_default_llm_model() == os.path.join(
        str(models_dir), "SmolLM3-Q4_K_M.gguf")

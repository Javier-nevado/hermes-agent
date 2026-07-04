"""Unit tests for abi.memory.model_cache — presence/hash checks and the
download-verify-cache lifecycle (download transport mocked). Pure-Python."""

import hashlib

from abi.memory import model_cache


def _register(name, files):
    model_cache.register_model(name, files)


def test_is_cached_present_no_hash(tmp_path):
    _register("t_present", [("f.txt", None)])
    d = tmp_path / "t_present"
    d.mkdir()
    (d / "f.txt").write_text("hi")
    assert model_cache.is_cached("t_present", tmp_path) is True


def test_is_cached_missing(tmp_path):
    _register("t_missing", [("f.txt", None)])
    assert model_cache.is_cached("t_missing", tmp_path) is False


def test_is_cached_hash_mismatch(tmp_path):
    h = hashlib.sha256(b"hi").hexdigest()
    _register("t_bad", [("f.txt", h)])
    d = tmp_path / "t_bad"
    d.mkdir()
    (d / "f.txt").write_text("bye")  # wrong content
    assert model_cache.is_cached("t_bad", tmp_path) is False


def test_is_cached_hash_match(tmp_path):
    h = hashlib.sha256(b"hi").hexdigest()
    _register("t_good", [("f.txt", h)])
    d = tmp_path / "t_good"
    d.mkdir()
    (d / "f.txt").write_text("hi")
    assert model_cache.is_cached("t_good", tmp_path) is True


def test_ensure_model_unknown_returns_none(tmp_path):
    assert model_cache.ensure_model("does_not_exist", tmp_path) is None


def test_ensure_model_downloads_and_verifies(tmp_path, monkeypatch):
    payload = b"payload"
    h = hashlib.sha256(payload).hexdigest()
    _register("t_dl", [("f.bin", h)])

    def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
    monkeypatch.setattr(model_cache, "_download_file", fake_download)

    d = model_cache.ensure_model("t_dl", tmp_path)
    assert d is not None
    assert (d / "f.bin").read_bytes() == payload
    assert model_cache.is_cached("t_dl", tmp_path)


def test_ensure_model_rejects_bad_hash_and_removes(tmp_path, monkeypatch):
    _register("t_badhash", [("f.bin", hashlib.sha256(b"expected").hexdigest())])

    def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"wrong")
    monkeypatch.setattr(model_cache, "_download_file", fake_download)

    assert model_cache.ensure_model("t_badhash", tmp_path) is None
    assert not (tmp_path / "t_badhash" / "f.bin").exists()  # poisoned file removed


def test_ensure_model_skips_download_when_cached(tmp_path, monkeypatch):
    payload = b"x"
    h = hashlib.sha256(payload).hexdigest()
    _register("t_skip", [("f.bin", h)])
    d = tmp_path / "t_skip"
    d.mkdir()
    (d / "f.bin").write_bytes(payload)

    calls = {"n": 0}

    def fake_download(url, dest):
        calls["n"] += 1
    monkeypatch.setattr(model_cache, "_download_file", fake_download)

    assert model_cache.ensure_model("t_skip", tmp_path) is not None
    assert calls["n"] == 0  # no network when already cached + valid


def test_get_cache_root_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ABI_MEMORY_MODELS_CACHE_DIR", str(tmp_path / "custom"))
    assert model_cache.get_cache_root() == tmp_path / "custom"


def test_get_cache_root_hermes_home(tmp_path):
    assert model_cache.get_cache_root(str(tmp_path)) == tmp_path / "abi_models"

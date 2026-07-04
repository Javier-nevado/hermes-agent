"""Verified download-on-first-run model cache for ABI memory ONNX models.

Models are NOT bundled in the image (keeps it small). On first use, model files
are downloaded once into the cache dir (default ``{HERMES_HOME}/abi_models``),
sha256-verified, and reused thereafter — no network on the hot path.

Download source defaults to Opteia-controlled hosting
(``ABI_MODELS_BASE_URL``, mirror of the secure-self-update posture), so customer
VMs do not depend on third-party (HuggingFace) availability. The sha256 manifest
is baked into the :data:`MODELS` registry below; expected hashes are filled in
once each model artifact is exported + published (Step 0 prep). A ``None`` hash
means "skip verification" (development only).

All network access is confined to :func:`ensure_model`, which is only called from
background/lazy paths (never from recall on the request path). :func:`is_cached`
performs a hash check with NO network and is what availability probes use.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default download base. Override with ABI_MODELS_BASE_URL for self-hosting/mirrors.
DEFAULT_BASE_URL = "https://api.opteia.com/abi-models"

# Registry of downloadable model artifacts.
#   name -> {"files": [(filename, expected_sha256_hex_or_None), ...]}
# Expected hashes are populated when each artifact is exported + published.
# A None hash skips verification (development/local builds only).
MODELS: Dict[str, Dict[str, List[Tuple[str, Optional[str]]]]] = {
    # cross-encoder ms-marco-MiniLM-L-6-v2 (~90MB) — recall reranking + dedup.
    # Sourced from the Xenova/ms-marco-MiniLM-L-6-v2 ONNX port (faithful export of
    # cross-encoder/ms-marco-MiniLM-L-6-v2), hosted at ABI_MODELS_BASE_URL/reranker/.
    "reranker": {
        "files": [
            ("model.onnx", "c623d0bcb99f4622beb413eaef00cfbe5db20df9f1dd982da4b4f26022881870"),
            ("tokenizer.json", "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"),
        ],
    },
    # (PR 2) multilingual NER — Davlan/mBERT-NER.
    # (PR 3) multilingual zero-shot classifier — mDeBERTa NLI.
}

# How long a single file download may take, in seconds.
_DOWNLOAD_TIMEOUT = 120
_MAX_RETRIES = 3

# User-Agent sent on model downloads. REQUIRED: api.opteia.com sits behind
# Cloudflare Bot Fight Mode, which 403s urllib's default "Python-urllib/x.y" UA
# (verified 2026-07-04 — only that UA was blocked; curl/custom UAs pass). A
# descriptive custom UA passes cleanly; env-overridable as a safety valve if a
# future CF rule ever targets it.
_USER_AGENT = os.environ.get(
    "ABI_MODELS_USER_AGENT", "abi-memory-model-cache/1.0 (+https://opteia.com)"
)


def get_cache_root(hermes_home: Optional[str] = None) -> Path:
    """Resolve the model cache root.

    Precedence: ``ABI_MEMORY_MODELS_CACHE_DIR`` env > ``{hermes_home}/abi_models``
    > ``~/.abi_models``.
    """
    env = os.environ.get("ABI_MEMORY_MODELS_CACHE_DIR")
    if env:
        return Path(env)
    if hermes_home:
        return Path(hermes_home) / "abi_models"
    return Path.home() / ".abi_models"


def _base_url() -> str:
    return os.environ.get("ABI_MODELS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def model_dir(name: str, cache_root: Path) -> Path:
    return Path(cache_root) / name


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_cached(name: str, cache_root: Path) -> bool:
    """True if every registered file is present and its sha256 matches (no network).

    Models with a ``None`` expected hash pass on presence alone.
    """
    spec = MODELS.get(name)
    if spec is None:
        return False
    d = model_dir(name, cache_root)
    for filename, expected in spec["files"]:
        path = d / filename
        if not path.exists():
            return False
        if expected and _sha256(path) != expected:
            return False
    return True


def _download_file(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` atomically with retries."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Optional[Exception] = None
    for attempt in range(1, _MAX_RETRIES + 1):
        tmp: Optional[Path] = None
        try:
            req = urllib.request.Request(  # noqa: S310 (trusted Opteia-hosted URL)
                url, headers={"User-Agent": _USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
                with tempfile.NamedTemporaryFile(dir=str(dest.parent), delete=False) as tmp_fh:
                    tmp = Path(tmp_fh.name)
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        tmp_fh.write(chunk)
            os.replace(tmp, dest)
            return
        except Exception as exc:  # network/HTTP errors
            last_err = exc
            if tmp is not None and tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            logger.warning("model_cache download attempt %d/%d failed for %s: %s",
                           attempt, _MAX_RETRIES, url, exc)
    raise RuntimeError(f"download failed after {_MAX_RETRIES} attempts: {last_err}")


def ensure_model(name: str, cache_root: Path) -> Optional[Path]:
    """Ensure all files for ``name`` are present (downloading if absent).

    Returns the model directory, or ``None`` on any failure (network error,
    sha256 mismatch, unknown model). Never raises — callers degrade gracefully.
    """
    spec = MODELS.get(name)
    if spec is None:
        logger.warning("model_cache: unknown model %r", name)
        return None

    d = model_dir(name, cache_root)
    d.mkdir(parents=True, exist_ok=True)
    base = _base_url()

    for filename, expected in spec["files"]:
        path = d / filename
        # Present + valid? skip.
        if path.exists() and (expected is None or _sha256(path) == expected):
            continue
        try:
            logger.info("model_cache: downloading %s/%s ...", name, filename)
            _download_file(f"{base}/{name}/{filename}", path)
        except Exception as exc:
            logger.warning("model_cache: could not fetch %s/%s: %s", name, filename, exc)
            return None
        if expected is not None and _sha256(path) != expected:
            logger.error("model_cache: sha256 mismatch for %s/%s — removing", name, filename)
            try:
                path.unlink()
            except OSError:
                pass
            return None

    logger.info("model_cache: %s ready at %s", name, d)
    return d


def register_model(name: str, files: List[Tuple[str, Optional[str]]]) -> None:
    """Add or replace a model entry at runtime (e.g. for tests or plugins)."""
    MODELS[name] = {"files": list(files)}

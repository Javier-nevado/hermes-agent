"""Cross-encoder reranker (ONNX) for ABI memory recall.

Uses ms-marco-MiniLM-L-6-v2 as a relevance cross-encoder: given a query and a
list of candidate memory texts, returns a relevance score per candidate. Used to
rescore the top-N RRF/graph-boosted candidates for sharper top-k precision, and
(PR 3) for "is this already known?" dedup during auto-extraction.

Mirrors :mod:`abi.memory.embeddings`' ONNX pattern (lazy ``InferenceSession`` on
CPU, ``tokenizers`` lib). The model is fetched on first use via
:mod:`abi.memory.model_cache` (download-on-first-run). All network is confined to
the first :meth:`Reranker.warmup`/``rerank`` call; :meth:`is_available` is a pure
in-memory check.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import List, Optional

import numpy as np

from .model_cache import ensure_model, is_cached

logger = logging.getLogger(__name__)

_MODEL_NAME = "reranker"
_MAX_LEN = 256  # cross-encoder pair cap (query + candidate)


class Reranker:
    """Lazy cross-encoder reranker backed by the model cache."""

    def __init__(self, cache_root: Path) -> None:
        self._cache_root = Path(cache_root)
        self._session = None
        self._tokenizer = None
        self._needs_type_ids = False
        self._tried_load = False
        self._lock = threading.Lock()

    def warmup(self) -> bool:
        """Attempt to load the model (downloads on first run). Non-fatal."""
        return self._load()

    def is_available(self) -> bool:
        """True if the model is loaded and ready. No network."""
        return self._session is not None

    def _load(self) -> bool:
        if self._session is not None:
            return True
        with self._lock:
            if self._session is not None:
                return True
            if self._tried_load:  # don't retry every call after a first failure
                return False
            self._tried_load = True

        model_dir = ensure_model(_MODEL_NAME, self._cache_root)
        if model_dir is None:
            # No download (offline/egress blocked) but maybe a cached copy exists.
            if not is_cached(_MODEL_NAME, self._cache_root):
                return False
            model_dir = self._cache_root / _MODEL_NAME

        onnx_path = model_dir / "model.onnx"
        tok_path = model_dir / "tokenizer.json"
        if not onnx_path.exists() or not tok_path.exists():
            return False

        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            self._session = ort.InferenceSession(
                str(onnx_path),
                providers=["CPUExecutionProvider"],
            )
            # Provide token_type_ids only if the exported graph expects them.
            self._needs_type_ids = any(
                "type" in (inp.name or "").lower() for inp in self._session.get_inputs()
            )
            self._tokenizer = Tokenizer.from_file(str(tok_path))
            self._tokenizer.enable_truncation(max_length=_MAX_LEN)
            pad_id = self._tokenizer.token_to_id("[PAD]") or 0
            self._tokenizer.enable_padding(pad_id=pad_id, pad_token="[PAD]", length=_MAX_LEN)
            logger.info("Reranker model loaded (cross-encoder)")
            return True
        except Exception as exc:
            logger.warning("Reranker load failed: %s", exc)
            self._session = None
            self._tokenizer = None
            return False

    def rerank(self, query: str, candidates: List[str]) -> Optional[List[float]]:
        """Score each candidate against ``query`` (higher = more relevant).

        Returns a list of floats aligned with ``candidates`` (sigmoid'd logits),
        or ``None`` if the model is unavailable (caller falls back to RRF order).
        """
        if not candidates:
            return []
        if not self._load():
            return None

        try:
            input_ids, attention_mask, type_ids = [], [], []
            for cand in candidates:
                enc = self._tokenizer.encode(query, cand)
                input_ids.append(enc.ids)
                attention_mask.append(enc.attention_mask)
                type_ids.append(enc.type_ids if enc.type_ids else [0] * len(enc.ids))

            feeds = {
                "input_ids": np.array(input_ids, dtype=np.int64),
                "attention_mask": np.array(attention_mask, dtype=np.int64),
            }
            if self._needs_type_ids:
                feeds["token_type_ids"] = np.array(type_ids, dtype=np.int64)

            outputs = self._session.run(None, feeds)
            logits = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
            # sigmoid -> relevance in (0,1); clip to avoid overflow on large logits.
            scores = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
            return [float(s) for s in scores]
        except Exception as exc:
            logger.warning("Rerank inference failed: %s", exc)
            return None

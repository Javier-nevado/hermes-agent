"""Embedding client using ONNX Runtime.

Uses all-MiniLM-L6-v2 (384d) via ONNX for fast, low-memory inference.
No torch or transformers needed at runtime.
Cold start: ~0.2s | Warm inference: ~5ms | RAM: ~50MB
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).parent / "onnx_model"
_session = None
_tokenizer = None
_PAD_ID = 0  # [PAD] token id for BERT


def _init_onnx():
    """Lazy-init ONNX session + tokenizer."""
    global _session, _tokenizer
    if _session is not None:
        return True

    onnx_path = _MODEL_DIR / "model.onnx"
    tok_path = _MODEL_DIR / "tokenizer.json"
    if not onnx_path.exists() or not tok_path.exists():
        return False

    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        _session = ort.InferenceSession(
            str(onnx_path),
            providers=["CPUExecutionProvider"],
        )
        _tokenizer = Tokenizer.from_file(str(tok_path))
        _tokenizer.enable_truncation(max_length=128)
        _tokenizer.enable_padding(pad_id=_PAD_ID, pad_token="[PAD]", length=128)
        logger.info("ONNX embedding model loaded (384d)")
        return True
    except Exception as e:
        logger.warning("ONNX init failed: %s", e)
        return False


def _mean_pool(hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Mean pooling over token embeddings, masked."""
    mask_expanded = mask[:, :, np.newaxis]
    pooled = (hidden * mask_expanded).sum(axis=1) / mask_expanded.sum(axis=1)
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return pooled / norms


def get_embedding(text: str) -> Optional[List[float]]:
    """Generate 384-dim embedding for text.

    Uses ONNX Runtime (fast, no torch). Falls back to sentence-transformers.
    """
    if not text or not text.strip():
        return None

    # Try ONNX first
    if _init_onnx():
        try:
            enc = _tokenizer.encode(text)
            input_ids = np.array([enc.ids], dtype=np.int64)
            attention_mask = np.array([enc.attention_mask], dtype=np.int64)
            outputs = _session.run(None, {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            })
            mask = attention_mask.astype(np.float32)
            normed = _mean_pool(outputs[0], mask)
            return normed[0].tolist()
        except Exception as e:
            logger.warning("ONNX inference failed: %s", e)

    # Fallback: sentence-transformers
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        return model.encode(text, normalize_embeddings=True).tolist()
    except Exception:
        return None

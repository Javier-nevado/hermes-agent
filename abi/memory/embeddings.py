"""Embedding client using sentence-transformers.

Uses all-MiniLM-L6-v2 (384 dimensions) for semantic search.
Model downloads on first use (~90MB).
"""

from typing import List, Optional

_embedding_model = None


def get_embedding(text: str) -> Optional[List[float]]:
    """Generate embedding for a text string.

    Returns 384-dimensional vector, or None if model unavailable.
    """
    global _embedding_model

    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            return None
        except Exception:
            return None

    try:
        embedding = _embedding_model.encode(text, normalize_embeddings=True)
        return embedding.tolist()
    except Exception:
        return None

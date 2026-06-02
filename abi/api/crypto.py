"""AES-256-GCM encryption for ABI memory content at rest.

Encrypts the `content` field before writing to PostgreSQL, decrypts on read.
DEK (Data Encryption Key) is delivered via the Cloudflare Worker alongside
the JWT, cached in LicenseManager memory.

Storage format: base64(iv[12] + tag[16] + ciphertext)
"""

from __future__ import annotations

import base64
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

IV_SIZE = 12
TAG_SIZE = 16  # GCM auth tag is always 16 bytes


class EncryptionService:
    """Encrypts/decrypts memory content using AES-256-GCM."""

    def __init__(self, dek: bytes) -> None:
        if len(dek) != 32:
            raise ValueError(f"DEK must be 32 bytes, got {len(dek)}")
        self._aesgcm = AESGCM(dek)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string. Returns base64-encoded blob."""
        iv = os.urandom(IV_SIZE)
        ct = self._aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
        return base64.b64encode(iv + ct).decode("ascii")

    def decrypt(self, blob: str) -> str:
        """Decrypt base64-encoded blob. Returns plaintext string."""
        raw = base64.b64decode(blob)
        iv = raw[:IV_SIZE]
        ct = raw[IV_SIZE:]
        return self._aesgcm.decrypt(iv, ct, None).decode("utf-8")

    @staticmethod
    def is_encrypted(value: str) -> bool:
        """Check if a value looks like an encrypted blob.

        Heuristic: valid base64, decodes to more than iv+tag bytes.
        """
        if not value or len(value) < 40:
            return False
        try:
            raw = base64.b64decode(value, validate=True)
            return len(raw) > IV_SIZE + TAG_SIZE
        except Exception:
            return False

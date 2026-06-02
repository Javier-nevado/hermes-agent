"""One-time migration: encrypt all existing plaintext content in abi_memories.

Usage (inside API container or with ABI_DATABASE_URL set):
    python -m abi.api.migrate_encrypt

Steps:
1. SELECT all memories with plaintext content
2. Encrypt content, regenerate fts from original plaintext
3. UPDATE in batches of 100
4. Print progress

IMPORTANT: Run `pg_dump` before this script. No exceptions.
"""

from __future__ import annotations

import base64
import os
import sys

import psycopg2
import psycopg2.extras

# Reuse EncryptionService
from abi.api.crypto import EncryptionService


def migrate(dek: bytes, db_url: str, batch_size: int = 100) -> None:
    enc = EncryptionService(dek)
    conn = psycopg2.connect(db_url)
    conn.autocommit = False

    try:
        # Count total rows
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM abi_memories")
            total = cur.fetchone()[0]
        print(f"Total memories: {total}")

        # Process in batches
        encrypted_count = 0
        skipped = 0
        offset = 0

        while True:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, content FROM abi_memories ORDER BY id OFFSET %s LIMIT %s",
                    [offset, batch_size],
                )
                rows = cur.fetchall()

            if not rows:
                break

            for row in rows:
                content = row["content"]
                if not content or enc.is_encrypted(content):
                    skipped += 1
                    continue

                # Encrypt and update
                encrypted = enc.encrypt(content)
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE abi_memories
                           SET content = %s, fts = to_tsvector('english', %s)
                           WHERE id = %s""",
                        [encrypted, content, row["id"]],
                    )

                encrypted_count += 1
                if encrypted_count % 100 == 0:
                    conn.commit()
                    print(f"  Encrypted {encrypted_count}/{total}...")

            offset += batch_size

        conn.commit()
        print(f"\nDone: {encrypted_count} encrypted, {skipped} skipped (already encrypted)")
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    db_url = os.environ.get("ABI_DATABASE_URL")
    if not db_url:
        print("ERROR: ABI_DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    dek_b64 = sys.argv[1] if len(sys.argv) > 1 else None
    if not dek_b64:
        print("Usage: python -m abi.api.migrate_encrypt <DEK_BASE64>")
        print("  Get the DEK from: curl 'https://api.opteia.com/license/health?key=ABI-INTERNAL-DEV'")
        sys.exit(1)

    dek = base64.b64decode(dek_b64)
    print(f"DEK size: {len(dek)} bytes")
    print("Starting encryption migration...")
    migrate(dek, db_url)

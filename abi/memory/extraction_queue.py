"""Durable write-behind queue for ABI memory auto-extraction (PR 3).

Turns (and full sessions) handed to :meth:`ExtractionQueue.enqueue` are persisted
to a local SQLite file *before* any work happens, then drained by a daemon
worker thread that runs the extraction pipeline. If the process crashes, pending
rows are replayed on the next start — no turn is silently lost.

This mirrors ``plugins/memory/retaindb/__init__.py``'s ``_WriteQueue`` (SQLite +
``queue.Queue`` + daemon thread + crash-replay-on-init + sentinel shutdown),
because ``sync_turn`` is called **synchronously on the turn path**
(``run_agent.py:_sync_external_memory_for_turn``); extraction must enqueue-and-
return without blocking the response.

The queue is intentionally generic: it knows nothing about how a turn becomes
memories. ``processor`` is a callable ``(payload: dict) -> None`` supplied by
:mod:`abi.api.deps` (a closure over the shared DB pool / reranker / entity
extractor). Keeps this module free of DB/ONNX imports so it unit-tests trivially.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Sentinel pushed to shut the worker down cleanly (identity-checked, not ==).
_SHUTDOWN = object()

# Back off after a failing row so a sick downstream (DB down, model error) does
# not spin a tight retry loop. Pending rows survive — they retry next drain.
_RETRY_SLEEP = 2.0
# Give the worker a moment to drain on shutdown before we stop waiting.
_JOIN_TIMEOUT = 15.0
# Drop rows that have been retried past this many attempts — protects against a
# permanently-poisonous payload (e.g. un-decodable content) parking the queue.
_MAX_ATTEMPTS = 8


class ExtractionQueue:
    """SQLite-backed durable queue feeding a daemon extraction worker."""

    def __init__(self, db_path: Path, processor: Callable[[dict], None]) -> None:
        self._db_path = Path(db_path)
        self._processor = processor
        self._q: "queue.Queue" = queue.Queue()
        self._local = threading.local()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._thread = threading.Thread(
            target=self._loop, name="abi-extraction-worker", daemon=True
        )
        self._thread.start()
        # Replay anything left from a previous crash before accepting new work.
        replayed = self._replay_pending()
        if replayed:
            logger.info("extraction_queue: replayed %d pending turn(s) after restart", replayed)

    # ------------------------------------------------------------------ db

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._db_path), timeout=30)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pending (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )"""
        )
        conn.commit()

    def _replay_pending(self) -> int:
        rows = self._conn().execute(
            "SELECT id, payload_json FROM pending ORDER BY id ASC LIMIT 500"
        ).fetchall()
        for row in rows:
            try:
                self._q.put({"id": row["id"], "payload": json.loads(row["payload_json"])})
            except json.JSONDecodeError:
                # Poisonous row from a half-written crash — drop it so it can't loop.
                logger.warning("extraction_queue: dropping undecodable pending row %s", row["id"])
                self._conn().execute("DELETE FROM pending WHERE id = ?", (row["id"],))
                self._conn().commit()
        return len(rows)

    # ---------------------------------------------------------------- public

    def enqueue(self, payload: dict) -> None:
        """Persist ``payload`` and wake the worker. Returns immediately."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO pending (payload_json, created_at) VALUES (?, ?)",
            (json.dumps(payload, ensure_ascii=False), now),
        )
        row_id = cur.lastrowid
        conn.commit()
        self._q.put({"id": row_id, "payload": payload})

    def shutdown(self) -> None:
        self._q.put(_SHUTDOWN)
        self._thread.join(timeout=_JOIN_TIMEOUT)

    @property
    def pending_count(self) -> int:
        try:
            row = self._conn().execute("SELECT count(*) AS n FROM pending").fetchone()
            return int(row["n"]) if row else 0
        except Exception:
            return 0

    # ---------------------------------------------------------------- worker

    def _loop(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=5)
                if item is _SHUTDOWN:
                    break
                self._process(item)
            except queue.Empty:
                continue
            except Exception as exc:  # never let the worker die
                logger.error("extraction_queue worker error: %s", exc)

    def _process(self, item: dict) -> None:
        row_id = item["id"]
        payload = item["payload"]
        try:
            self._processor(payload)
            conn = self._conn()
            conn.execute("DELETE FROM pending WHERE id = ?", (row_id,))
            conn.commit()
        except Exception as exc:
            # Bump attempts; drop if poisonous, else back off and leave for retry.
            conn = self._conn()
            row = conn.execute(
                "SELECT attempts FROM pending WHERE id = ?", (row_id,)
            ).fetchone()
            attempts = (row["attempts"] + 1) if row else _MAX_ATTEMPTS
            if attempts >= _MAX_ATTEMPTS:
                logger.error(
                    "extraction_queue: dropping row %s after %d failed attempts: %s",
                    row_id, attempts, exc,
                )
                conn.execute("DELETE FROM pending WHERE id = ?", (row_id,))
            else:
                logger.warning(
                    "extraction_queue: row %s failed (attempt %d, will retry): %s",
                    row_id, attempts, exc,
                )
                conn.execute(
                    "UPDATE pending SET attempts = ?, last_error = ? WHERE id = ?",
                    (attempts, str(exc)[:500], row_id),
                )
            conn.commit()
            time.sleep(_RETRY_SLEEP)

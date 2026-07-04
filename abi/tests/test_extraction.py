"""Unit tests for PR 3 auto-extraction: candidate selection, heuristic
classification, dedup-skipping, and the durable queue's crash-replay.

Pure-Python — no FastAPI / psycopg2 / ONNX. The extractor and the queue are
self-contained modules, so these run under any interpreter:

    PYTHONPATH=. python3 -m pytest abi/tests/test_extraction.py -v
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from abi.memory.extractor import (
    Candidate,
    _classify,
    _is_trivia,
    is_duplicate,
    process_turn,
    select_candidates,
)
from abi.memory.extraction_queue import ExtractionQueue


# --------------------------------------------------------------------------- #
# Candidate selection + heuristic classification
# --------------------------------------------------------------------------- #

class TestSelectCandidates(unittest.TestCase):
    def test_trivia_acknowledgement_filtered(self):
        self.assertEqual(select_candidates("ok", "sure, will do"), [])

    def test_short_question_filtered(self):
        self.assertEqual(select_candidates("ready?", "yes"), [])

    def test_greeting_filtered(self):
        self.assertEqual(select_candidates("hey", "hello! how are you?"), [])

    def test_decision_kept_and_typed(self):
        cands = select_candidates("We decided to use PostgreSQL for the new project.", "")
        self.assertTrue(cands, "expected at least one candidate")
        self.assertTrue(any(c.memory_type == "decision" for c in cands))

    def test_preference_kept_and_typed(self):
        cands = select_candidates("I prefer emails in the morning, never after 6pm.", "")
        self.assertIn("preference", {c.memory_type for c in cands})

    def test_identity_kept_and_high_importance(self):
        cands = select_candidates("I am Javier, the CEO of Opteia.", "")
        ids = [c for c in cands if c.memory_type == "identity"]
        self.assertTrue(ids)
        self.assertGreaterEqual(ids[0].importance, 0.8)

    def test_amount_lifts_importance(self):
        cands = select_candidates("The retainer is €149/mo.", "")
        self.assertTrue(any(c.importance >= 0.8 for c in cands))

    def test_assistant_prose_without_signal_skipped(self):
        cands = select_candidates("", "I will now check the system and get back to you shortly.")
        self.assertEqual(cands, [])

    def test_dedup_within_turn(self):
        # the same statement repeated verbatim in user + assistant yields one candidate
        cands = select_candidates(
            "We decided to use PostgreSQL.",
            "We decided to use PostgreSQL.",
        )
        pg = [c for c in cands if "postgresql" in c.text.lower()]
        self.assertLessEqual(len(pg), 1)


class TestClassify(unittest.TestCase):
    def test_trivia_detector(self):
        self.assertTrue(_is_trivia("ok"))
        self.assertTrue(_is_trivia("Thanks!"))
        self.assertTrue(_is_trivia("ready?"))
        self.assertFalse(_is_trivia("We decided to deploy on Friday."))

    def test_decision_indicator(self):
        mtype, imp = _classify("We decided to migrate to GLM this week.")
        self.assertEqual(mtype, "decision")
        self.assertGreaterEqual(imp, 0.8)

    def test_preference_indicator(self):
        mtype, _ = _classify("I always want PDF reports, never HTML.")
        self.assertEqual(mtype, "preference")

    def test_event_indicator(self):
        mtype, _ = _classify("We deployed the fix yesterday and it broke staging.")
        self.assertEqual(mtype, "event")

    def test_intent_verb_decision(self):
        # Request-style phrasing — "let's create a weekly report" — must classify
        # as a decision (commitment to establish standing work), not a lowly fact.
        mtype, imp = _classify("Let's create a weekly report for proxmox.")
        self.assertEqual(mtype, "decision")
        self.assertGreaterEqual(imp, 0.8)

    def test_cadence_lifts_importance(self):
        # "Friday evening" carries no decision/preference keyword; without the
        # cadence signal it defaults to fact 0.5 (below floor). Cadence lifts it.
        _, imp = _classify("Friday evening sounds be fine.")
        self.assertGreaterEqual(imp, 0.8)
        _, imp2 = _classify("Send it every Monday morning.")
        self.assertGreaterEqual(imp2, 0.8)


# --------------------------------------------------------------------------- #
# process_turn pipeline (writer + embed mocked)
# --------------------------------------------------------------------------- #

def _fake_writer():
    written: list = []

    def writer(content, agent_name, user_id, *, dlp_level=None, memory_type=None, importance=None):
        written.append((content, memory_type, importance))
        return f"mem-{len(written)}"

    return written, writer


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._sql = sql

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Fake psycopg2 conn returning canned rows from the dedup neighbor query."""

    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


class TestProcessTurn(unittest.TestCase):
    def test_trivia_turn_saves_nothing(self):
        written, writer = _fake_writer()
        stats = process_turn(
            {"agent_name": "atlas", "user_content": "ok thanks", "assistant_content": "sure"},
            conn=None, embed_fn=lambda s: None, writer=writer,
        )
        self.assertEqual(stats["saved"], 0)
        self.assertEqual(written, [])

    def test_decision_saved_low_importance_filtered(self):
        written, writer = _fake_writer()
        # First sentence: decision (imp 0.85, saved). Second: low-value fact (imp 0.5, floored).
        stats = process_turn(
            {"agent_name": "atlas",
             "user_content": "We decided to use PostgreSQL for storage. The sky is usually blue.",
             "assistant_content": ""},
            conn=None, embed_fn=lambda s: None, writer=writer, min_importance=0.6,
        )
        self.assertGreaterEqual(stats["saved"], 1)
        self.assertTrue(any("PostgreSQL" in c or "postgresql" in c for c, _, _ in written))
        self.assertGreaterEqual(stats["skipped_trivia_floor"], 1)

    def test_real_request_message_captures_all_points(self):
        # Regression: Javier's actual proxmox-report request. Before the lexicon
        # boost only the HTML preference survived (decision + cadence dropped at
        # the 0.6 floor). All three durable points must now be saved.
        written, writer = _fake_writer()
        msg = ("Let's create a weekly report for proxmox, status, errors, "
               "things to be aware of. Let's make it on html, send it on this "
               "channel. Friday evening sounds be fine.")
        stats = process_turn(
            {"agent_name": "atlas", "user_content": msg, "assistant_content": ""},
            conn=None, embed_fn=lambda s: None, writer=writer, min_importance=0.6,
        )
        self.assertGreaterEqual(stats["saved"], 3)
        types = {t for _, t, _ in written}
        self.assertIn("decision", types)      # "let's create a weekly report"
        self.assertIn("preference", types)    # "make it on html"
        blob = " ".join(c for c, _, _ in written).lower()
        self.assertIn("friday", blob)         # cadence point survived

    def test_duplicate_skipped_via_cosine(self):
        written, writer = _fake_writer()
        # One neighbor at cosine 0.92 → above 0.85 threshold → skip.
        fake_conn = _FakeConn([("existing decision about postgres storage", 0.92)])
        stats = process_turn(
            {"agent_name": "atlas",
             "user_content": "We decided to use PostgreSQL for storage.",
             "assistant_content": ""},
            conn=fake_conn, embed_fn=lambda s: [0.1] * 8, writer=writer,
            dedup_threshold=0.85, min_importance=0.6,
        )
        self.assertEqual(stats["saved"], 0)
        self.assertEqual(stats["skipped_dup"], 1)
        self.assertEqual(written, [])

    def test_no_duplicate_when_below_threshold(self):
        written, writer = _fake_writer()
        fake_conn = _FakeConn([("something unrelated about email", 0.40)])
        stats = process_turn(
            {"agent_name": "atlas",
             "user_content": "We decided to use PostgreSQL for storage.",
             "assistant_content": ""},
            conn=fake_conn, embed_fn=lambda s: [0.1] * 8, writer=writer,
            dedup_threshold=0.85, min_importance=0.6,
        )
        self.assertEqual(stats["saved"], 1)
        self.assertEqual(stats["skipped_dup"], 0)

    def test_writer_exception_does_not_abort_turn(self):
        def bad_writer(*a, **k):
            raise RuntimeError("DB down")
        # Should not raise; the bad candidate is skipped but process_turn returns.
        stats = process_turn(
            {"agent_name": "atlas",
             "user_content": "We decided to use PostgreSQL for storage.",
             "assistant_content": ""},
            conn=None, embed_fn=lambda s: None, writer=bad_writer, min_importance=0.6,
        )
        self.assertEqual(stats["saved"], 0)


class TestIsDuplicate(unittest.TestCase):
    def test_no_embed_fn_means_no_dup(self):
        self.assertFalse(is_duplicate("x", "a", conn=None, embed_fn=None))

    def test_no_neighbors_means_no_dup(self):
        self.assertFalse(is_duplicate(
            "x", "a", conn=_FakeConn([]), embed_fn=lambda s: [0.1] * 4, threshold=0.85,
        ))


# --------------------------------------------------------------------------- #
# Durable queue — enqueue/process + crash replay
# --------------------------------------------------------------------------- #

class TestExtractionQueue(unittest.TestCase):
    def test_enqueue_then_process(self):
        calls: list = []
        lock = threading.Lock()
        done = threading.Event()

        def processor(payload):
            with lock:
                calls.append(payload)
            done.set()

        with tempfile.TemporaryDirectory() as d:
            q = ExtractionQueue(Path(d) / "q.db", processor)
            q.enqueue({"hello": 1})
            try:
                self.assertTrue(done.wait(timeout=5), "processor was not called")
            finally:
                q.shutdown()
            self.assertEqual(calls, [{"hello": 1}])
            self.assertEqual(q.pending_count, 0)

    def test_crash_replay_on_restart(self):
        calls: list = []
        replayed = threading.Event()

        def processor(payload):
            calls.append(payload)
            replayed.set()

        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "q.db"
            # Simulate a crash: seed a pending row the worker never drained.
            con = sqlite3.connect(str(db))
            con.execute(
                "CREATE TABLE pending (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "payload_json TEXT NOT NULL, created_at TEXT NOT NULL, "
                "attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
            )
            con.execute(
                "INSERT INTO pending (payload_json, created_at) VALUES (?,?)",
                (json.dumps({"replayed": True}), "2026-01-01T00:00:00"),
            )
            con.commit()
            con.close()

            q = ExtractionQueue(db, processor)  # replays pending on init
            try:
                self.assertTrue(replayed.wait(timeout=5), "pending row was not replayed")
            finally:
                q.shutdown()

            self.assertTrue(any(c.get("replayed") for c in calls))
            # Successfully processed row is deleted.
            con = sqlite3.connect(str(db))
            n = con.execute("SELECT count(*) FROM pending").fetchone()[0]
            con.close()
            self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()

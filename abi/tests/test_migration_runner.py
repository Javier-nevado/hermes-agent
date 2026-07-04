"""Unit tests for abi.api.deps.apply_pending_migrations orchestration.

The DB is faked; real migration SQL is never executed here. These verify the
runner discovers ``abi/sql/*.sql``, applies each once, records it, and skips
already-applied files (idempotent) — and that a failing file is isolated."""

from pathlib import Path

import abi
import abi.api.deps as deps


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._fetch = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = sql if isinstance(sql, str) else sql.decode()
        self._conn.executed.append(s[:60])
        if "SELECT filename FROM abi_schema_migrations" in s:
            self._fetch = [(n,) for n in sorted(self._conn.applied)]
        elif "INSERT INTO abi_schema_migrations" in s and params:
            self._conn.applied.add(params[0])
        # CREATE TABLE / ALTER / migration bodies are no-ops in the fake.

    def fetchall(self):
        return self._fetch


class FakeConn:
    def __init__(self):
        self.autocommit = True
        self.applied = set()
        self.executed = []

    def cursor(self):
        return FakeCursor(self)


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def getconn(self):
        return self._conn

    def putconn(self, c):
        pass


def _expected_filenames():
    sql_dir = Path(abi.__file__).parent / "sql"
    return {p.name for p in sql_dir.glob("*.sql")}


def test_applies_all_migrations_then_skips(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(deps, "get_pool", lambda: FakePool(conn))

    deps.apply_pending_migrations()
    assert conn.applied == _expected_filenames()  # everything applied once

    executed_after_first = len(conn.executed)
    deps.apply_pending_migrations()
    assert conn.applied == _expected_filenames()  # still idempotent
    # Second call only re-runs the CREATE+SELECT probe (2 executes), no files.
    assert len(conn.executed) - executed_after_first == 2


def test_failure_is_isolated(monkeypatch):
    conn = FakeConn()

    # Make one specific migration's file text raise when executed.
    bad_name = sorted(_expected_filenames())[0]
    real_read_text = Path.read_text

    def flaky_read_text(self, *a, **kw):
        if self.name == bad_name:
            raise OSError("simulated read failure")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    monkeypatch.setattr(deps, "get_pool", lambda: FakePool(conn))

    deps.apply_pending_migrations()  # must not raise
    # The flaky file is skipped, but the rest still applied.
    assert bad_name not in conn.applied
    assert len(conn.applied) == len(_expected_filenames()) - 1

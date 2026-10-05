"""SQLite database: one connection, one lock, explicit transactions.

SQLite is enough for one server with a team of engineers. All access goes through this class, so a
move to PostgreSQL later touches this module and `stores.py`, not the rest of the program.
Times are UTC. Passwords, session tokens and the audit chain are described in `spc.auth`.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 7

SCHEMA = """
CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('viewer', 'operator', 'engineer', 'admin')),
    active        INTEGER NOT NULL DEFAULT 1,
    must_change   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf       TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen  REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX sessions_user ON sessions(user_id);
CREATE TABLE login_attempts (
    username     TEXT PRIMARY KEY,
    failures     INTEGER NOT NULL,
    locked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE datasets (
    id         TEXT PRIMARY KEY,
    owner_id   INTEGER NOT NULL REFERENCES users(id),
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision   INTEGER NOT NULL DEFAULT 1,
    n_total    INTEGER NOT NULL,
    n_invalid  INTEGER NOT NULL,
    data       TEXT NOT NULL
);
CREATE TABLE reports (
    id         TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    owner_id   INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    language   TEXT NOT NULL,
    digest     TEXT NOT NULL,
    html       TEXT NOT NULL,
    archive    TEXT NOT NULL
);
CREATE TABLE profiles (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    revision   INTEGER NOT NULL DEFAULT 1,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE monitors (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    config        TEXT NOT NULL,
    revision      INTEGER NOT NULL DEFAULT 1,
    limits_rev    INTEGER NOT NULL DEFAULT 0,
    ocap_rev      INTEGER NOT NULL DEFAULT 1,
    active        INTEGER NOT NULL DEFAULT 1,
    created_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE monitor_limits (
    id         INTEGER PRIMARY KEY,
    monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    revision   INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    reason     TEXT NOT NULL,
    source     TEXT NOT NULL,
    data       TEXT NOT NULL,
    UNIQUE (monitor_id, revision)
);
CREATE TABLE monitor_points (
    id             INTEGER PRIMARY KEY,
    monitor_id     INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    limits_rev     INTEGER NOT NULL,
    taken_at       TEXT NOT NULL,
    entered_at     TEXT NOT NULL,
    entered_by     TEXT NOT NULL,
    label          TEXT NOT NULL DEFAULT '',
    tags           TEXT NOT NULL DEFAULT '{}',
    vals           TEXT NOT NULL,
    loc            REAL NOT NULL,
    var            REAL,
    valid          INTEGER NOT NULL DEFAULT 1,
    invalid_reason TEXT NOT NULL DEFAULT '',
    invalid_by     TEXT NOT NULL DEFAULT '',
    invalid_at     TEXT NOT NULL DEFAULT '',
    alarms         TEXT NOT NULL DEFAULT '[]',
    warnings       TEXT NOT NULL DEFAULT '[]',
    incident_id    INTEGER,
    UNIQUE (monitor_id, seq)
);
CREATE INDEX monitor_points_time ON monitor_points(monitor_id, taken_at);
CREATE TABLE monitor_incidents (
    id           INTEGER PRIMARY KEY,
    monitor_id   INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    opened_at    TEXT NOT NULL,
    point_seq    INTEGER NOT NULL,
    rules        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',
    acked_by     TEXT NOT NULL DEFAULT '',
    acked_at     TEXT NOT NULL DEFAULT '',
    closed_at    TEXT NOT NULL DEFAULT '',
    closed_by    TEXT NOT NULL DEFAULT '',
    outcome      TEXT NOT NULL DEFAULT '',
    outcome_text TEXT NOT NULL DEFAULT ''
);
CREATE TABLE monitor_events (
    id          INTEGER PRIMARY KEY,
    monitor_id  INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    incident_id INTEGER REFERENCES monitor_incidents(id) ON DELETE CASCADE,
    at          TEXT NOT NULL,
    by_label    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    step        TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE monitor_acks (
    monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    ocap_rev   INTEGER NOT NULL,
    at         TEXT NOT NULL,
    PRIMARY KEY (monitor_id, user_id, ocap_rev)
);
CREATE TABLE audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    user_id   INTEGER,
    username  TEXT NOT NULL,
    action    TEXT NOT NULL,
    target    TEXT NOT NULL,
    detail    TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash      TEXT NOT NULL
);
CREATE TABLE studies (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    revision   INTEGER NOT NULL DEFAULT 1,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE msa_systems (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    revision   INTEGER NOT NULL DEFAULT 1,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE control_plans (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE spc_people (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
;
CREATE TABLE validation_cases (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE validation_runs (
    id         INTEGER PRIMARY KEY,
    data       TEXT NOT NULL,
    digest     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
"""


MIGRATION_6_TO_7 = """
CREATE TABLE validation_cases (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE validation_runs (
    id         INTEGER PRIMARY KEY,
    data       TEXT NOT NULL,
    digest     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
)
"""


def _monitor_tables() -> str:
    start = SCHEMA.index("CREATE TABLE monitors")
    return SCHEMA[start : SCHEMA.index("CREATE TABLE audit (")]


MIGRATION_3_TO_4 = """
CREATE TABLE studies (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    revision   INTEGER NOT NULL DEFAULT 1,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
)"""

MIGRATION_5_TO_6 = """
CREATE TABLE control_plans (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE spc_people (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);"""

MIGRATION_4_TO_5 = """
CREATE TABLE msa_systems (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    revision   INTEGER NOT NULL DEFAULT 1,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
)"""

MIGRATION_1_TO_2 = """
CREATE TABLE profiles (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    revision   INTEGER NOT NULL DEFAULT 1,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
)"""


class Database:
    def __init__(self, path: str | os.PathLike = ":memory:"):
        self.path = str(path)
        memory = self.path == ":memory:"
        new_file = not memory and not Path(self.path).exists()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            if not memory:
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._migrate()
        if new_file:
            try:
                os.chmod(self.path, 0o600)  # the file holds password hashes and all data
            except OSError:
                pass

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self._conn.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    self._conn.execute(statement)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.execute("COMMIT")
        elif version in (1, 2, 3, 4, 5, 6):
            if version == 1:  # version 2 adds the customer profiles
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(MIGRATION_1_TO_2)
                self._conn.execute("PRAGMA user_version = 2")
                self._conn.execute("COMMIT")
            if version <= 2:
                self._migrate_2_to_3()
            if version <= 3:
                self._conn.execute("BEGIN IMMEDIATE")  # version 4 adds the machine performance studies
                self._conn.execute(MIGRATION_3_TO_4)
                self._conn.execute("PRAGMA user_version = 4")
                self._conn.execute("COMMIT")
            if version <= 4:
                self._conn.execute("BEGIN IMMEDIATE")  # version 5 adds the measurement systems (MSA gate)
                self._conn.execute(MIGRATION_4_TO_5)
                self._conn.execute("PRAGMA user_version = 5")
                self._conn.execute("COMMIT")
            if version <= 5:
                self._conn.execute("BEGIN IMMEDIATE")  # version 6 adds the control plans and the SPC roles of people
                for statement in MIGRATION_5_TO_6.split(";"):
                    if statement.strip():
                        self._conn.execute(statement)
                self._conn.execute("PRAGMA user_version = 6")
                self._conn.execute("COMMIT")
            self._conn.execute("BEGIN IMMEDIATE")  # version 7 adds the validation cases and runs
            for statement in MIGRATION_6_TO_7.split(";"):
                if statement.strip():
                    self._conn.execute(statement)
            self._conn.execute("PRAGMA user_version = 7")
            self._conn.execute("COMMIT")
        elif version != SCHEMA_VERSION:
            raise RuntimeError(f"database schema version {version} is not supported (expected {SCHEMA_VERSION})")

    def _migrate_2_to_3(self) -> None:
        """Version 3 adds the SPC monitors and the role 'operator'. A CHECK cannot be changed in SQLite, so the
        users table is built again. Foreign keys are off while that happens (the documented way), and checked after."""
        self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            users = SCHEMA[SCHEMA.index("CREATE TABLE users") : SCHEMA.index("CREATE TABLE sessions")].replace("CREATE TABLE users", "CREATE TABLE users_new", 1)
            self._conn.execute(users)
            self._conn.execute("INSERT INTO users_new SELECT * FROM users")
            self._conn.execute("DROP TABLE users")
            self._conn.execute("ALTER TABLE users_new RENAME TO users")
            for statement in _monitor_tables().split(";"):
                if statement.strip():
                    self._conn.execute(statement)
            problems = self._conn.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"foreign key problems after the migration: {len(problems)}")
            self._conn.execute("PRAGMA user_version = 3")
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def tx(self):
        """A transaction. Nested use joins the outer one. Everything is undone when an exception leaves it."""
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield self
                finally:
                    self._depth -= 1
                return
            self._conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
            finally:
                self._depth = 0

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple = ()):
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple = ()) -> list:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
